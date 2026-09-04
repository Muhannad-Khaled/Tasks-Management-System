"""Pydantic models for structured SOW extraction (brief section 8).

This is the M1 lean version: a single-pass extraction target. It grows
per-team detail models (Merchants, Offers, SLAs, User Stories, ...) in M2.
Every extracted object carries provenance: source_status + source chunk keys.
"""

from datetime import date

from pydantic import BaseModel, Field

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


class ExtractedTask(Provenance):
    task_id: str = Field(description="Stable id like T-001")
    team: Team
    title: str
    description: str = ""
    priority: Priority = Priority.MEDIUM
    estimated_hours: float | None = None
    depends_on: list[str] = Field(
        default_factory=list, description="task_ids this task depends on"
    )
    requirement_id: str | None = Field(
        default=None, description="The requirement this task implements"
    )


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
    requirements: list[ExtractedRequirement] = Field(default_factory=list)
    tasks: list[ExtractedTask] = Field(default_factory=list)
    assumptions: list[ExtractedAssumption] = Field(default_factory=list)
