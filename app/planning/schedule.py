"""Critical path scheduling (brief section 15).

Standard CPM: a forward pass gives the earliest each task can start and finish,
a backward pass gives the latest it can without delaying the project, and the
difference is slack. Tasks with no slack form the critical path.

The critical path is computed across the whole project rather than per team,
because the delays that matter run between teams — technical validation holding
up operations configuration, and so on.

Dates are working days. A due date landing on a Saturday is wrong in a way a PM
notices immediately, and these dates are pushed to Trello.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.planning.dependencies import DependencyGraph

HOURS_PER_DAY = 8.0
DEFAULT_DURATION_DAYS = 1


@dataclass
class PlanTask:
    task_id: str
    title: str
    team: str = ""
    estimated_hours: float | None = None

    @property
    def duration_days(self) -> int:
        if not self.estimated_hours or self.estimated_hours <= 0:
            return DEFAULT_DURATION_DAYS
        return max(1, math.ceil(self.estimated_hours / HOURS_PER_DAY))


@dataclass
class ScheduledTask:
    task_id: str
    title: str
    team: str
    duration_days: int
    early_start_offset: int
    early_finish_offset: int
    late_start_offset: int
    slack_days: int
    start_date: date
    due_date: date

    @property
    def is_critical(self) -> bool:
        return self.slack_days == 0


@dataclass
class Schedule:
    tasks: dict[str, ScheduledTask] = field(default_factory=dict)
    project_start: date | None = None
    project_end: date | None = None
    duration_days: int = 0
    deadline_breach: str | None = None

    @property
    def critical_path(self) -> list[ScheduledTask]:
        """Critical tasks in execution order."""
        return sorted(
            (t for t in self.tasks.values() if t.is_critical),
            key=lambda t: (t.early_start_offset, t.title),
        )


def add_working_days(start: date, days: int) -> date:
    """Advance by whole working days, skipping weekends."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _next_working_day(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def compute_schedule(
    tasks: list[PlanTask],
    dependency_graph: DependencyGraph,
    project_start: date,
    deadline: date | None = None,
) -> Schedule:
    """Run CPM over the validated dependency graph."""
    by_id = {t.task_id: t for t in tasks}
    if not by_id:
        return Schedule(project_start=project_start, project_end=project_start)

    order = dependency_graph.topological_order()
    duration = {tid: by_id[tid].duration_days for tid in by_id}

    # Forward pass: a task starts once everything it depends on has finished.
    early_start: dict[str, int] = {}
    early_finish: dict[str, int] = {}
    for task_id in order:
        predecessors = dependency_graph.dependencies_of(task_id)
        early_start[task_id] = max(
            (early_finish[p] for p in predecessors if p in early_finish), default=0
        )
        early_finish[task_id] = early_start[task_id] + duration[task_id]

    project_duration = max(early_finish.values(), default=0)

    # Backward pass: the latest a task can finish without pushing the project out.
    late_finish: dict[str, int] = {}
    late_start: dict[str, int] = {}
    for task_id in reversed(order):
        successors = list(dependency_graph.graph.successors(task_id))
        late_finish[task_id] = min(
            (late_start[s] for s in successors if s in late_start), default=project_duration
        )
        late_start[task_id] = late_finish[task_id] - duration[task_id]

    start = _next_working_day(project_start)
    scheduled: dict[str, ScheduledTask] = {}
    for task_id, task in by_id.items():
        task_start = add_working_days(start, early_start[task_id])
        scheduled[task_id] = ScheduledTask(
            task_id=task_id,
            title=task.title,
            team=task.team,
            duration_days=duration[task_id],
            early_start_offset=early_start[task_id],
            early_finish_offset=early_finish[task_id],
            late_start_offset=late_start[task_id],
            slack_days=late_start[task_id] - early_start[task_id],
            start_date=task_start,
            # A one-day task starts and finishes the same day.
            due_date=add_working_days(task_start, duration[task_id] - 1),
        )

    schedule = Schedule(
        tasks=scheduled,
        project_start=start,
        project_end=add_working_days(start, max(project_duration - 1, 0)),
        duration_days=project_duration,
    )

    if deadline and schedule.project_end and schedule.project_end > deadline:
        overrun = (schedule.project_end - deadline).days
        schedule.deadline_breach = (
            f"Schedule ends {schedule.project_end.isoformat()}, "
            f"{overrun} day(s) past the SOW date of {deadline.isoformat()}."
        )
    return schedule
