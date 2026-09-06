"""The periodic look at every board, and the message it sends.

One entry point, called from two places: a background task inside the API and a
script the user can run whenever they like. Writing the logic twice would mean
the scheduled check and the one run by hand could disagree about what counts as
a difference, which is precisely the confusion this feature exists to remove.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import Project
from app.notifications.discord import notify_board_drift
from app.taskmanager import drift
from app.taskmanager.base import TaskManagerInterface
from app.taskmanager.cards import _artifacts_by_requirement, _board_task
from app.taskmanager.trello import TrelloAdapter, TrelloError

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 900  # fifteen minutes


@dataclass
class WatchReport:
    checked: int = 0
    new_differences: int = 0
    notified: bool = False
    errors: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)


def check_project(
    db: Session, project: Project, adapter: TaskManagerInterface
) -> list[str]:
    """Read one project's board and record whatever no longer matches the plan."""
    tasks = [t for t in project.tasks if t.external_ref]
    if not tasks:
        return []

    derived = _artifacts_by_requirement(db, project.id)
    board_tasks = {t.id: _board_task(db, t, derived) for t in tasks}
    snapshots = adapter.card_snapshots(project.board_id)
    found = drift.detect(tasks, board_tasks, snapshots, adapter)
    fresh = drift.reconcile(db, project.id, found)

    messages = [f"{project.name}: {row.detail}" for row in fresh]
    if messages and notify_board_drift(messages):
        drift.mark_notified(fresh)
    db.commit()
    return messages


def check_all_boards(db: Session | None = None) -> WatchReport:
    """Every project that has a board, checked once."""
    owned = db is None
    db = db or SessionLocal()
    report = WatchReport()
    try:
        projects = db.query(Project).filter(Project.board_id != "").all()
        if not projects:
            return report

        try:
            adapter = TrelloAdapter()
        except TrelloError as exc:
            # No credentials is a normal state for a fresh checkout, not a
            # failure worth waking anybody for.
            report.errors.append(str(exc))
            return report

        try:
            for project in projects:
                report.checked += 1
                try:
                    messages = check_project(db, project, adapter)
                except TrelloError as exc:
                    # One unreachable board must not stop the others being
                    # checked; the boards are independent of each other.
                    logger.warning("Could not read board for %s: %s", project.name, exc)
                    report.errors.append(f"{project.name}: {exc}")
                    db.rollback()
                    continue
                report.details.extend(messages)
                report.new_differences += len(messages)
        finally:
            adapter.close()
    finally:
        if owned:
            db.close()

    report.notified = bool(report.details)
    return report


async def watch_boards(interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> None:
    """Run the check forever, on a timer.

    Every failure is swallowed and logged. A watcher that dies on the first bad
    response stops watching silently, which is worse than the drift it was
    added to catch.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            report = await asyncio.to_thread(check_all_boards)
        except Exception:
            logger.exception("Board check failed; will try again next cycle")
            continue
        if report.new_differences:
            logger.info(
                "Board check: %d project(s), %d new difference(s)",
                report.checked,
                report.new_differences,
            )


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "WatchReport",
    "check_all_boards",
    "check_project",
    "watch_boards",
]
