"""What a card says about a task.

Kept out of the API layer because two callers need it and neither is HTTP: the
push routes write cards from this, and the board watcher renders the same thing
to compare against what is actually on the board.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import (
    ProjectRole,
    ProjectTask,
    SOWSection,
    UserStory,
)
from app.taskmanager.base import BoardCase, BoardTask


def _artifacts_by_requirement(db: Session, project_id: str) -> dict[str, dict]:
    """Acceptance-criteria counts and assumed test fields, keyed by requirement.

    A task inherits these through the requirement it implements, which is the
    only link between a unit of work and the tests that will judge it.
    """
    stories = (
        db.query(UserStory)
        .filter(UserStory.project_id == project_id, UserStory.requirement_id.isnot(None))
        .all()
    )
    out: dict[str, dict] = {}
    for story in stories:
        entry = out.setdefault(
            story.requirement_id, {"criteria": [], "assumed_fields": set(), "cases": []}
        )
        entry["criteria"].extend(c.text for c in story.acceptance_criteria)
        for case in story.test_cases:
            assumed = [f for f in case.assumed_fields.split(",") if f]
            entry["assumed_fields"].update(assumed)
            entry["cases"].append(
                BoardCase(
                    title=case.title,
                    expected_result=case.expected_result,
                    rests_on_assumption=case.rests_on_assumption,
                    assumed_fields=assumed,
                )
            )
    return out


def _holder_of(db: Session, task: ProjectTask) -> str:
    """Who is doing this task, if anybody has been named.

    The task's own `assignee` wins when set, so one task can go to somebody
    other than whoever normally holds the role. Otherwise it follows the role,
    which means changing who holds a role updates every task at once instead of
    task by task.
    """
    if task.assignee:
        return task.assignee
    role = (
        db.query(ProjectRole)
        .filter(
            ProjectRole.project_id == task.project_id,
            ProjectRole.role_title == task.assignee_role,
        )
        .first()
    )
    return role.person.name if role and role.person else ""


def _board_task(db: Session, task: ProjectTask, derived: dict[str, dict]) -> BoardTask:
    """Everything a card says about one task.

    Shared by both push routes. While this was written inline inside the
    project-wide push, a task pushed on its own would have had to grow a second
    copy of it, and the two would have drifted at the first change.
    """
    section = (
        db.get(SOWSection, task.source_sow_section_id) if task.source_sow_section_id else None
    )
    artifacts = derived.get(task.requirement_id or "", {})
    return BoardTask(
        task_id=task.id,
        title=task.title,
        description=task.description,
        team=task.team,
        priority=task.priority,
        status=task.status,
        due_date=task.due_date,
        assignee_role=task.assignee_role,
        assignee_name=_holder_of(db, task),
        source_status=task.source_status,
        validation_status=task.validation_status,
        acceptance_criteria=artifacts.get("criteria", []),
        assumed_test_fields=sorted(artifacts.get("assumed_fields", set())),
        test_cases=artifacts.get("cases", []),
        source_section=section.title if section else "",
        source_chunk_keys=[k for k in task.source_chunk_keys.split(",") if k],
        grounding_score=task.grounding_score,
        depends_on_titles=[d.title for d in task.depends_on],
    )
