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
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.grounding.engine import GroundingStatus, TaskGrounding, project_grounding_score
from app.models import ProjectTask, SOWChunk, ValidationLog
from app.planning.dependencies import build_dependency_graph
from app.schemas.enums import Team

logger = logging.getLogger(__name__)

VALIDATOR_VERSION = "v1"

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
