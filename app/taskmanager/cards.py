"""What a card says about a task.

Kept out of the API layer because two callers need it and neither is HTTP: the
push routes write cards from this, and the board watcher renders the same thing
to compare against what is actually on the board.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import (
    Person,
    ProjectRole,
    ProjectTask,
    SOWSection,
    UserStory,
)
from app.schemas.enums import Team
from app.taskmanager.base import BoardBlocker, BoardCase, BoardTask


def _empty_artifacts() -> dict:
    return {"criteria": [], "assumed_fields": set(), "cases": [], "story": None}


def _collect(entry: dict, story: UserStory) -> None:
    entry["criteria"].extend(c.rendered() for c in story.acceptance_criteria)
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


def artifacts_for_tasks(db: Session, project_id: str) -> dict[str, dict]:
    """What each task's card should carry, keyed by task id.

    Engineer stories are attached to a task and go to that card alone. Anything
    else is reached through the requirement, which is the only link a client
    story has to a unit of work.

    That distinction is the whole point of this function. Inheriting everything
    through the requirement meant three technical tasks under one requirement
    were handed the same criteria and the same tests, so a card for writing a
    design document arrived carrying the acceptance criteria of the accrual
    engine — plausible, specific, and about different work.
    """
    stories = db.query(UserStory).filter(UserStory.project_id == project_id).all()

    by_requirement: dict[str, dict] = {}
    out: dict[str, dict] = {}
    for story in stories:
        if story.task_id:
            entry = out.setdefault(story.task_id, _empty_artifacts())
            entry["story"] = story
            _collect(entry, story)
        elif story.requirement_id:
            _collect(by_requirement.setdefault(story.requirement_id, _empty_artifacts()), story)

    for task in db.query(ProjectTask).filter(ProjectTask.project_id == project_id):
        # A task with its own engineer story keeps it. Falling back to the
        # requirement here would put the duplication straight back.
        if task.id in out or not task.requirement_id:
            continue
        inherited = by_requirement.get(task.requirement_id)
        if inherited is not None:
            out[task.id] = inherited
    return out


def _holder_of(db: Session, task: ProjectTask) -> str:
    """Who is doing this task, if anybody has been named.

    The task's own `assignee` wins when set, so one task can go to somebody
    other than whoever normally holds the role. Otherwise it follows the role,
    which means changing who holds a role updates every task at once instead of
    task by task.
    """
    person = _person_on(db, task)
    if person is not None:
        return person.name
    return task.assignee


def _person_on(db: Session, task: ProjectTask) -> Person | None:
    """The directory entry behind a task, when there is one.

    `assignee` is free text a PM typed, so it names somebody without being
    anybody: it can carry a name onto a card but never a Trello account. Only
    a role held by a directory person resolves to a row here.
    """
    if task.assignee:
        return None
    role = (
        db.query(ProjectRole)
        .filter(
            ProjectRole.project_id == task.project_id,
            ProjectRole.role_title == task.assignee_role,
        )
        .first()
    )
    return role.person if role and role.person else None


def _board_task(db: Session, task: ProjectTask, derived: dict[str, dict]) -> BoardTask:
    """Everything a card says about one task.

    Shared by both push routes. While this was written inline inside the
    project-wide push, a task pushed on its own would have had to grow a second
    copy of it, and the two would have drifted at the first change.
    """
    section = (
        db.get(SOWSection, task.source_sow_section_id) if task.source_sow_section_id else None
    )
    artifacts = derived.get(task.id, {})
    story = artifacts.get("story")
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
        assignee_member_id=(holder.trello_member_id if (holder := _person_on(db, task)) else ""),
        source_status=task.source_status,
        validation_status=task.validation_status,
        acceptance_criteria=artifacts.get("criteria", []),
        assumed_test_fields=sorted(artifacts.get("assumed_fields", set())),
        test_cases=artifacts.get("cases", []),
        story_sentence=story.sentence if story is not None else "",
        technical_notes=story.technical_notes if story is not None else "",
        # Only a task that was meant to have build detail can be missing it.
        # Saying "the SOW specified nothing" on a contract-signing card would
        # be true and pointless.
        expects_technical_notes=task.team == str(Team.TECHNICAL),
        source_section=section.title if section else "",
        source_chunk_keys=[k for k in task.source_chunk_keys.split(",") if k],
        grounding_score=task.grounding_score,
        blocked_by=[
            BoardBlocker(title=link.upstream.title, source_status=link.source_status)
            for link in task.blockers
        ],
    )
