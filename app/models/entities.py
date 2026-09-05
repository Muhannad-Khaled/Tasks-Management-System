from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


task_dependencies = Table(
    "task_dependencies",
    Base.metadata,
    Column("task_id", ForeignKey("project_tasks.id"), primary_key=True),
    Column("depends_on_id", ForeignKey("project_tasks.id"), primary_key=True),
)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="ingesting")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    merchant_name: Mapped[str] = mapped_column(String(255), default="")
    # Dates read from the SOW. Stored so the schedule can be recomputed later
    # and still be checked against the deadline the SOW actually committed to.
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    go_live_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    sow_documents: Mapped[list[SOWDocument]] = relationship(back_populates="project")
    tasks: Mapped[list[ProjectTask]] = relationship(back_populates="project")
    assumptions: Mapped[list[Assumption]] = relationship(back_populates="project")


class SOWDocument(Base):
    __tablename__ = "sow_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    doc_key: Mapped[str] = mapped_column(String(32))  # e.g. SOW-001
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))  # pdf | docx | txt
    stored_path: Mapped[str] = mapped_column(String(512))
    parsing_status: Mapped[str] = mapped_column(String(32), default="pending")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="sow_documents")
    sections: Mapped[list[SOWSection]] = relationship(back_populates="document")


class SOWSection(Base):
    __tablename__ = "sow_sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sow_document_id: Mapped[str] = mapped_column(ForeignKey("sow_documents.id"))
    section_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(512), default="")
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    document: Mapped[SOWDocument] = relationship(back_populates="sections")
    chunks: Mapped[list[SOWChunk]] = relationship(back_populates="section")


class SOWChunk(Base):
    __tablename__ = "sow_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    section_id: Mapped[str] = mapped_column(ForeignKey("sow_sections.id"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    chunk_key: Mapped[str] = mapped_column(String(64), index=True)  # e.g. SOW-001-S02-C03
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)

    section: Mapped[SOWSection] = relationship(back_populates="chunks")


class ProjectTask(Base):
    __tablename__ = "project_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    team: Mapped[str] = mapped_column(String(32))
    assignee: Mapped[str] = mapped_column(String(255), default="")
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(32), default="backlog")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    estimated_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_status: Mapped[str] = mapped_column(String(16), default="explicit")
    source_sow_section_id: Mapped[str | None] = mapped_column(
        ForeignKey("sow_sections.id"), nullable=True
    )
    source_chunk_keys: Mapped[str] = mapped_column(Text, default="")  # comma-separated chunk keys
    grounding_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(32), default="pending")
    external_ref: Mapped[str] = mapped_column(String(255), default="")  # Trello card / Plane issue

    project: Mapped[Project] = relationship(back_populates="tasks")
    depends_on: Mapped[list[ProjectTask]] = relationship(
        secondary=task_dependencies,
        primaryjoin=id == task_dependencies.c.task_id,
        secondaryjoin=id == task_dependencies.c.depends_on_id,
    )


class Assumption(Base):
    __tablename__ = "assumptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    assumption_key: Mapped[str] = mapped_column(String(32))  # e.g. A-001
    category: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="assumed")
    created_by: Mapped[str] = mapped_column(String(64), default="system")

    project: Mapped[Project] = relationship(back_populates="assumptions")


class LLMRequest(Base):
    __tablename__ = "llm_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    graph_node: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    output: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
