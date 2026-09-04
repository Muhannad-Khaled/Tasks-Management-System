"""Project endpoints: upload a SOW, inspect what was generated, approve, push."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.graph.workflow import run_sow_pipeline
from app.llm.client import LLMError
from app.models import Assumption, Project, ProjectTask, SOWChunk, SOWDocument, SOWSection
from app.schemas.enums import ProjectStatus
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
        project.status = ProjectStatus.EXTRACTING
        db.commit()
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
