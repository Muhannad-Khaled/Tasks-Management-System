"""Project endpoints: upload a SOW, inspect what was generated, approve, push."""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, date, datetime
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
    ClarificationQuestion,
    LLMRequest,
    Person,
    PersonRole,
    Project,
    ProjectDetail,
    ProjectMilestone,
    ProjectRequirement,
    ProjectRole,
    ProjectTask,
    ProjectTestCase,
    SOWChunk,
    SOWDocument,
    SOWSection,
    UserStory,
    ValidationLog,
)
from app.planning.milestones import calibrate, calibration_summary, check_milestones
from app.planning.service import resolve_deadline, schedule_project
from app.schemas.enums import ProjectStatus, ReviewStatus
from app.schemas.roles import TEAM_ROLES
from app.taskmanager.cards import _artifacts_by_requirement, _board_task
from app.taskmanager.drift import open_drift
from app.taskmanager.sync import PUSHABLE, refresh_status, sync_tasks
from app.taskmanager.trello import TrelloAdapter, TrelloError
from app.taskmanager.watch import check_project
from app.validation.pipeline import validate_staffing

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["projects"])
# Separate prefix: the directory belongs to the company, not to any one
# project, and hanging it under /projects/{id} would imply otherwise.
people_router = APIRouter(prefix="/people", tags=["people"])

ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt"}


class ProjectSummary(BaseModel):
    id: str
    name: str
    status: str
    task_count: int
    assumption_count: int
    board_url: str = ""
    board_checked_at: datetime | None = None


class DependencyView(BaseModel):
    """One arrow, and what it rests on.

    source_status is empty for arrows drawn before provenance was recorded.
    Rendered as unknown rather than assumed to be anything.
    """

    title: str
    source_status: str
    source_chunk_keys: list[str]
    rationale: str


class TaskView(BaseModel):
    id: str
    title: str
    description: str
    team: str
    priority: str
    status: str
    assignee_role: str
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
    depends_on: list[DependencyView]
    external_ref: str
    board_dirty: bool


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
            board_url=p.board_url,
            board_checked_at=p.board_checked_at,
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
        assignee_role=task.assignee_role,
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
        depends_on=[
            DependencyView(
                title=link.upstream.title,
                source_status=link.source_status,
                source_chunk_keys=[k for k in link.source_chunk_keys.split(",") if k],
                rationale=link.rationale,
            )
            for link in task.blockers
        ],
        external_ref=task.external_ref,
        board_dirty=task.board_dirty,
    )


@router.get("/{project_id}/tasks", response_model=list[TaskView])
def list_tasks(project_id: str, db: Session = Depends(get_db)) -> list[TaskView]:
    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project_id).all()
    return [_task_view(db, t) for t in tasks]


class PersonIn(BaseModel):
    name: str
    # What they can be put on. Empty is fine — a person with no roles simply
    # never gets staffed automatically, which is better than a wrong guess.
    roles: list[str] = []


class RoleHolder(BaseModel):
    # None clears it: a role with nobody on it is a normal state, and saying so
    # has to be as easy as saying who.
    person_id: str | None = None


@people_router.get("/roles")
def known_roles() -> list[dict]:
    """Every role the platform recognises, so a person can be marked for one.

    Served rather than duplicated in the UI: these are the same titles the
    extraction normalises project roles to, and two lists that could disagree
    would silently stop automatic staffing from ever matching.
    """
    return [{"title": r.title, "team": str(r.team)} for r in TEAM_ROLES]


@router.get("/{project_id}/board-members")
def board_members(project_id: str, db: Session = Depends(get_db)) -> list[dict]:
    """The Trello accounts this board will accept an assignment for.

    Read live rather than stored. Trello refuses idMembers for anybody who is
    not on the board, so an offline copy of this list would let the PM pick
    somebody the push then rejects — and the failure would arrive minutes
    later, attached to a card, with no obvious cause.
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if not project.board_id:
        raise HTTPException(409, "This project has no board yet.")

    try:
        adapter = TrelloAdapter()
        try:
            return adapter.board_members(project.board_id)
        finally:
            adapter.close()
    except TrelloError as exc:
        raise HTTPException(502, f"Could not read the board: {exc}") from exc


@people_router.get("")
def list_people(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(Person).order_by(Person.active.desc(), Person.name).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "active": p.active,
            "roles": p.role_titles,
            "trello_member_id": p.trello_member_id,
        }
        for p in rows
    ]


@people_router.post("")
def add_person(body: PersonIn, db: Session = Depends(get_db)) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "A person needs a name.")

    existing = db.query(Person).filter(Person.name == name).first()
    if existing:
        # Re-adding somebody who left is how they come back, rather than a
        # duplicate row that splits their history in two.
        if existing.active:
            raise HTTPException(409, f"{name} is already in the directory.")
        existing.active = True
        _set_capabilities(db, existing, body.roles)
        db.commit()
        return {"id": existing.id, "name": existing.name, "active": True}

    person = Person(name=name)
    db.add(person)
    db.flush()
    _set_capabilities(db, person, body.roles)
    db.commit()
    return {
        "id": person.id,
        "name": person.name,
        "active": person.active,
        "roles": person.role_titles,
    }


def _set_capabilities(db: Session, person: Person, titles: list[str]) -> None:
    """Replace what a person can do with exactly what was asked for.

    Unknown titles are dropped rather than stored: a role the roster does not
    know can never match a project role, so keeping it would show the PM a
    capability that does nothing.
    """
    known = {r.title for r in TEAM_ROLES}
    wanted = {t for t in titles if t in known}
    db.query(PersonRole).filter(PersonRole.person_id == person.id).delete()
    for title in sorted(wanted):
        db.add(PersonRole(person_id=person.id, role_title=title))


@people_router.patch("/{person_id}")
def set_person_roles(person_id: str, body: PersonIn, db: Session = Depends(get_db)) -> dict:
    """Change what somebody can be put on.

    Existing projects are left alone. Staffing runs once, when a plan is built,
    so that a plan the PM has already reviewed does not quietly restaff itself
    underneath them.
    """
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    _set_capabilities(db, person, body.roles)
    db.commit()
    return {"id": person.id, "name": person.name, "roles": person.role_titles}


class TrelloLink(BaseModel):
    # "" unlinks. The field is required rather than defaulted so that clearing
    # a link is something the caller asked for and not something a missing key
    # did quietly.
    member_id: str


@people_router.patch("/{person_id}/trello")
def link_person_to_trello(
    person_id: str, body: TrelloLink, db: Session = Depends(get_db)
) -> dict:
    """Say which Trello account is this person.

    Cards already on the board are marked out of date, because the name on
    them is now capable of being an assignment and is not one yet.
    """
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")

    member_id = body.member_id.strip()
    if member_id and member_id != person.trello_member_id:
        clash = (
            db.query(Person)
            .filter(Person.trello_member_id == member_id, Person.id != person.id)
            .first()
        )
        if clash:
            # One account, one person. Two directory entries sharing a Trello
            # login would assign both of them the same cards and neither of
            # them would be wrong on the board.
            raise HTTPException(409, f"That Trello account is already {clash.name}.")

    if member_id != person.trello_member_id:
        person.trello_member_id = member_id
        _restaff_cards_of(db, person)
    db.commit()
    return {"id": person.id, "name": person.name, "trello_member_id": person.trello_member_id}


def _restaff_cards_of(db: Session, person: Person) -> int:
    """Mark every card this person owns as out of date with the plan."""
    marked = 0
    for role in db.query(ProjectRole).filter(ProjectRole.person_id == person.id):
        for task in db.query(ProjectTask).filter(
            ProjectTask.project_id == role.project_id,
            ProjectTask.assignee_role == role.role_title,
            ProjectTask.external_ref != "",
        ):
            task.board_dirty = True
            marked += 1
    return marked


@people_router.delete("/{person_id}")
def deactivate_person(person_id: str, db: Session = Depends(get_db)) -> dict:
    """Retire somebody without erasing what they were assigned.

    Deleting the row would strip their name off plans that already ran, which
    is a record of who did what and not something to lose on a staff change.
    """
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    person.active = False
    db.commit()
    return {"id": person.id, "name": person.name, "active": False}


@router.patch("/{project_id}/roles/{role_id}")
def set_role_holder(
    project_id: str, role_id: str, body: RoleHolder, db: Session = Depends(get_db)
) -> dict:
    """Say who is filling one role on this project."""
    role = db.get(ProjectRole, role_id)
    if role is None or role.project_id != project_id:
        raise HTTPException(404, "Role not found in this project")

    if body.person_id:
        person = db.get(Person, body.person_id)
        if person is None:
            raise HTTPException(404, "Person not found")
        role.person_id = person.id
    else:
        role.person_id = None

    # Every card naming this role now says something different.
    for task in db.query(ProjectTask).filter(
        ProjectTask.project_id == project_id,
        ProjectTask.assignee_role == role.role_title,
        ProjectTask.external_ref != "",
    ):
        task.board_dirty = True

    db.commit()
    return {
        "role_id": role.id,
        "role_title": role.role_title,
        "person": role.person.name if role.person else None,
    }


@router.get("/{project_id}/roles")
def list_roles(project_id: str, db: Session = Depends(get_db)) -> list[dict]:
    # Ordered, because the UI draws one picker per row: an unordered query
    # lets the list reshuffle between renders while somebody is using it.
    rows = (
        db.query(ProjectRole)
        .filter(ProjectRole.project_id == project_id)
        .order_by(ProjectRole.team, ProjectRole.role_title)
        .all()
    )
    return [
        {
            "role_id": r.id,
            "team": r.team,
            "role_title": r.role_title,
            "responsibility": r.responsibility,
            "headcount": r.headcount,
            "person_id": r.person_id,
            "person": r.person.name if r.person else None,
            "source_status": r.source_status,
            "source_chunk_keys": [k for k in r.source_chunk_keys.split(",") if k],
        }
        for r in rows
    ]


@router.get("/{project_id}/questions")
def list_questions(project_id: str, db: Session = Depends(get_db)) -> dict:
    """Open questions, grouped by who has to answer them."""
    rows = (
        db.query(ClarificationQuestion)
        .filter(ClarificationQuestion.project_id == project_id)
        .all()
    )
    grouped: dict[str, list[dict]] = {"merchant": [], "offer": [], "project": []}
    for q in rows:
        grouped.setdefault(q.scope, []).append(
            {
                "question_id": q.question_key,
                "category": q.category,
                "field_key": q.field_key,
                "team": q.team,
                "text": q.text,
                "working_assumption": q.working_assumption,
                "status": q.status,
                "answer": q.answer,
            }
        )
    return {
        "counts": {scope: len(items) for scope, items in grouped.items()},
        "total": len(rows),
        "by_scope": grouped,
    }


@router.get("/{project_id}/structure")
def project_structure(project_id: str, db: Session = Depends(get_db)) -> dict:
    """The whole plan as one tree: project -> team -> what each team owns.

    Served in a single response because the shape is the point — a caller
    reassembling it from six endpoints would have to re-derive the grouping
    rules the taxonomy already encodes.
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    requirements = (
        db.query(ProjectRequirement).filter(ProjectRequirement.project_id == project_id).all()
    )
    stories = db.query(UserStory).filter(UserStory.project_id == project_id).all()
    stories_by_requirement: dict[str, list[UserStory]] = {}
    for story in stories:
        stories_by_requirement.setdefault(story.requirement_id, []).append(story)

    details = db.query(ProjectDetail).filter(ProjectDetail.project_id == project_id).all()
    roles = db.query(ProjectRole).filter(ProjectRole.project_id == project_id).all()
    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project_id).all()

    tasks_by_requirement: dict[str, list[ProjectTask]] = {}
    unlinked: list[ProjectTask] = []
    for task in tasks:
        if task.requirement_id:
            tasks_by_requirement.setdefault(task.requirement_id, []).append(task)
        else:
            unlinked.append(task)
    questions = (
        db.query(ClarificationQuestion)
        .filter(ClarificationQuestion.project_id == project_id)
        .all()
    )

    teams: dict[str, dict] = {}
    for team in ("commercial", "technical", "operations"):
        team_details: dict[str, list[dict]] = {}
        for detail in (d for d in details if d.team == team):
            team_details.setdefault(detail.category, []).append(
                {
                    "name": detail.name,
                    "description": detail.description,
                    "source_status": detail.source_status,
                    "source_chunk_keys": [k for k in detail.source_chunk_keys.split(",") if k],
                }
            )
        teams[team] = {
            "roles": [
                {
                    "title": r.role_title,
                    "responsibility": r.responsibility,
                    "source_status": r.source_status,
                    # Named by a human, so it carries no source_status: the SOW
                    # said the role was needed, not who would fill it.
                    "person": r.person.name if r.person else None,
                }
                for r in roles
                if r.team == team
            ],
            "task_count": sum(1 for t in tasks if t.team == team),
            "details": team_details,
            "requirements": [
                {
                    "requirement_id": r.requirement_key,
                    "title": r.title,
                    "source_status": r.source_status,
                    "source_chunk_keys": [k for k in r.source_chunk_keys.split(",") if k],
                    # Which task actually delivers this, and where it ended up.
                    # A requirement can be owned by one team and delivered by
                    # another — an SLA is a commercial promise kept by
                    # operations — and without this the reader goes looking for
                    # a card in the wrong board column and concludes it is
                    # missing.
                    "tasks": [
                        {
                            "title": t.title,
                            "team": t.team,
                            "assignee_role": t.assignee_role,
                            "due_date": t.due_date,
                            "validation_status": t.validation_status,
                            "delivered_by_another_team": t.team != r.team,
                        }
                        for t in tasks_by_requirement.get(r.id, [])
                    ],
                    "user_stories": [
                        {
                            "story_id": story.story_key,
                            "sentence": story.sentence,
                            "source_status": story.source_status,
                            "acceptance_criteria": [
                                {"criterion_id": c.criterion_key, "text": c.text}
                                for c in story.acceptance_criteria
                            ],
                            "test_cases": [
                                {
                                    "case_id": case.case_key,
                                    "title": case.title,
                                    "preconditions": case.preconditions,
                                    "action": case.action,
                                    "expected_result": case.expected_result,
                                    "kind": case.kind,
                                    "rests_on_assumption": case.rests_on_assumption,
                                    "assumed_fields": [
                                        f for f in case.assumed_fields.split(",") if f
                                    ],
                                }
                                for case in story.test_cases
                            ],
                        }
                        for story in stories_by_requirement.get(r.id, [])
                    ],
                }
                for r in requirements
                if r.team == team
            ],
            "open_questions": sum(1 for q in questions if q.team == team and q.status == "open"),
            # Work sitting on this team's board that no requirement explains.
            "unlinked_tasks": [
                {"title": t.title, "assignee_role": t.assignee_role}
                for t in unlinked
                if t.team == team
            ],
        }

    cases = db.query(ProjectTestCase).filter(ProjectTestCase.project_id == project_id).all()
    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "merchant_name": project.merchant_name,
            "status": project.status,
        },
        "totals": {
            "requirements": len(requirements),
            "tasks": len(tasks),
            "user_stories": len(stories),
            "test_cases": len(cases),
            "test_cases_resting_on_assumptions": sum(1 for c in cases if c.rests_on_assumption),
            "detail_items": len(details),
            "open_questions": sum(1 for q in questions if q.status == "open"),
        },
        "teams": teams,
    }


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


@router.get("/{project_id}/evidence")
def get_all_evidence(project_id: str, db: Session = Depends(get_db)) -> dict:
    """Every chunk this project's tasks cite, in one response.

    The task list showed evidence per task, which meant one request per cited
    chunk. Streamlit renders the body of a collapsed expander too, so those ran
    on every page render whether or not anyone looked at them.
    """
    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project_id).all()
    keys = {k for t in tasks for k in t.source_chunk_keys.split(",") if k}
    # Dependencies cite too, and not always the same chunks their tasks do. An
    # arrow whose evidence went unfetched would render as a bare claim.
    keys |= {k for t in tasks for link in t.blockers for k in link.source_chunk_keys.split(",") if k}
    if not keys:
        return {}
    chunks = db.query(SOWChunk).filter(SOWChunk.chunk_key.in_(keys)).all()
    sections = {
        s.id: s.title
        for s in db.query(SOWSection).filter(
            SOWSection.id.in_({c.section_id for c in chunks})
        )
    }
    return {
        c.chunk_key: {
            "chunk_key": c.chunk_key,
            "text": c.text,
            "page": c.page,
            "section": sections.get(c.section_id),
        }
        for c in chunks
    }


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


def _milestone_view(db: Session, project_id: str) -> list[dict]:
    """Each SOW milestone against where the plan actually lands."""
    rows = db.query(ProjectMilestone).filter(ProjectMilestone.project_id == project_id).all()
    checks = check_milestones(
        [(m.name, m.target_date, [(t.title, t.due_date) for t in m.tasks]) for m in rows]
    )
    return [
        {
            "name": c.name,
            "target_date": c.target_date,
            "projected_date": c.projected_date,
            "variance_days": c.variance_days,
            "status": c.status,
            "detail": c.detail,
            "tasks": c.task_titles,
        }
        for c in checks
    ]


def _calibration_view(db: Session, project_id: str, project_start: date) -> dict:
    rows = db.query(ProjectMilestone).filter(ProjectMilestone.project_id == project_id).all()
    checks = check_milestones(
        [(m.name, m.target_date, [(t.title, t.due_date) for t in m.tasks]) for m in rows]
    )
    segments = calibrate(project_start, checks)
    summary = calibration_summary(segments)
    summary["by_step"] = [
        {
            "name": s.name,
            "after": s.after,
            "allowed_days": s.allowed_days,
            "planned_days": s.planned_days,
            "unaccounted_days": s.unaccounted_days,
            "coverage": s.coverage,
            "detail": s.detail,
        }
        for s in segments
    ]
    return summary


def _staffing_clashes(db: Session, tasks: list[ProjectTask]) -> list[str]:
    """One person needed in two places at once, as sentences the PM can act on."""
    result = validate_staffing(db, tasks)
    return [] if result.passed else result.detail.split("; ")


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
        # Recomputed rather than read from the last validation run: the PM
        # can restaff a role at any time, and a stale clash list would be
        # worse than none.
        "staffing_clashes": _staffing_clashes(db, tasks),
        # How much of this schedule anybody actually estimated. Without it the
        # dates read as though the work had been sized, when most of them may
        # be the one-day default standing in for a missing estimate.
        "estimate_coverage": schedule.estimate_coverage,
        # The dates the SOW committed to along the way, not just the final one.
        "milestones": _milestone_view(db, project_id),
        # What the SOW's own windows say about the estimates inside them. The
        # only calibration a SOW contains: it never states effort, but it does
        # state the dates both parties agreed the work must fit between.
        "calibration": _calibration_view(db, project_id, schedule.project_start or start),
        "assumed_durations": [
            {"task_id": t.task_id, "title": t.title, "team": t.team}
            for t in schedule.assumed_durations
        ],
        "critical_path": [
            {
                "task_id": t.task_id,
                "title": t.title,
                "team": t.team,
                "start_date": t.start_date,
                "due_date": t.due_date,
                "duration_days": t.duration_days,
                "duration_is_assumed": t.duration_is_assumed,
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


@router.get("/{project_id}/drift")
def get_drift(project_id: str, db: Session = Depends(get_db)) -> list[dict]:
    """Differences between the board and the plan that nobody has settled."""
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    return [
        {
            "task_id": row.task_id,
            "kind": row.kind,
            "detail": row.detail,
            "first_seen": row.first_seen_at,
            "notified": row.notified_at is not None,
        }
        for row in open_drift(db, project_id)
    ]


@router.post("/{project_id}/check-board")
def check_board(project_id: str, db: Session = Depends(get_db)) -> dict:
    """Read the board now rather than waiting for the next scheduled look."""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if not project.board_id:
        raise HTTPException(409, "This project has no board yet.")

    try:
        adapter = TrelloAdapter()
        try:
            fresh = check_project(db, project, adapter)
        finally:
            adapter.close()
    except TrelloError as exc:
        raise HTTPException(502, f"Could not read the board: {exc}") from exc

    return {"new": fresh, "open": len(open_drift(db, project_id))}


@router.post("/{project_id}/tasks/{task_id}/restore-card")
def restore_card(project_id: str, task_id: str, db: Session = Depends(get_db)) -> dict:
    """Un-archive a card somebody put away, most likely by mistake.

    Deliberately not automatic. Archiving is how a board gets tidied, and a
    platform that silently undid it would be fighting the team; but when it was
    an accident, recreating the card would lose its comments and its ticked
    items, so restoring has to be one action rather than a rebuild.
    """
    project = db.get(Project, project_id)
    if not project or not project.board_id:
        raise HTTPException(404, "Project or board not found")
    task = _get_task(db, project_id, task_id)
    if not task.external_ref:
        raise HTTPException(409, "This task has no card to restore.")

    try:
        adapter = TrelloAdapter()
        try:
            restored = adapter.restore_card(project.board_id, task.external_ref)
        finally:
            adapter.close()
    except TrelloError as exc:
        raise HTTPException(502, f"Could not restore the card: {exc}") from exc

    if not restored:
        # Gone rather than archived. Say so plainly instead of reporting a
        # success that put nothing back.
        raise HTTPException(
            409,
            "That card was deleted, not archived, so it cannot be restored. "
            "Push the task to create a new card — it will not carry the old "
            "card's comments or ticked items.",
        )

    for row in open_drift(db, project_id):
        if row.task_id == task.id and row.kind == "archived":
            row.resolved_at = datetime.now(UTC)
    db.commit()
    return {"task_id": task.id, "restored": True}


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


def _sync(db: Session, project: Project, tasks: list[ProjectTask]):
    """Build the cards for these tasks and put them on the project's board."""
    derived = _artifacts_by_requirement(db, project.id)
    board_tasks = {t.id: _board_task(db, t, derived) for t in tasks}
    try:
        return sync_tasks(db, project, tasks, board_tasks, TrelloAdapter())
    except TrelloError as exc:
        raise HTTPException(502, f"Trello push failed: {exc}") from exc


@router.post("/{project_id}/tasks/{task_id}/push")
def push_task(project_id: str, task_id: str, db: Session = Depends(get_db)) -> dict:
    """Put one reviewed task on the board without waiting for the rest.

    The task's own approval is the gate. Requiring the project-wide approval as
    well would mean signing off the entire plan in order to push a single task,
    which is the opposite of reviewing one at a time.
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    task = _get_task(db, project_id, task_id)

    if task.review_status == ReviewStatus.REJECTED:
        raise HTTPException(
            409, "This task is rejected. Regenerate or edit it before putting it on the board."
        )
    if task.review_status not in PUSHABLE:
        raise HTTPException(409, "Approve this task before pushing it to the board.")

    result = _sync(db, project, [task])
    return {
        "task_id": task.id,
        "card_id": task.external_ref,
        "action": "updated" if task.id in result.updated else "created",
        "board_url": result.board_url,
        "warnings": result.warnings,
        "project_status": project.status,
    }


@router.post("/{project_id}/push")
def push_to_task_manager(project_id: str, db: Session = Depends(get_db)) -> dict:
    """Push everything reviewed that is not on the board yet, or has changed.

    Kept alongside the per-task push rather than replaced by it: a PM who has
    read the whole plan should not have to click thirty times to send it.

    This route still needs the project-wide approval, and that is what lets it
    carry tasks the PM never opened individually — approving the project is a
    sign-off on the plan as a whole.
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if project.status not in {
        ProjectStatus.APPROVED,
        ProjectStatus.PARTIALLY_SYNCED,
        ProjectStatus.SYNCED,
    }:
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

    # Cards already on the board and unchanged are left alone. Re-sending every
    # one of them costs about three Trello calls each and would overwrite
    # nothing, so a second push after a single edit stays cheap.
    outstanding = [t for t in tasks if not t.external_ref or t.board_dirty]
    if not outstanding:
        refresh_status(project)
        db.commit()
        return {
            "board_url": project.board_url,
            "cards_created": 0,
            "cards_updated": 0,
            "warnings": [],
            "status": project.status,
            "detail": "Every task is already on the board and up to date.",
        }

    result = _sync(db, project, outstanding)
    return {
        "board_url": result.board_url,
        "cards_created": len(result.created),
        "cards_updated": len(result.updated),
        "warnings": result.warnings,
        "status": project.status,
    }
