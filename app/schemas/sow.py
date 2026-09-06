"""Pydantic models for structured SOW extraction (brief section 8).

This is the M1 lean version: a single-pass extraction target. It grows
per-team detail models (Merchants, Offers, SLAs, User Stories, ...) in M2.
Every extracted object carries provenance: source_status + source chunk keys.
"""

from datetime import date

from pydantic import BaseModel, Field

from app.schemas.details import DetailCategory
from app.schemas.enums import Priority, SourceStatus, Team


class Provenance(BaseModel):
    source_status: SourceStatus
    source_chunk_keys: list[str] = Field(
        default_factory=list,
        description="Chunk keys (e.g. SOW-001-S02-C03) supporting this item. Empty if ASSUMED.",
    )


class ExtractedRequirement(Provenance):
    requirement_id: str = Field(description="Stable id like REQ-001")
    team: Team
    title: str
    description: str = ""


class ExtractedDetail(Provenance):
    """One thing the SOW enumerates: an API, an offer, a configuration step.

    The team is not asked for. It follows from the category, so the model
    cannot file an API under the commercial team.
    """

    category: DetailCategory
    name: str = Field(description="Short label, e.g. 'Points accrual endpoint'")
    description: str = ""


class ExtractedRole(Provenance):
    """A role on the delivery team, as the SOW describes it."""

    title: str = Field(description="Role title, e.g. 'Backend Engineer'")
    team: Team
    responsibility: str = ""
    headcount: int = Field(default=1, ge=1)


class ExtractedTask(Provenance):
    task_id: str = Field(description="Stable id like T-001")
    team: Team
    title: str
    description: str = ""
    assignee_role: str = Field(
        default="",
        description=(
            "The role on the owning team best suited to do this work, e.g. "
            "'QA Engineer'. Never a person's name."
        ),
    )
    priority: Priority = Priority.MEDIUM
    # Required, not optional. Left optional the model simply omitted it for
    # nine tasks out of ten, and the scheduler quietly used one day for each —
    # producing a timeline that looked estimated and was not.
    # No gt/ge constraint: Gemini's response_schema rejects exclusiveMinimum
    # and the whole extraction call fails. A non-positive value is caught in
    # code instead, where it is treated as no estimate at all.
    estimated_hours: float = Field(
        description=(
            "Effort in person-hours for this task alone, excluding waiting on "
            "others. Judge it from the work described; a SOW rarely states it."
        ),
    )
    depends_on: list[str] = Field(
        default_factory=list, description="task_ids this task depends on"
    )
    requirement_id: str | None = Field(
        default=None, description="The requirement this task implements"
    )
    milestone: str = Field(
        default="",
        description=(
            "The milestone this task contributes to, named exactly as in the "
            "milestones list. Empty if it belongs to none of them."
        ),
    )


class ExtractedMilestone(Provenance):
    """A dated checkpoint the SOW commits to, and the work behind it.

    Naming the tasks is what makes the date checkable. Without them a
    milestone is a string nobody can compare a schedule against.
    """

    name: str = Field(description="The milestone as the SOW names it")
    target_date: date | None = Field(
        default=None, description="The date the SOW gives, if it gives one"
    )
    # No task ids here: milestones are extracted before tasks exist, so the
    # link is declared on the task instead. Asking for ids not yet written
    # forced milestones to come last, and there they were simply dropped.


class ExtractedAssumption(BaseModel):
    assumption_id: str = Field(description="Stable id like A-001")
    category: str
    value: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class ProjectInfo(BaseModel):
    project_name: str
    merchant_name: str = ""
    start_date: date | None = None
    end_date: date | None = None
    go_live_date: date | None = None


class StructuredSOW(BaseModel):
    """Top-level extraction target for a single Gemini structured-output call."""

    project_info: ProjectInfo
    team_roster: list[ExtractedRole] = Field(
        default_factory=list,
        description="Roles the SOW names. Leave empty if it does not describe the team.",
    )
    requirements: list[ExtractedRequirement] = Field(default_factory=list)
    # Before tasks, deliberately: these are the windows the estimates have to
    # answer to, and the model writes its output in field order.
    milestones: list[ExtractedMilestone] = Field(
        default_factory=list,
        description="Dated checkpoints the SOW commits to between start and go-live.",
    )
    tasks: list[ExtractedTask] = Field(default_factory=list)
    details: list[ExtractedDetail] = Field(
        default_factory=list,
        description="Everything the SOW enumerates, filed under a category.",
    )
    assumptions: list[ExtractedAssumption] = Field(default_factory=list)
