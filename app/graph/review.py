"""Human-in-the-loop review and partial regeneration (brief section 23).

The PM acts on one task at a time: approve it, edit it, or reject it. Rejecting
regenerates that task alone — the rest of the plan, its dependencies and its
schedule stay put. Regenerating everything because one item was wrong would
discard work the PM already accepted and hand them a different plan to review
from scratch.

A regenerated task is re-grounded immediately, so its score reflects the new
wording rather than the wording that was rejected.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.grounding.engine import ground_project
from app.llm.prompts import TASK_REGENERATION
from app.models import ClaimRecord, ProjectTask, SOWChunk, SOWSection
from app.schemas.enums import ReviewStatus
from app.schemas.sow import ExtractedTask

logger = logging.getLogger(__name__)

MAX_REGENERATIONS = 3


class ReviewError(RuntimeError):
    pass


def set_review(
    db: Session, task: ProjectTask, status: ReviewStatus, note: str = ""
) -> ProjectTask:
    task.review_status = str(status)
    task.review_note = note
    db.commit()
    return task


def apply_edit(db: Session, task: ProjectTask, **fields) -> ProjectTask:
    """Apply a PM edit.

    An edited task is marked as such and its grounding is cleared: the score
    described the generated wording, and keeping it against text a human
    rewrote would misreport where that text came from.
    """
    editable = {"title", "description", "team", "priority", "estimated_hours"}
    for key, value in fields.items():
        if key in editable and value is not None:
            setattr(task, key, value)
    task.review_status = str(ReviewStatus.EDITED)
    task.grounding_score = None
    task.validation_status = "pending"
    db.query(ClaimRecord).filter(ClaimRecord.task_id == task.id).delete()
    db.commit()
    return task


def _evidence_for_task(db: Session, task: ProjectTask) -> str:
    """The SOW text this task cites, plus the rest of its section for context."""
    keys = [k for k in task.source_chunk_keys.split(",") if k]
    chunks = db.query(SOWChunk).filter(SOWChunk.chunk_key.in_(keys)).all() if keys else []

    if task.source_sow_section_id:
        section_chunks = (
            db.query(SOWChunk).filter(SOWChunk.section_id == task.source_sow_section_id).all()
        )
        seen = {c.chunk_key for c in chunks}
        chunks += [c for c in section_chunks if c.chunk_key not in seen]

    if not chunks:
        return "(no evidence is linked to this task)"

    lines = []
    for chunk in chunks:
        section = db.get(SOWSection, chunk.section_id)
        location = section.title if section else ""
        lines.append(f"[{chunk.chunk_key}] ({location})\n{chunk.text}")
    return "\n\n".join(lines)


def _failed_claims_text(db: Session, task: ProjectTask) -> str:
    failures = (
        db.query(ClaimRecord)
        .filter(ClaimRecord.task_id == task.id, ClaimRecord.verdict != "supported")
        .all()
    )
    if not failures:
        return ""
    lines = ["Claims the SOW did not support:"]
    lines += [f"- {c.text} ({c.verdict}: {c.reasoning})" for c in failures]
    return "\n".join(lines)


def regenerate_task(
    client,
    db: Session,
    task: ProjectTask,
    reason: str = "",
    valid_keys: set[str] | None = None,
    retriever=None,
) -> ProjectTask:
    """Regenerate one rejected task in place, then re-ground it.

    The task keeps its id, so dependencies pointing at it stay intact and the
    schedule does not have to be rebuilt from nothing.
    """
    if task.regeneration_count >= MAX_REGENERATIONS:
        raise ReviewError(
            f"Task has already been regenerated {task.regeneration_count} times. "
            "Edit it directly or reject it permanently."
        )

    reason = reason.strip() or task.review_note.strip() or (
        "The task failed grounding: it asserts detail the SOW does not establish."
    )
    replacement = client.generate_structured(
        prompt=TASK_REGENERATION,
        schema=ExtractedTask,
        db=db,
        project_id=task.project_id,
        graph_node="task_regeneration",
        use_cache=False,  # the same input must be able to yield a different answer
        title=task.title,
        description=task.description or "(none)",
        team=task.team,
        reason=reason,
        failed_claims=_failed_claims_text(db, task),
        evidence=_evidence_for_task(db, task),
    )

    if valid_keys is None:
        valid_keys = {
            key
            for (key,) in db.query(SOWChunk.chunk_key)
            .join(SOWSection, SOWChunk.section_id == SOWSection.id)
            .all()
        }
    cited = [k for k in replacement.source_chunk_keys if k in valid_keys]

    task.title = replacement.title or task.title
    task.description = replacement.description
    task.priority = str(replacement.priority)
    task.source_status = str(replacement.source_status)
    if replacement.estimated_hours:
        task.estimated_hours = replacement.estimated_hours
    if cited:
        task.source_chunk_keys = ",".join(cited)
    task.regeneration_count += 1
    task.review_status = str(ReviewStatus.PENDING)
    task.review_note = ""
    db.commit()

    # Re-ground the replacement so its score describes the new wording.
    db.query(ClaimRecord).filter(ClaimRecord.task_id == task.id).delete()
    db.commit()
    groundings = ground_project(client, db, task.project_id, [task], valid_keys, retriever)
    if groundings:
        from app.graph.persistence import persist_grounding
        from app.models import Project

        persist_grounding(db, db.get(Project, task.project_id), groundings)
    return task


def review_summary(db: Session, project_id: str) -> dict:
    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project_id).all()
    counts = {status.value: 0 for status in ReviewStatus}
    for task in tasks:
        counts[task.review_status] = counts.get(task.review_status, 0) + 1
    return {
        "total": len(tasks),
        "counts": counts,
        "ready": counts[ReviewStatus.PENDING.value] == 0
        and counts[ReviewStatus.REJECTED.value] == 0,
    }
