"""Bridge between persisted tasks and the planning engine.

Reads the project's tasks out of the database, schedules them, and writes the
resulting dates and slack back. Kept separate from the pure planning modules so
those stay testable without a database.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import Assumption, Project, ProjectTask
from app.planning.dependencies import DependencyGraph, build_dependency_graph
from app.planning.schedule import PlanTask, Schedule, compute_schedule

logger = logging.getLogger(__name__)


def parse_date(value: str | None) -> date | None:
    """Best-effort date parsing for values coming from the SOW or an assumption."""
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%B %d, %Y", "%d %b %Y"):
        try:
            # SOW dates are calendar dates and carry no timezone.
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def load_plan_tasks(db: Session, project_id: str) -> tuple[list[PlanTask], DependencyGraph]:
    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project_id).all()
    plan_tasks = [
        PlanTask(
            task_id=t.id,
            title=t.title,
            team=t.team,
            estimated_hours=t.estimated_hours,
        )
        for t in tasks
    ]
    dependencies = {t.id: [d.id for d in t.depends_on] for t in tasks}
    return plan_tasks, build_dependency_graph(dependencies)


def resolve_deadline(db: Session, project_id: str, go_live: str | None = None) -> date | None:
    """The date the schedule must respect.

    Prefers the date the SOW states, then the one stored on the project, then an
    assumed schedule date. Without this fallback chain a schedule recomputed
    after extraction has nothing to be checked against.
    """
    if deadline := parse_date(go_live):
        return deadline
    project = db.get(Project, project_id)
    if project is not None and project.go_live_date:
        return project.go_live_date
    row = (
        db.query(Assumption)
        .filter(Assumption.project_id == project_id, Assumption.category == "SCHEDULE")
        .first()
    )
    return parse_date(row.value) if row else None


def schedule_project(
    db: Session,
    project_id: str,
    project_start: date,
    deadline: date | None = None,
) -> tuple[Schedule, list[str]]:
    """Schedule the project and persist the dates. Returns the schedule and warnings."""
    plan_tasks, dependency_graph = load_plan_tasks(db, project_id)
    warnings = [f"dependency {issue.kind}: {issue.detail}" for issue in dependency_graph.issues]

    schedule = compute_schedule(plan_tasks, dependency_graph, project_start, deadline)

    for task in db.query(ProjectTask).filter(ProjectTask.project_id == project_id):
        if scheduled := schedule.tasks.get(task.id):
            task.start_date = scheduled.start_date
            task.due_date = scheduled.due_date
    db.commit()

    if schedule.deadline_breach:
        warnings.append(schedule.deadline_breach)
    return schedule, warnings
