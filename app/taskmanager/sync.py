"""Putting the plan on the board, a task at a time.

The PM reviews task by task and pushes each one when they are happy with it,
so the board is long-lived state rather than something a push builds from
nothing. It is created once, on the first push, and every push after that lands
on it. Everything here follows from that: the board id is remembered, a task
already on the board is updated rather than duplicated, and a partly-filled
board is a normal state, not a failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Project, ProjectTask
from app.schemas.enums import ProjectStatus, ReviewStatus
from app.taskmanager import drift
from app.taskmanager.base import (
    BoardTask,
    TaskManagerInterface,
    content_fingerprint,
)

logger = logging.getLogger(__name__)

# What the PM has signed off on, one way or the other. A pending task has not
# been looked at and a rejected one was told not to be done.
PUSHABLE = {ReviewStatus.APPROVED.value, ReviewStatus.EDITED.value}


@dataclass
class SyncResult:
    board_url: str
    created: list[str] = field(default_factory=list)  # task ids
    updated: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def ensure_board(db: Session, project: Project, adapter: TaskManagerInterface) -> tuple[str, str]:
    """The project's board, created on first use and remembered after that."""
    if project.board_id:
        return project.board_id, project.board_url

    board_id, board_url = adapter.create_board(project.name)
    project.board_id, project.board_url = board_id, board_url
    # Committed before a single card is written, deliberately. If card creation
    # fails next and the session rolls back, the board still exists in Trello —
    # and forgetting its id here would make the retry build a second one, which
    # is the exact failure this whole module was written to remove.
    db.commit()
    logger.info("Created board %s for project %s", board_id, project.id)
    return board_id, board_url


def sync_tasks(
    db: Session,
    project: Project,
    tasks: list[ProjectTask],
    board_tasks: dict[str, BoardTask],
    adapter: TaskManagerInterface,
) -> SyncResult:
    """Create the cards that are missing and refresh the ones that moved on."""
    board_id, board_url = ensure_board(db, project, adapter)
    result = SyncResult(board_url=board_url)

    to_create: list[ProjectTask] = []
    for task in tasks:
        if not task.external_ref:
            to_create.append(task)
            continue
        if adapter.update_task(board_id, task.external_ref, board_tasks[task.id]):
            _remember(task, board_tasks[task.id])
            result.updated.append(task.id)
        else:
            # Somebody deleted the card by hand, and it is not put back here.
            # Recreating it would undo their decision in the middle of an
            # operation about a different task; the PM is told instead and can
            # push this one deliberately if it should return.
            result.warnings.append(
                f"{task.title!r} is no longer on the board. It was not recreated — "
                "push it again to restore it."
            )
            task.external_ref = ""

    if to_create:
        # One call for the whole batch: the adapter reads the board's lists and
        # labels once per call, so creating cards one at a time would pay for
        # those two round trips again on every single task.
        created = adapter.push_tasks(board_id, [board_tasks[t.id] for t in to_create])
        for task in to_create:
            if external_id := created.get(task.id):
                task.external_ref = external_id
                _remember(task, board_tasks[task.id])
                result.created.append(task.id)

    result.warnings.extend(check_drift(db, project, tasks, board_tasks, adapter))
    result.warnings.extend(_dangling_dependencies(tasks))
    result.warnings.extend(_unassignable_owners(tasks, board_tasks))
    refresh_status(project)
    db.commit()
    return result


def _remember(task: ProjectTask, board_task: BoardTask) -> None:
    """Record what the board was just told, so a later edit to it shows up."""
    task.board_fingerprint = content_fingerprint(
        board_task.title, board_task.rendered_description()
    )
    task.board_dirty = False


def check_drift(
    db: Session,
    project: Project,
    tasks: list[ProjectTask],
    board_tasks: dict[str, BoardTask],
    adapter: TaskManagerInterface,
) -> list[str]:
    """Compare the board with the plan and record anything that disagrees.

    Runs on every push as well as on the timer, because the board is read back
    here anyway and a PM who is already looking at the plan is the right person
    to see it. Nothing is corrected — see app/taskmanager/drift.py.
    """
    on_board = [task for task in tasks if task.external_ref]
    if not on_board:
        return []

    snapshots = adapter.card_snapshots(project.board_id)
    found = drift.detect(on_board, board_tasks, snapshots, adapter)
    fresh = drift.reconcile(db, project.id, found)
    # Only the new ones are worth saying out loud; a difference the PM has
    # already been shown does not need repeating on every push.
    return [row.detail for row in fresh]


def _unassignable_owners(
    tasks: list[ProjectTask], board_tasks: dict[str, BoardTask]
) -> list[str]:
    """Owners the card names but cannot be assigned to.

    Silence here reads as success. The card says "Owner: Ahmed Hassan" whether
    or not Trello put him on it, so a PM who linked nobody would see a board
    that looks staffed and a team that was never notified of anything.
    """
    orphaned: dict[str, int] = {}
    for task in tasks:
        board_task = board_tasks.get(task.id)
        if board_task and board_task.assignee_name and not board_task.assignee_member_id:
            orphaned[board_task.assignee_name] = orphaned.get(board_task.assignee_name, 0) + 1
    if not orphaned:
        return []
    named = ", ".join(f"{name} ({count})" for name, count in sorted(orphaned.items()))
    return [
        (
            "Named on their cards but not assigned on Trello, having no linked "
            f"account: {named}. Link them under People on the Structure tab."
        )
    ]


def _dangling_dependencies(tasks: list[ProjectTask]) -> list[str]:
    """Tasks that reached the board ahead of what they depend on.

    Not an error. The PM reviews in whatever order suits them and a half-filled
    board is the normal state while they work. But a card whose blocker is not
    there yet reads as ready to start, so it is said out loud rather than
    quietly allowed — the same choice made for milestone variance and for
    estimates that overrun their window.
    """
    warnings = []
    for task in tasks:
        missing = sorted(d.title for d in task.depends_on if not d.external_ref)
        if missing:
            listed = ", ".join(missing[:3])
            more = f" (+{len(missing) - 3} more)" if len(missing) > 3 else ""
            warnings.append(
                f"{task.title!r} is on the board before what it depends on: {listed}{more}"
            )
    return warnings


def refresh_status(project: Project) -> str:
    """Set the project's status from what is actually on the board.

    Derived rather than tracked, so it cannot drift: a push that half-failed
    leaves the project reading partially_synced without anyone remembering to
    say so.
    """
    live = [t for t in project.tasks if t.review_status != ReviewStatus.REJECTED]
    on_board = [t for t in live if t.external_ref]
    if not on_board:
        return project.status  # nothing pushed yet; leave the review state alone
    project.status = (
        ProjectStatus.SYNCED if len(on_board) == len(live) else ProjectStatus.PARTIALLY_SYNCED
    )
    return project.status
