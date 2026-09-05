"""Claim-level grounding (brief sections 16-18).

For each generated task:

    split into atomic claims
        -> gather evidence (its citations, plus retrieval for what it did not cite)
        -> judge each claim against that evidence
        -> score = supported / total
        -> accept, review, or reject

A task is not judged as a whole. It can be right about the work and wrong about
the duration, and averaging that into one verdict hides exactly the detail a PM
needs to catch.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.graph.persistence import normalize_citation
from app.llm.prompts import CLAIM_EXTRACTION, CLAIM_VERIFICATION
from app.models import SOWChunk
from app.rag.index import RetrievedChunk, search
from app.schemas.grounding import (
    BatchClaimSet,
    Claim,
    ClaimJudgement,
    ClaimVerdict,
    GroundingJudgement,
)

logger = logging.getLogger(__name__)

# Brief section 18. Tuned against the synthetic corpus; ACCEPT is deliberately
# strict because a task that reaches Trello carries implied authority.
ACCEPT_THRESHOLD = 0.9
REVIEW_THRESHOLD = 0.7

RETRIEVAL_LIMIT = 3

# (project_id, query, limit) -> chunks
Retriever = Callable[[str, str, int], list[RetrievedChunk]]


class GroundingStatus:
    ACCEPT = "accept"
    REVIEW = "review"
    REJECT = "reject"


@dataclass
class TaskGrounding:
    task_id: str
    title: str
    claims: list[Claim] = field(default_factory=list)
    judgements: list[ClaimJudgement] = field(default_factory=list)

    @property
    def score(self) -> float:
        """Supported claims over total (brief section 18)."""
        if not self.judgements:
            # Nothing checkable was asserted, so there is nothing unsupported.
            return 1.0
        supported = sum(1 for j in self.judgements if j.verdict == ClaimVerdict.SUPPORTED)
        return supported / len(self.judgements)

    @property
    def status(self) -> str:
        if any(j.verdict == ClaimVerdict.CONTRADICTED for j in self.judgements):
            # One claim the SOW actively contradicts outweighs a good average.
            return GroundingStatus.REJECT
        if self.score >= ACCEPT_THRESHOLD:
            return GroundingStatus.ACCEPT
        if self.score >= REVIEW_THRESHOLD:
            return GroundingStatus.REVIEW
        return GroundingStatus.REJECT

    @property
    def failures(self) -> list[ClaimJudgement]:
        return [j for j in self.judgements if j.verdict != ClaimVerdict.SUPPORTED]

    def claim_text(self, claim_id: str) -> str:
        return next((c.text for c in self.claims if c.claim_id == claim_id), claim_id)


def render_evidence(chunks: list[tuple[str, str, str, int | None]]) -> str:
    """Render (key, section, text, page) tuples the way the judge prompt expects."""
    lines = []
    for key, section, text, page in chunks:
        location = f"{section}" + (f", page {page}" if page else "")
        lines.append(f"[{key}] ({location})\n{text}")
    return "\n\n".join(lines) if lines else "(no evidence found in the SOW)"


def gather_evidence(
    db: Session,
    project_id: str,
    cited_keys: list[str],
    claims: list[Claim],
    retriever: Retriever | None = search,
) -> list[tuple[str, str, str, int | None]]:
    """The task's own citations, plus retrieval for claims it did not cite.

    Retrieval matters because an uncited claim is not the same as an
    unsupported one: the SOW may establish it in a chunk the generation step
    simply failed to reference, and judging without looking would manufacture
    false failures.

    `retriever` is injectable so tests can skip embedding, which otherwise
    dominates their runtime.
    """
    evidence: dict[str, tuple[str, str, str, int | None]] = {}

    for key in cited_keys:
        chunk = db.query(SOWChunk).filter(SOWChunk.chunk_key == key).first()
        if chunk:
            section = chunk.section.title if chunk.section else ""
            evidence[key] = (key, section, chunk.text, chunk.page)

    if retriever is not None:
        for claim in claims:
            for hit in retriever(project_id, claim.text, RETRIEVAL_LIMIT):
                if hit.chunk_key and hit.chunk_key not in evidence:
                    evidence[hit.chunk_key] = (hit.chunk_key, hit.section, hit.text, hit.page)

    return list(evidence.values())


def extract_claims_batch(client, db: Session, project_id: str, tasks: list) -> dict[str, list[Claim]]:
    """Extract claims for every task in a single call.

    One call per task would spend a whole day's free-tier budget (20 requests)
    grounding a single project, so the batch is the only workable shape.
    """
    if not tasks:
        return {}

    refs = {f"T{i}": task for i, task in enumerate(tasks, start=1)}
    rendered = "\n\n".join(
        f"[{ref}] {task.title}\n{task.description or '(no description)'}"
        for ref, task in refs.items()
    )
    batch = client.generate_structured(
        prompt=CLAIM_EXTRACTION,
        schema=BatchClaimSet,
        db=db,
        project_id=project_id,
        graph_node="claim_extraction",
        tasks=rendered,
    )

    by_task: dict[str, list[Claim]] = {task.id: [] for task in tasks}
    for entry in batch.tasks:
        task = refs.get(entry.task_ref.strip().upper())
        if task is None:
            logger.warning("Claim extraction returned unknown task ref %r", entry.task_ref)
            continue
        by_task[task.id] = entry.claims
    return by_task


def verify_claims(
    client,
    db: Session,
    project_id: str,
    claims: list[Claim],
    evidence: list[tuple[str, str, str, int | None]],
    valid_keys: set[str],
) -> list[ClaimJudgement]:
    rendered_claims = "\n".join(f"- {c.claim_id}: {c.text}" for c in claims)
    judgement = client.generate_structured(
        prompt=CLAIM_VERIFICATION,
        schema=GroundingJudgement,
        db=db,
        project_id=project_id,
        graph_node="claim_verification",
        evidence=render_evidence(evidence),
        claims=rendered_claims,
    )

    by_id = {j.claim_id: j for j in judgement.judgements}
    results: list[ClaimJudgement] = []
    for claim in claims:
        found = by_id.get(claim.claim_id)
        if found is None:
            # An unjudged claim is unverified, which is not the same as fine.
            results.append(
                ClaimJudgement(
                    claim_id=claim.claim_id,
                    verdict=ClaimVerdict.UNSUPPORTED,
                    reasoning="The verifier did not return a judgement for this claim.",
                )
            )
            continue
        found.supporting_chunk_keys = [
            key
            for key in (normalize_citation(k) for k in found.supporting_chunk_keys)
            if key in valid_keys
        ]
        # A claim called supported by evidence that does not exist is not supported.
        if found.verdict == ClaimVerdict.SUPPORTED and not found.supporting_chunk_keys:
            found.verdict = ClaimVerdict.UNSUPPORTED
            found.reasoning = (
                "Marked supported but cited no evidence that exists in the SOW. "
                + found.reasoning
            )
        results.append(found)
    return results


def ground_project(
    client,
    db: Session,
    project_id: str,
    tasks: list,
    valid_keys: set[str],
    retriever: Retriever | None = search,
) -> list[TaskGrounding]:
    """Ground every task in the project using two LLM calls in total.

    Claims are extracted for all tasks at once, then judged against the union of
    their evidence in one verification call. Claim ids are namespaced per task
    first, so a duplicate id from the model cannot cross-assign a verdict from
    one task to another.
    """
    if not tasks:
        return []

    claims_by_task = extract_claims_batch(client, db, project_id, tasks)

    namespaced: list[Claim] = []
    owner: dict[str, str] = {}
    groundings: list[TaskGrounding] = []
    all_evidence: dict[str, tuple[str, str, str, int | None]] = {}

    for index, task in enumerate(tasks, start=1):
        task_claims = []
        for claim in claims_by_task.get(task.id, []):
            unique = Claim(
                claim_id=f"T{index}-{claim.claim_id}",
                text=claim.text,
                is_quantitative=claim.is_quantitative,
            )
            task_claims.append(unique)
            namespaced.append(unique)
            owner[unique.claim_id] = task.id
        groundings.append(
            TaskGrounding(task_id=task.id, title=task.title, claims=task_claims)
        )

        cited = [k for k in task.source_chunk_keys.split(",") if k]
        for item in gather_evidence(db, project_id, cited, task_claims, retriever):
            all_evidence[item[0]] = item

    if not namespaced:
        return groundings

    judgements = verify_claims(
        client, db, project_id, namespaced, list(all_evidence.values()), valid_keys
    )
    by_task: dict[str, list[ClaimJudgement]] = {}
    for judgement in judgements:
        by_task.setdefault(owner[judgement.claim_id], []).append(judgement)
    for grounding in groundings:
        grounding.judgements = by_task.get(grounding.task_id, [])
    return groundings


def project_grounding_score(groundings: list[TaskGrounding]) -> float:
    """Score across every claim in the project, not the mean of task scores.

    Averaging task scores would let a task asserting one claim outweigh a task
    asserting ten.
    """
    total = sum(len(g.judgements) for g in groundings)
    if not total:
        return 1.0
    supported = sum(
        1 for g in groundings for j in g.judgements if j.verdict == ClaimVerdict.SUPPORTED
    )
    return supported / total
