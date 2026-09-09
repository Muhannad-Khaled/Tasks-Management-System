"""Telling the PM when the board and the plan stop agreeing.

The board is not a read-only copy of the plan. People move cards, rewrite them,
and delete them, and every one of those is a real decision by someone on the
team. None of it is undone here: this module only notices and reports, so the
two records can disagree loudly instead of quietly.

Differences are stored rather than recomputed on the spot, for one reason. A
check that runs on a timer and reports the same unresolved thing every cycle is
a check people mute, and the alert after that one goes unread too. Each
difference is reported once, and again only if it is settled and then recurs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import BoardDrift, ProjectTask
from app.taskmanager.base import BoardTask, CardSnapshot, TaskManagerInterface

logger = logging.getLogger(__name__)

MOVED = "moved"
EDITED = "edited"
ARCHIVED = "archived"
DELETED = "deleted"
REASSIGNED = "reassigned"


@dataclass(frozen=True)
class Difference:
    """One disagreement between a card and the task it came from."""

    task_id: str
    kind: str
    detail: str


def detect(
    tasks: list[ProjectTask],
    board_tasks: dict[str, BoardTask],
    snapshots: dict[str, CardSnapshot],
    adapter: TaskManagerInterface,
) -> list[Difference]:
    """Compare what is on the board with what the plan last put there.

    An empty `snapshots` means the board could not be read at all, which is not
    the same as every card having been deleted — so nothing is reported rather
    than everything.
    """
    if not snapshots:
        return []

    found: list[Difference] = []
    for task in tasks:
        if not task.external_ref:
            continue
        card = snapshots.get(task.external_ref)

        if card is None:
            found.append(
                Difference(
                    task.id,
                    DELETED,
                    f"{task.title!r} was deleted from the board and cannot be "
                    "brought back. Pushing the task makes a new card, which will "
                    "not have the old one's comments or ticked items.",
                )
            )
            continue

        if card.archived:
            # Reported on its own: an archived card is not in any list and has
            # no meaningful position, so calling it moved as well would be
            # noise on top of the thing that actually matters.
            found.append(
                Difference(
                    task.id,
                    ARCHIVED,
                    f"{task.title!r} was archived on the board. It can be restored "
                    "exactly as it was, with its comments and ticked items intact.",
                )
            )
            continue

        planned = adapter.expected_location(board_tasks[task.id])
        if planned and card.location and card.location != planned:
            found.append(
                Difference(
                    task.id,
                    MOVED,
                    f"{task.title!r} sits in {card.location!r}, but the plan has it "
                    f"under {task.team} ({planned!r}).",
                )
            )

        # Checked separately from the fingerprint. Folding members into the
        # digest would report that the card's text changed when only its owner
        # did, and the two need different answers: text is replaced by pushing
        # again, an owner is a decision somebody made about who does the work.
        #
        # Only when the plan knows an account. Where it does not, the platform
        # deliberately leaves members alone on push, so whoever is on the card
        # was put there by a person and is not a disagreement with anything.
        wanted = board_tasks[task.id].assignee_member_id
        if wanted and wanted not in card.members:
            owner = board_tasks[task.id].assignee_name or "the planned owner"
            found.append(
                Difference(
                    task.id,
                    REASSIGNED,
                    f"{task.title!r} is no longer assigned to {owner} on the board. "
                    "Pushing the task again puts them back.",
                )
            )

        # Only meaningful once something has actually been written from here.
        # A blank fingerprint is a card this platform has never described.
        if task.board_fingerprint and card.fingerprint() != task.board_fingerprint:
            found.append(
                Difference(
                    task.id,
                    EDITED,
                    f"{task.title!r} was edited on the board. The card now reads "
                    f"{card.title!r}. Pushing the task again will replace it.",
                )
            )
    return found


def reconcile(db: Session, project_id: str, found: list[Difference]) -> list[BoardDrift]:
    """Bring the stored differences in line with what this check saw.

    Returns only the rows that are new, which are the only ones worth telling
    anybody about. Anything the check no longer finds is marked settled, so if
    it comes back later it is news again.
    """
    open_rows = {
        (row.task_id, row.kind): row
        for row in db.query(BoardDrift)
        .filter(BoardDrift.project_id == project_id, BoardDrift.resolved_at.is_(None))
        .all()
    }
    seen = {(d.task_id, d.kind) for d in found}
    now = datetime.now(UTC)

    for key, row in open_rows.items():
        if key not in seen:
            row.resolved_at = now

    fresh: list[BoardDrift] = []
    for difference in found:
        key = (difference.task_id, difference.kind)
        if existing := open_rows.get(key):
            # Same problem, possibly reworded. Keep the newer wording without
            # making it look like something new happened.
            existing.detail = difference.detail
            continue
        row = BoardDrift(
            project_id=project_id,
            task_id=difference.task_id,
            kind=difference.kind,
            detail=difference.detail,
        )
        db.add(row)
        fresh.append(row)

    db.flush()
    return fresh


def open_drift(db: Session, project_id: str) -> list[BoardDrift]:
    return (
        db.query(BoardDrift)
        .filter(BoardDrift.project_id == project_id, BoardDrift.resolved_at.is_(None))
        .order_by(BoardDrift.first_seen_at)
        .all()
    )


def mark_notified(rows: list[BoardDrift]) -> None:
    now = datetime.now(UTC)
    for row in rows:
        row.notified_at = now
