"""Persist a parsed SOW and its extraction into PostgreSQL.

Chunk keys are the join between the document and everything generated from it,
so they are written first and every task records the keys it cites.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.ingestion.parser import ParsedDocument
from app.models import (
    Assumption,
    ClaimRecord,
    Project,
    ProjectTask,
    SOWChunk,
    SOWDocument,
    SOWSection,
)
from app.schemas.sow import StructuredSOW

_CHUNK_KEY = re.compile(r"[A-Za-z0-9_.-]*SOW[A-Za-z0-9_.-]*-S\d+-C\d+", re.IGNORECASE)


def normalize_citation(raw: str) -> str:
    """Pull the canonical chunk key out of whatever the model emitted.

    Models decorate citations — a trailing page number, surrounding brackets,
    stray whitespace. Comparing those raw against the document's keys silently
    discards every citation and leaves tasks with no evidence, which is the one
    failure this system cannot afford to have happen quietly.
    """
    match = _CHUNK_KEY.search(raw.strip().strip("[]()"))
    return match.group(0) if match else raw.strip()


def persist_document(
    db: Session, project: Project, doc: ParsedDocument, stored_path: str, parsing_status: str
) -> tuple[SOWDocument, dict[str, SOWChunk]]:
    """Write the document tree; return it with a chunk_key -> chunk index."""
    sow_doc = SOWDocument(
        project_id=project.id,
        doc_key=doc.doc_key,
        filename=doc.filename,
        file_type=doc.file_type,
        stored_path=stored_path,
        parsing_status=parsing_status,
    )
    db.add(sow_doc)
    db.flush()

    chunks_by_key: dict[str, SOWChunk] = {}
    for section in doc.sections:
        db_section = SOWSection(
            sow_document_id=sow_doc.id,
            section_index=section.section_index,
            title=section.title,
            page_start=section.page_start,
            page_end=section.page_end,
        )
        db.add(db_section)
        db.flush()
        for chunk in section.chunks:
            key = doc.chunk_key(section, chunk)
            db_chunk = SOWChunk(
                section_id=db_section.id,
                chunk_index=chunk.chunk_index,
                chunk_key=key,
                text=chunk.text,
                page=chunk.page,
            )
            db.add(db_chunk)
            chunks_by_key[key] = db_chunk
    db.flush()
    return sow_doc, chunks_by_key


def persist_extraction(
    db: Session,
    project: Project,
    extraction: StructuredSOW,
    chunks_by_key: dict[str, SOWChunk],
) -> list[ProjectTask]:
    """Write tasks and assumptions, resolving citations and dependencies.

    Citations the model invented (keys that are not in the document) are dropped
    rather than stored, so a task can never point at evidence that does not exist.
    """
    tasks_by_extracted_id: dict[str, ProjectTask] = {}
    for extracted in extraction.tasks:
        cited = [normalize_citation(k) for k in extracted.source_chunk_keys]
        valid_keys = [k for k in cited if k in chunks_by_key]
        section_id = None
        if valid_keys:
            section_id = chunks_by_key[valid_keys[0]].section_id
        task = ProjectTask(
            project_id=project.id,
            title=extracted.title,
            description=extracted.description,
            team=str(extracted.team),
            priority=str(extracted.priority),
            estimated_hours=extracted.estimated_hours,
            source_status=str(extracted.source_status),
            source_sow_section_id=section_id,
            source_chunk_keys=",".join(valid_keys),
        )
        db.add(task)
        db.flush()
        tasks_by_extracted_id[extracted.task_id] = task

    for extracted in extraction.tasks:
        task = tasks_by_extracted_id[extracted.task_id]
        for dependency_id in extracted.depends_on:
            upstream = tasks_by_extracted_id.get(dependency_id)
            # Unknown ids are dropped and self-dependencies would deadlock the graph.
            if upstream is not None and upstream.id != task.id:
                task.depends_on.append(upstream)

    db.commit()
    return list(tasks_by_extracted_id.values())


def persist_assumptions(db: Session, project: Project, assumptions: list[dict]) -> list[Assumption]:
    """Write the assumption engine's findings.

    These come from the explicit field audit rather than from whatever the
    extraction pass chose to mention, so they replace any already recorded for
    the project instead of accumulating alongside them.
    """
    db.query(Assumption).filter(Assumption.project_id == project.id).delete()
    rows = [
        Assumption(
            project_id=project.id,
            assumption_key=a["assumption_key"],
            category=a["category"],
            value=a["value"],
            reason=a["reason"],
            confidence=a["confidence"],
        )
        for a in assumptions
    ]
    db.add_all(rows)
    db.flush()
    return rows


def persist_grounding(db: Session, project: Project, groundings: list) -> None:
    """Write per-claim verdicts and roll the score up onto each task.

    Only the claims of the tasks being persisted are replaced. Clearing the
    whole project would mean regenerating one task silently destroyed the
    evidence behind every other task's score, leaving scores on screen with
    nothing to justify them.
    """
    task_ids = [g.task_id for g in groundings]
    if task_ids:
        db.query(ClaimRecord).filter(
            ClaimRecord.project_id == project.id, ClaimRecord.task_id.in_(task_ids)
        ).delete(synchronize_session=False)
    for grounding in groundings:
        by_id = {j.claim_id: j for j in grounding.judgements}
        for claim in grounding.claims:
            judgement = by_id.get(claim.claim_id)
            db.add(
                ClaimRecord(
                    project_id=project.id,
                    task_id=grounding.task_id,
                    claim_key=claim.claim_id,
                    text=claim.text,
                    is_quantitative=claim.is_quantitative,
                    verdict=str(judgement.verdict) if judgement else "unsupported",
                    reasoning=judgement.reasoning if judgement else "",
                    supporting_chunk_keys=(
                        ",".join(judgement.supporting_chunk_keys) if judgement else ""
                    ),
                )
            )
        task = db.get(ProjectTask, grounding.task_id)
        if task is not None:
            task.grounding_score = grounding.score
            task.validation_status = grounding.status
    db.commit()


def invented_citations(extraction: StructuredSOW, chunks_by_key: dict[str, SOWChunk]) -> list[str]:
    """Citation keys the model produced that do not exist in the document."""
    cited = {normalize_citation(k) for t in extraction.tasks for k in t.source_chunk_keys}
    cited |= {normalize_citation(k) for r in extraction.requirements for k in r.source_chunk_keys}
    return sorted(cited - set(chunks_by_key))
