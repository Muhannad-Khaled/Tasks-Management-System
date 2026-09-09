"""Checking the computed schedule against the dates the SOW committed to.

A SOW usually carries more than a go-live date: a table of intermediate
milestones with owners, each a date the two parties agreed on. The scheduler
does not use them, and it should not — a milestone is a ceiling, not an
appointment. Turning "integration complete by 10 November" into a constraint
would push work later for no reason other than to match a date.

What it must do is *compare*. And the comparison is interesting in both
directions:

- Finishing a milestone late is a breach of what was agreed, and the plan is
  claiming something the SOW does not allow.
- Finishing it far early is rarely good news. It usually means the effort
  behind it was never estimated, so the plan is fast on paper and nowhere else.
  A schedule that lands six weeks before every milestone is not ahead; it is
  unfinished.

Without this, timeline validation asks only "do we beat the final deadline?",
answers yes, and passes a plan that ignores every date in between.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# How far ahead of an agreed date a plan can land before the gap is telling us
# something about the plan rather than about the schedule. Two working weeks:
# wide enough to allow genuine slack in the SOW, narrow enough to catch a
# milestone that only looks achievable because nobody sized the work.
MATERIALLY_EARLY_DAYS = 10


class MilestoneStatus:
    LATE = "late"
    ON_TRACK = "on_track"
    EARLY = "early"
    UNSCHEDULED = "unscheduled"


@dataclass
class MilestoneCheck:
    name: str
    target_date: date | None
    projected_date: date | None
    task_titles: list[str] = field(default_factory=list)

    @property
    def variance_days(self) -> int | None:
        """Days between the plan and the promise. Positive means late."""
        if self.target_date is None or self.projected_date is None:
            return None
        return (self.projected_date - self.target_date).days

    @property
    def status(self) -> str:
        variance = self.variance_days
        if variance is None:
            return MilestoneStatus.UNSCHEDULED
        if variance > 0:
            return MilestoneStatus.LATE
        if variance < -MATERIALLY_EARLY_DAYS:
            return MilestoneStatus.EARLY
        return MilestoneStatus.ON_TRACK

    @property
    def detail(self) -> str:
        if self.projected_date is None:
            return f"{self.name}: no scheduled task covers this milestone"
        if self.target_date is None:
            return f"{self.name}: the SOW gives no date to check against"
        # Both dates are known here, so compute the gap directly rather than
        # going through the optional property.
        variance = (self.projected_date - self.target_date).days
        plan = self.projected_date.isoformat()
        promised = self.target_date.isoformat()
        if variance > 0:
            return f"{self.name}: plan ends {plan}, {variance} day(s) past the agreed {promised}"
        if variance < 0:
            return (
                f"{self.name}: plan ends {plan}, {abs(variance)} day(s) before the "
                f"agreed {promised}"
            )
        return f"{self.name}: plan ends {plan}, exactly as agreed"


def check_milestones(
    milestones: list[tuple[str, date | None, list[tuple[str, date | None]]]],
) -> list[MilestoneCheck]:
    """Project each milestone from the tasks that have to be done for it.

    Takes (name, target_date, [(task_title, task_due_date), ...]) so the
    comparison stays testable without a database. A milestone is reached when
    its last task finishes, so the projection is the latest due date among
    them.
    """
    checks: list[MilestoneCheck] = []
    for name, target, tasks in milestones:
        dues = [due for _, due in tasks if due is not None]
        checks.append(
            MilestoneCheck(
                name=name,
                target_date=target,
                projected_date=max(dues) if dues else None,
                task_titles=[title for title, _ in tasks],
            )
        )
    return checks


def milestone_summary(checks: list[MilestoneCheck]) -> dict:
    """Counts and messages for the API and the validation stage."""
    late = [c for c in checks if c.status == MilestoneStatus.LATE]
    early = [c for c in checks if c.status == MilestoneStatus.EARLY]
    unscheduled = [c for c in checks if c.status == MilestoneStatus.UNSCHEDULED]
    return {
        "total": len(checks),
        "late": len(late),
        "early": len(early),
        "unscheduled": len(unscheduled),
        "on_track": len(checks) - len(late) - len(early) - len(unscheduled),
        "breaches": [c.detail for c in late],
        "warnings": [c.detail for c in early + unscheduled],
    }


@dataclass
class MilestoneSegment:
    """One stretch of the SOW's timetable, and what the plan puts in it.

    The comparison that matters is between consecutive checkpoints, not across
    the whole project. A plan can beat the final date while missing every
    window along the way, and a whole-project number hides exactly that.
    """

    name: str
    after: str
    allowed_days: int
    planned_days: int

    @property
    def unaccounted_days(self) -> int:
        """Days the SOW allows that the plan does not use."""
        return max(self.allowed_days - self.planned_days, 0)

    @property
    def coverage(self) -> float | None:
        """Share of the agreed window the plan actually fills."""
        if self.allowed_days <= 0:
            return None
        return self.planned_days / self.allowed_days

    @property
    def detail(self) -> str:
        window = f"after {self.after}" if self.after else "from kickoff"
        if self.allowed_days <= 0:
            return (
                f"{self.name}: the SOW gives no working days for this step "
                f"({window}), so it cannot be compared"
            )
        return (
            f"{self.name}: the SOW allows {self.allowed_days} working day(s) {window}, "
            f"the plan uses {self.planned_days}"
        )


def calibrate(project_start: date, checks: list[MilestoneCheck]) -> list[MilestoneSegment]:
    """Measure each agreed window against what the plan puts inside it.

    This is the only calibration signal a SOW actually contains. Nothing in the
    document states effort, so an estimate has nothing to answer to — except
    the dates the two parties already agreed, which bound the work between one
    checkpoint and the next.

    The gap is reported, never applied. Scaling estimates until they fill the
    window would replace one invented number with another, and the slack in a
    SOW is not all work: it holds review cycles and waiting on the client too.
    """
    from app.planning.schedule import working_days_between

    usable = sorted(
        (c for c in checks if c.target_date and c.projected_date),
        key=lambda c: c.target_date,
    )
    segments: list[MilestoneSegment] = []
    previous_target, previous_projected, previous_name = project_start, project_start, ""
    for check in usable:
        segments.append(
            MilestoneSegment(
                name=check.name,
                after=previous_name,
                allowed_days=working_days_between(previous_target, check.target_date),
                planned_days=working_days_between(previous_projected, check.projected_date),
            )
        )
        previous_target, previous_projected, previous_name = (
            check.target_date,
            check.projected_date,
            check.name,
        )
    return segments


def calibration_summary(segments: list[MilestoneSegment]) -> dict:
    """What the whole timetable says about the estimates behind it."""
    allowed = sum(s.allowed_days for s in segments)
    planned = sum(s.planned_days for s in segments)
    return {
        "segments": len(segments),
        "allowed_days": allowed,
        "planned_days": planned,
        "unaccounted_days": max(allowed - planned, 0),
        "coverage": (planned / allowed) if allowed else None,
        # The steps where the plan uses well under the agreed window are where
        # an estimate is most likely to be missing something.
        "widest_gaps": [
            s.detail for s in sorted(segments, key=lambda x: -x.unaccounted_days)[:3]
            if s.unaccounted_days > 0
        ],
    }
