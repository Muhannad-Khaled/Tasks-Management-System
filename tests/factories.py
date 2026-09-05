"""Test helpers: corpus paths and the LLM test double."""

import re
from pathlib import Path

from app.schemas.enums import SourceStatus
from app.schemas.fields import PLANNING_FIELDS
from app.schemas.gaps import FieldFinding, GapReport
from app.schemas.grounding import (
    BatchClaimSet,
    Claim,
    ClaimJudgement,
    ClaimVerdict,
    GroundingJudgement,
    TaskClaims,
)
from app.schemas.sow import (
    ExtractedAssumption,
    ExtractedRequirement,
    ExtractedTask,
    ProjectInfo,
    StructuredSOW,
)

CORPUS = Path(__file__).parent.parent / "data" / "sample_sows"


class StubLLM:
    """Stands in for GeminiClient, returning a fixed extraction.

    `citations` lets a test inject chunk keys that do not exist in the document,
    which is how the invented-citation guard is exercised.
    """

    def __init__(self, citations: list[str] | None = None):
        self.citations = citations if citations is not None else []
        self.calls: list[dict] = []

    def generate_structured(self, prompt, schema, **kwargs):
        self.calls.append({"prompt": prompt.name, **kwargs})
        if schema is GapReport:
            return self._gap_report()
        if schema is BatchClaimSet:
            return self._batch_claims(kwargs.get("tasks", ""))
        if schema is GroundingJudgement:
            return self._judgement(kwargs.get("claims", ""))
        return self._extraction()

    def _batch_claims(self, rendered_tasks: str) -> BatchClaimSet:
        """Two claims per task: one qualitative, one quantitative."""
        refs = re.findall(r"^\[(T\d+)\]", rendered_tasks, re.MULTILINE)
        return BatchClaimSet(
            tasks=[
                TaskClaims(
                    task_ref=ref,
                    claims=[
                        Claim(claim_id="C-001", text=f"{ref} states a requirement"),
                        Claim(
                            claim_id="C-002",
                            text=f"{ref} takes a specific duration",
                            is_quantitative=True,
                        ),
                    ],
                )
                for ref in refs
            ]
        )

    def _judgement(self, rendered_claims: str) -> GroundingJudgement:
        """Support the qualitative claim, fail the quantitative one.

        This mirrors the real failure mode the grounding layer exists to catch:
        a task correct about the work and unsupported about the numbers.
        """
        judgements = []
        for line in rendered_claims.splitlines():
            claim_id = line.strip().lstrip("- ").split(":")[0].strip()
            if not claim_id:
                continue
            if claim_id.endswith("C-001"):
                judgements.append(
                    ClaimJudgement(
                        claim_id=claim_id,
                        verdict=ClaimVerdict.SUPPORTED,
                        supporting_chunk_keys=self.citations[:1],
                        reasoning="The SOW states this requirement.",
                    )
                )
            else:
                judgements.append(
                    ClaimJudgement(
                        claim_id=claim_id,
                        verdict=ClaimVerdict.UNSUPPORTED,
                        reasoning="The SOW gives no duration for this work.",
                    )
                )
        return GroundingJudgement(judgements=judgements)

    def _gap_report(self) -> GapReport:
        """Report the first field as stated and the rest as gaps."""
        findings = []
        for index, spec in enumerate(PLANNING_FIELDS):
            if index == 0 and self.citations:
                findings.append(
                    FieldFinding(
                        field_key=spec.key,
                        status=SourceStatus.EXPLICIT,
                        value="5",
                        evidence_chunk_keys=self.citations,
                    )
                )
            else:
                findings.append(
                    FieldFinding(
                        field_key=spec.key,
                        status=SourceStatus.ASSUMED,
                        value="",
                        note=f"The SOW does not specify the {spec.label}.",
                    )
                )
        return GapReport(findings=findings)

    def _extraction(self) -> StructuredSOW:
        return StructuredSOW(
            project_info=ProjectInfo(
                project_name="Loyalty Program - CairoMart",
                merchant_name="CairoMart Retail Group",
            ),
            requirements=[
                ExtractedRequirement(
                    requirement_id="REQ-001",
                    team="commercial",
                    title="Countersign the commercial contract",
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                )
            ],
            tasks=[
                ExtractedTask(
                    task_id="T-001",
                    team="commercial",
                    title="Finalize merchant contract",
                    description="Countersign before integration starts.",
                    priority="high",
                    estimated_hours=8,
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                ),
                ExtractedTask(
                    task_id="T-002",
                    team="technical",
                    title="Develop POS API integration",
                    description="REST integration for points accrual.",
                    priority="high",
                    estimated_hours=60,
                    depends_on=["T-001"],
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                ),
                ExtractedTask(
                    task_id="T-003",
                    team="operations",
                    title="Configure merchant and offers",
                    priority="medium",
                    estimated_hours=16,
                    depends_on=["T-002"],
                    source_status="inferred",
                    source_chunk_keys=self.citations,
                ),
            ],
            assumptions=[
                ExtractedAssumption(
                    assumption_id="A-001",
                    category="TEAM_SIZE",
                    value="5 technical team members",
                    reason="SOW does not specify team size",
                    confidence=0.8,
                )
            ],
        )
