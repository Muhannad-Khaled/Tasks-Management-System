"""The validation pipeline (brief section 20).

    schema -> grounding -> evidence -> business rules -> dependencies -> timeline

Each stage answers a different question, and a stage that only the model could
answer is not a check. Schema, business rules, dependencies and timeline are
deterministic; grounding and evidence use the model but only as a judge over
retrieved text, never as a source.

Failures do not delete work. They mark it, so the PM sees what did not hold up
rather than finding a shorter plan with no explanation.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.grounding.engine import GroundingStatus, TaskGrounding, project_grounding_score
from app.models import (
    ProjectMilestone,
    ProjectRole,
    ProjectTask,
    ProjectTestCase,
    SOWChunk,
    UserStory,
    ValidationLog,
)
from app.planning.dependencies import build_dependency_graph
from app.planning.milestones import check_milestones, milestone_summary
from app.schemas.enums import Team

logger = logging.getLogger(__name__)

VALIDATOR_VERSION = "v1"

_NUMBER = re.compile(r"\d")


def _asserts_a_number(text: str) -> bool:
    return bool(_NUMBER.search(text or ""))

# Words that signal a task belongs to a particular team. Used to catch an item
# filed under the wrong team, which would send it to the wrong Trello list and
# the wrong people (brief section 20, "does the task belong to the correct team").
#
# Signals must be distinctive to one team. A bare "configur" is not: it flagged
# "Configure Points Calculation & Expiry Engine" as operations work when
# configuring a calculation engine is plainly technical. Operations signals name
# what operations configures — merchants, stores, offers — not the act itself.
TEAM_SIGNALS = {
    Team.COMMERCIAL: (
        "contract",
        "pricing",
        "sign-off",
        "signature",
        "commercial terms",
        "revenue share",
        "sla",
    ),
    Team.TECHNICAL: (
        "api",
        "integration",
        "endpoint",
        "auth",
        "engine",
        "calculation",
        "accrual logic",
        "schema",
        "deploy",
        "code",
        "unit test",
        "end-to-end test",
    ),
    Team.OPERATIONS: (
        "training",
        "onboard",
        "merchant configuration",
        "store configuration",
        "configure the merchant",
        "go-live",
        "hypercare",
        "rollout",
    ),
}


@dataclass
class StageResult:
    stage: str
    passed: bool
    detail: str
    grounding_score: float | None = None
    offending_task_ids: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    stages: list[StageResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.stages)

    @property
    def failures(self) -> list[StageResult]:
        return [s for s in self.stages if not s.passed]

    def summary(self) -> list[str]:
        return [
            f"{'PASS' if s.passed else 'FAIL'} {s.stage}: {s.detail}" for s in self.stages
        ]


def validate_schema(tasks: list[ProjectTask]) -> StageResult:
    """Structural completeness the Pydantic parse cannot enforce on its own."""
    problems = []
    for task in tasks:
        if not task.title.strip() or task.team not in {t.value for t in Team}:
            problems.append(task.id)
    return StageResult(
        stage="schema",
        passed=not problems,
        detail=f"{len(tasks)} tasks, {len(problems)} structurally invalid",
        offending_task_ids=problems,
    )


def validate_grounding(groundings: list[TaskGrounding]) -> StageResult:
    """Project-level grounding against the accept/review/reject policy."""
    score = project_grounding_score(groundings)
    rejected = [g.task_id for g in groundings if g.status == GroundingStatus.REJECT]
    review = [g.task_id for g in groundings if g.status == GroundingStatus.REVIEW]
    return StageResult(
        stage="grounding",
        passed=not rejected,
        detail=(
            f"score {score:.0%}; {len(rejected)} task(s) rejected, "
            f"{len(review)} need review"
        ),
        grounding_score=score,
        offending_task_ids=rejected,
    )


def validate_evidence(db: Session, tasks: list[ProjectTask]) -> StageResult:
    """Every cited chunk must exist and actually contain text."""
    problems = []
    for task in tasks:
        for key in (k for k in task.source_chunk_keys.split(",") if k):
            chunk = db.query(SOWChunk).filter(SOWChunk.chunk_key == key).first()
            if chunk is None or not chunk.text.strip():
                problems.append(task.id)
                break
    return StageResult(
        stage="evidence",
        passed=not problems,
        detail=f"{len(problems)} task(s) cite evidence that is missing or empty",
        offending_task_ids=problems,
    )


def validate_business_rules(tasks: list[ProjectTask]) -> StageResult:
    """Catch a task filed under a team its own wording contradicts."""
    problems = []
    for task in tasks:
        text = f"{task.title} {task.description}".lower()
        matches = {
            team for team, signals in TEAM_SIGNALS.items() if any(s in text for s in signals)
        }
        # Only flag when the wording points at exactly one team and it is not this one.
        if len(matches) == 1 and task.team not in {t.value for t in matches}:
            problems.append(task.id)
    return StageResult(
        stage="business_rules",
        passed=not problems,
        detail=f"{len(problems)} task(s) assigned to a team their wording contradicts",
        offending_task_ids=problems,
    )


def validate_dependencies(tasks: list[ProjectTask]) -> StageResult:
    graph = build_dependency_graph({t.id: [d.id for d in t.depends_on] for t in tasks})
    return StageResult(
        stage="dependencies",
        passed=graph.is_valid,
        detail=(
            "; ".join(i.detail for i in graph.issues[:3])
            if graph.issues
            else "no cycles or dangling dependencies"
        ),
    )


def validate_milestones(db: Session, project_id: str) -> StageResult:
    """The plan must answer to the dates in between, not just the final one.

    Checking only the deadline passes a schedule that finishes six weeks early
    because nobody estimated the work — every agreed checkpoint missed, in the
    direction that looks like good news.
    """
    rows = db.query(ProjectMilestone).filter(ProjectMilestone.project_id == project_id).all()
    if not rows:
        return StageResult(
            stage="milestones",
            passed=True,
            detail="the SOW states no intermediate milestones",
        )

    checks = check_milestones(
        [(m.name, m.target_date, [(t.title, t.due_date) for t in m.tasks]) for m in rows]
    )
    summary = milestone_summary(checks)
    messages = summary["breaches"] + summary["warnings"]
    return StageResult(
        stage="milestones",
        # Only a late milestone fails: it breaks what was agreed. Finishing
        # early is a signal about the estimates, not a broken promise.
        passed=not summary["breaches"],
        detail=(
            f"{summary['on_track']}/{summary['total']} on track, "
            f"{summary['late']} late, {summary['early']} far early"
            + ("; " + "; ".join(messages[:3]) if messages else "")
        ),
    )


def validate_timeline(tasks: list[ProjectTask], deadline) -> StageResult:
    """The schedule must satisfy the SOW's own deadline (brief section 15)."""
    due_dates = [t.due_date for t in tasks if t.due_date]
    if not due_dates or deadline is None:
        return StageResult(
            stage="timeline",
            passed=True,
            detail="no deadline in the SOW to check against"
            if due_dates
            else "no scheduled dates yet",
        )
    end = max(due_dates)
    late = [t.id for t in tasks if t.due_date and t.due_date > deadline]
    return StageResult(
        stage="timeline",
        passed=end <= deadline,
        detail=(
            f"ends {end.isoformat()} against a deadline of {deadline.isoformat()}"
            + (f"; {len(late)} task(s) fall past it" if late else "")
        ),
        offending_task_ids=late,
    )


def validate_derived_artifacts(db: Session, project_id: str) -> StageResult:
    """Check the derived layer holds together and declares what it stands on.

    Deterministic on purpose. A user story is a restatement, so judging it with
    the model would score a legitimate derivation as unsupported; what can be
    checked without one is that nothing is orphaned and that no test asserting
    a number hides where the number came from.
    """
    stories = db.query(UserStory).filter(UserStory.project_id == project_id).all()
    cases = db.query(ProjectTestCase).filter(ProjectTestCase.project_id == project_id).all()
    if not stories and not cases:
        return StageResult(
            stage="derived_artifacts", passed=True, detail="no derived artifacts to check"
        )

    problems: list[str] = []
    orphans = [s.story_key for s in stories if s.requirement_id is None]
    if orphans:
        problems.append(f"{len(orphans)} story(s) not linked to a requirement")

    storyless = [c.case_key for c in cases if c.user_story_id is None]
    if storyless:
        problems.append(f"{len(storyless)} test case(s) not linked to a story")

    criterionless = [s.story_key for s in stories if not s.acceptance_criteria]
    if criterionless:
        problems.append(f"{len(criterionless)} story(s) with no acceptance criteria")

    # "As a support agent, I want to deliver training" is a task wearing a user
    # story's grammar. It cannot be accepted by anyone outside the project.
    inward = [s.story_key for s in stories if s.actor_is_delivery_side]
    if inward:
        problems.append(
            f"{len(inward)} story(s) written from the delivery team's point of view "
            f"rather than a user's: {inward[:3]}"
        )

    # A number in an expected result either traces to the SOW or names the
    # assumption it came from. Silence is the failure mode that matters: it
    # reads as fact and gets signed off as one.
    unexplained = [
        c.case_key
        for c in cases
        if _asserts_a_number(c.expected_result)
        and not c.assumed_fields
        and not (c.user_story and c.user_story.source_chunk_keys)
    ]
    if unexplained:
        problems.append(
            f"{len(unexplained)} test case(s) assert a number with neither evidence "
            f"nor a named assumption: {unexplained[:3]}"
        )

    resting = [c.case_key for c in cases if c.rests_on_assumption]
    return StageResult(
        stage="derived_artifacts",
        passed=not problems,
        detail=(
            "; ".join(problems)
            if problems
            else (
                f"{len(stories)} story(s), {len(cases)} test case(s); "
                f"{len(resting)} rest on an assumed value"
            )
        ),
    )


def record_stage(db: Session, project_id: str, stage: StageResult) -> None:
    """Append one stage's outcome, replacing any earlier run of that stage.

    Used by stages that run outside run_validation, which wipes the log before
    writing its own.
    """
    db.query(ValidationLog).filter(
        ValidationLog.project_id == project_id, ValidationLog.stage == stage.stage
    ).delete()
    db.add(
        ValidationLog(
            project_id=project_id,
            stage=stage.stage,
            passed=stage.passed,
            detail=stage.detail,
            grounding_score=stage.grounding_score,
            validator_version=VALIDATOR_VERSION,
        )
    )
    db.commit()


def _owner_of(db: Session, task: ProjectTask) -> str:
    """The person doing this task, by the same rule the card body uses."""
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


def validate_staffing(db: Session, tasks: list[ProjectTask]) -> StageResult:
    """One person cannot be in two places at once.

    A warning rather than a failure, like every other schedule finding here.
    Someone genuinely may split a week across two small tasks, and the platform
    has no way to know which case this is — but it does know the dates overlap,
    and saying so is the whole job.
    """
    scheduled: dict[str, list[ProjectTask]] = defaultdict(list)
    for task in tasks:
        if task.start_date and task.due_date and (owner := _owner_of(db, task)):
            scheduled[owner].append(task)

    clashes: list[str] = []
    offenders: list[str] = []
    for owner, owned in scheduled.items():
        owned.sort(key=lambda t: t.start_date)
        # Carry the task that runs latest rather than simply the previous one:
        # a long task overlaps everything that starts before it ends, and
        # comparing neighbours would miss all but the first of them.
        running = owned[0]
        for task in owned[1:]:
            # Inclusive dates: finishing and starting on the same day is one
            # person working both, not a clean handover.
            if task.start_date <= running.due_date:
                days = (running.due_date - task.start_date).days + 1
                clashes.append(
                    f"{owner} is on {running.title!r} and {task.title!r} "
                    f"at the same time ({days} day(s) overlapping)"
                )
                offenders += [running.id, task.id]
            if task.due_date > running.due_date:
                running = task

    return StageResult(
        stage="staffing",
        passed=not clashes,
        detail="; ".join(clashes) if clashes else f"{len(scheduled)} person(s), no clashes",
        offending_task_ids=sorted(set(offenders)),
    )


def run_validation(
    db: Session,
    project_id: str,
    tasks: list[ProjectTask],
    groundings: list[TaskGrounding],
    deadline=None,
) -> ValidationReport:
    report = ValidationReport(
        stages=[
            validate_schema(tasks),
            validate_grounding(groundings),
            validate_evidence(db, tasks),
            validate_business_rules(tasks),
            validate_dependencies(tasks),
            validate_timeline(tasks, deadline),
            validate_milestones(db, project_id),
            validate_staffing(db, tasks),
        ]
    )

    db.query(ValidationLog).filter(ValidationLog.project_id == project_id).delete()
    db.add_all(
        ValidationLog(
            project_id=project_id,
            stage=stage.stage,
            passed=stage.passed,
            detail=stage.detail,
            grounding_score=stage.grounding_score,
            validator_version=VALIDATOR_VERSION,
        )
        for stage in report.stages
    )
    db.commit()
    return report
