"""Project endpoints: upload a SOW, inspect what was generated, approve, push."""

from __future__ import annotations

import logging
import shutil
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.graph.review import (
    ReviewError,
    apply_edit,
    regenerate_task,
    review_summary,
    set_review,
)
from app.graph.workflow import run_sow_pipeline
from app.llm.client import GeminiClient, LLMError
from app.models import (
    Assumption,
    ClaimRecord,
    LLMRequest,
    Project,
    ProjectTask,
    SOWChunk,
    SOWDocument,
    SOWSection,
    ValidationLog,
)
from app.planning.service import resolve_deadline, schedule_project
from app.schemas.enums import ProjectStatus, ReviewStatus
from app.taskmanager.base import BoardTask
from app.taskmanager.trello import TrelloAdapter, TrelloError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["projects"])

ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt"}


class ProjectSummary(BaseModel):
    id: str
    name: str
    status: str
    task_count: int
    assumption_count: int


class TaskView(BaseModel):
    id: str
    title: str
    description: str
    team: str
    priority: str
    status: str
    source_status: str
    source_section: str | None
    source_chunk_keys: list[str]
    estimated_hours: float | None
    start_date: date | None
    due_date: date | None
    grounding_score: float | None
    validation_status: str
    review_status: str
    review_note: str
    regeneration_count: int
    depends_on: list[str]
    external_ref: str


class UploadResponse(BaseModel):
    project_id: str
    parsing_status: str
    task_count: int
    warnings: list[str]
    error: str | None = None


def _next_doc_key(db: Session) -> str:
    return f"SOW-{db.query(SOWDocument).count() + 1:03d}"


@router.post("/upload", response_model=UploadResponse)
def upload_sow(file: UploadFile = File(...), db: Session = Depends(get_db)) -> UploadResponse:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"Unsupported format {suffix!r}. Use PDF, DOCX, or TXT.")

    settings = get_settings()
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    project = Project(name=Path(file.filename or "Untitled SOW").stem, status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()

    stored = upload_dir / f"{project.id}{suffix}"
    with stored.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    try:
        state = run_sow_pipeline(db, project.id, str(stored), _next_doc_key(db))
    except LLMError as exc:
        # Extraction failed before anything was persisted, so the project row is
        # empty. Leaving it behind would litter the project list with shells
        # that can never be approved or pushed.
        db.rollback()
        db.delete(project)
        db.commit()
        stored.unlink(missing_ok=True)
        raise HTTPException(502, f"Extraction failed: {exc}") from exc

    if error := state.get("error"):
        project.status = ProjectStatus.PARSING_FAILED
        db.commit()
        return UploadResponse(
            project_id=project.id,
            parsing_status=state.get("parsing_status", "unknown"),
            task_count=0,
            warnings=state.get("warnings", []),
            error=error,
        )

    return UploadResponse(
        project_id=project.id,
        parsing_status=state.get("parsing_status", "unknown"),
        task_count=state.get("task_count", 0),
        warnings=state.get("warnings", []),
    )


@router.get("", response_model=list[ProjectSummary])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectSummary]:
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return [
        ProjectSummary(
            id=p.id,
            name=p.name,
            status=p.status,
            task_count=db.query(ProjectTask).filter(ProjectTask.project_id == p.id).count(),
            assumption_count=db.query(Assumption).filter(Assumption.project_id == p.id).count(),
        )
        for p in projects
    ]


def _task_view(db: Session, task: ProjectTask) -> TaskView:
    section = (
        db.get(SOWSection, task.source_sow_section_id) if task.source_sow_section_id else None
    )
    return TaskView(
        id=task.id,
        title=task.title,
        description=task.description,
        team=task.team,
        priority=task.priority,
        status=task.status,
        source_status=task.source_status,
        source_section=section.title if section else None,
        source_chunk_keys=[k for k in task.source_chunk_keys.split(",") if k],
        estimated_hours=task.estimated_hours,
        start_date=task.start_date,
        due_date=task.due_date,
        grounding_score=task.grounding_score,
        validation_status=task.validation_status,
        review_status=task.review_status,
        review_note=task.review_note,
        regeneration_count=task.regeneration_count,
        depends_on=[d.title for d in task.depends_on],
        external_ref=task.external_ref,
    )


@router.get("/{project_id}/tasks", response_model=list[TaskView])
def list_tasks(project_id: str, db: Session = Depends(get_db)) -> list[TaskView]:
    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project_id).all()
    return [_task_view(db, t) for t in tasks]


@router.get("/{project_id}/assumptions")
def list_assumptions(project_id: str, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(Assumption).filter(Assumption.project_id == project_id).all()
    return [
        {
            "assumption_id": a.assumption_key,
            "category": a.category,
            "value": a.value,
            "reason": a.reason,
            "confidence": a.confidence,
            "status": a.status,
        }
        for a in rows
    ]


@router.get("/{project_id}/evidence/{chunk_key}")
def get_evidence(project_id: str, chunk_key: str, db: Session = Depends(get_db)) -> dict:
    """Resolve a citation back to the exact SOW text it points at."""
    chunk = db.query(SOWChunk).filter(SOWChunk.chunk_key == chunk_key).first()
    if not chunk:
        raise HTTPException(404, f"No evidence chunk {chunk_key!r}")
    section = db.get(SOWSection, chunk.section_id)
    return {
        "chunk_key": chunk.chunk_key,
        "text": chunk.text,
        "page": chunk.page,
        "section": section.title if section else None,
    }


@router.get("/{project_id}/timeline")
def get_timeline(project_id: str, db: Session = Depends(get_db)) -> dict:
    """Recompute the schedule and report the critical path.

    Recomputed rather than read back so an edited estimate or dependency is
    reflected without re-running extraction.
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project_id).all()
    existing_starts = [t.start_date for t in tasks if t.start_date]
    start = min(existing_starts) if existing_starts else (project.start_date or date.today())  # noqa: DTZ011
    deadline = resolve_deadline(db, project_id, None)

    schedule, warnings = schedule_project(db, project_id, start, deadline)
    return {
        "project_start": schedule.project_start,
        "project_end": schedule.project_end,
        "duration_working_days": schedule.duration_days,
        "deadline": deadline,
        "deadline_breach": schedule.deadline_breach,
        "warnings": warnings,
        "critical_path": [
            {
                "task_id": t.task_id,
                "title": t.title,
                "team": t.team,
                "start_date": t.start_date,
                "due_date": t.due_date,
                "duration_days": t.duration_days,
            }
            for t in schedule.critical_path
        ],
        "slack": [
            {"task_id": t.task_id, "title": t.title, "slack_days": t.slack_days}
            for t in sorted(schedule.tasks.values(), key=lambda x: -x.slack_days)
            if t.slack_days > 0
        ],
    }


@router.get("/{project_id}/grounding")
def get_grounding(project_id: str, db: Session = Depends(get_db)) -> dict:
    """Grounding dashboard data (brief section 36).

    Reports the score by team and every claim the SOW did not establish, so a
    failure can be inspected rather than just counted.
    """
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")

    claims = db.query(ClaimRecord).filter(ClaimRecord.project_id == project_id).all()
    tasks = {t.id: t for t in db.query(ProjectTask).filter(ProjectTask.project_id == project_id)}

    supported = sum(1 for c in claims if c.verdict == "supported")
    by_team: dict[str, dict[str, int]] = {}
    for claim in claims:
        team = tasks[claim.task_id].team if claim.task_id in tasks else "unknown"
        bucket = by_team.setdefault(team, {"supported": 0, "total": 0})
        bucket["total"] += 1
        bucket["supported"] += claim.verdict == "supported"

    stages = db.query(ValidationLog).filter(ValidationLog.project_id == project_id).all()
    return {
        "overall_score": supported / len(claims) if claims else None,
        "total_claims": len(claims),
        "supported_claims": supported,
        "by_team": {
            team: {
                "score": b["supported"] / b["total"] if b["total"] else None,
                "claims": b["total"],
            }
            for team, b in sorted(by_team.items())
        },
        "task_status_counts": {
            status: sum(1 for t in tasks.values() if t.validation_status == status)
            for status in ("accept", "review", "reject")
        },
        "failures": [
            {
                "claim": c.text,
                "verdict": c.verdict,
                "reasoning": c.reasoning,
                "task": tasks[c.task_id].title if c.task_id in tasks else "",
                "team": tasks[c.task_id].team if c.task_id in tasks else "",
                "is_quantitative": c.is_quantitative,
            }
            for c in claims
            if c.verdict != "supported"
        ],
        "validation_stages": [
            {"stage": s.stage, "passed": s.passed, "detail": s.detail} for s in stages
        ],
    }


class TaskEdit(BaseModel):
    title: str | None = None
    description: str | None = None
    team: str | None = None
    priority: str | None = None
    estimated_hours: float | None = None


class RejectRequest(BaseModel):
    reason: str = ""
    regenerate: bool = True


def _get_task(db: Session, project_id: str, task_id: str) -> ProjectTask:
    task = db.get(ProjectTask, task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(404, "Task not found in this project")
    return task


@router.get("/{project_id}/review")
def get_review_state(project_id: str, db: Session = Depends(get_db)) -> dict:
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    return review_summary(db, project_id)


@router.post("/{project_id}/tasks/{task_id}/approve")
def approve_task(project_id: str, task_id: str, db: Session = Depends(get_db)) -> dict:
    task = set_review(db, _get_task(db, project_id, task_id), ReviewStatus.APPROVED)
    return {"task_id": task.id, "review_status": task.review_status}


@router.patch("/{project_id}/tasks/{task_id}")
def edit_task(
    project_id: str, task_id: str, edit: TaskEdit, db: Session = Depends(get_db)
) -> TaskView:
    task = apply_edit(db, _get_task(db, project_id, task_id), **edit.model_dump())
    return _task_view(db, task)


@router.post("/{project_id}/tasks/{task_id}/reject")
def reject_task(
    project_id: str, task_id: str, body: RejectRequest, db: Session = Depends(get_db)
) -> TaskView:
    """Reject a task and, by default, regenerate that task alone.

    Regeneration is scoped to the one item so the rest of the plan — including
    everything the PM already approved — is left untouched (brief section 23).
    """
    task = _get_task(db, project_id, task_id)
    set_review(db, task, ReviewStatus.REJECTED, body.reason)
    if not body.regenerate:
        return _task_view(db, task)

    try:
        task = regenerate_task(GeminiClient(), db, task, body.reason)
    except ReviewError as exc:
        raise HTTPException(409, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, f"Regeneration failed: {exc}") from exc
    return _task_view(db, task)


@router.get("/{project_id}/audit")
def get_audit(project_id: str, db: Session = Depends(get_db)) -> dict:
    """Every LLM exchange and validation outcome for this project (brief §22, §37)."""
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")

    requests = (
        db.query(LLMRequest)
        .filter(LLMRequest.project_id == project_id)
        .order_by(LLMRequest.created_at)
        .all()
    )
    stages = db.query(ValidationLog).filter(ValidationLog.project_id == project_id).all()
    return {
        "llm_requests": [
            {
                "request_id": r.id,
                "graph_node": r.graph_node,
                "model": r.model,
                "prompt_version": r.prompt_version,
                "input_hash": r.input_hash[:12],
                "latency_ms": r.latency_ms,
                "timestamp": r.created_at,
                "output_chars": len(r.output or ""),
            }
            for r in requests
        ],
        "total_requests": len(requests),
        "validation_stages": [
            {
                "stage": s.stage,
                "passed": s.passed,
                "detail": s.detail,
                "grounding_score": s.grounding_score,
                "validator_version": s.validator_version,
            }
            for s in stages
        ],
    }


@router.post("/{project_id}/approve")
def approve_project(project_id: str, db: Session = Depends(get_db)) -> dict:
    """PM approval gate. Nothing reaches a task manager before this (brief §23)."""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    project.status = ProjectStatus.APPROVED
    db.commit()
    return {"project_id": project.id, "status": project.status}


@router.post("/{project_id}/push")
def push_to_task_manager(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if project.status != ProjectStatus.APPROVED:
        raise HTTPException(409, "Project must be approved by the PM before it can be pushed.")

    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project_id).all()
    if not tasks:
        raise HTTPException(400, "Project has no tasks to push.")

    # Pushing a task the PM rejected would put work the team was told not to do
    # onto the board, which defeats the review entirely.
    rejected = [t for t in tasks if t.review_status == ReviewStatus.REJECTED]
    if rejected:
        raise HTTPException(
            409,
            f"{len(rejected)} task(s) are still rejected. Regenerate, edit, or "
            "remove them before pushing.",
        )
    tasks = [t for t in tasks if t.review_status != ReviewStatus.REJECTED]

    board_tasks = []
    for task in tasks:
        section = (
            db.get(SOWSection, task.source_sow_section_id) if task.source_sow_section_id else None
        )
        board_tasks.append(
            BoardTask(
                task_id=task.id,
                title=task.title,
                description=task.description,
                team=task.team,
                priority=task.priority,
                status=task.status,
                due_date=task.due_date,
                source_status=task.source_status,
                validation_status=task.validation_status,
                source_section=section.title if section else "",
                source_chunk_keys=[k for k in task.source_chunk_keys.split(",") if k],
                grounding_score=task.grounding_score,
                depends_on_titles=[d.title for d in task.depends_on],
            )
        )

    try:
        adapter = TrelloAdapter()
        result = adapter.push_project(project.name, board_tasks)
    except TrelloError as exc:
        raise HTTPException(502, f"Trello push failed: {exc}") from exc

    for task in tasks:
        if external_id := result.created.get(task.id):
            task.external_ref = external_id
    project.status = ProjectStatus.SYNCED
    db.commit()

    return {
        "board_url": result.board_url,
        "cards_created": len(result.created),
        "status": project.status,
    }
