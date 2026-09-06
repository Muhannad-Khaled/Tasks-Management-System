from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


milestone_tasks = Table(
    "milestone_tasks",
    Base.metadata,
    Column("milestone_id", ForeignKey("project_milestones.id"), primary_key=True),
    Column("task_id", ForeignKey("project_tasks.id"), primary_key=True),
)


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

    # The board this project pushes to, remembered so every later push lands on
    # it. Without these the adapter created a fresh board on each push, which
    # made pushing one task at a time impossible: the PM would have ended up
    # with one board per task.
    board_id: Mapped[str] = mapped_column(String(255), default="")
    board_url: Mapped[str] = mapped_column(String(512), default="")

    # Deleting a project must take its children with it. Without the cascade,
    # SQLAlchemy tries to null out project_id instead and the delete fails on
    # the NOT NULL constraint — which is what happens whenever a run is
    # abandoned partway and the empty project is cleaned up.
    sow_documents: Mapped[list[SOWDocument]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[ProjectTask]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    assumptions: Mapped[list[Assumption]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    requirements: Mapped[list[ProjectRequirement]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    roles: Mapped[list[ProjectRole]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    drift: Mapped[list[BoardDrift]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    milestones: Mapped[list[ProjectMilestone]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    details: Mapped[list[ProjectDetail]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    questions: Mapped[list[ClarificationQuestion]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    user_stories: Mapped[list[UserStory]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    test_cases: Mapped[list[ProjectTestCase]] = relationship(cascade="all, delete-orphan")
    claims: Mapped[list[ClaimRecord]] = relationship(cascade="all, delete-orphan")
    validation_logs: Mapped[list[ValidationLog]] = relationship(cascade="all, delete-orphan")
    llm_requests: Mapped[list[LLMRequest]] = relationship(cascade="all, delete-orphan")


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
    sections: Mapped[list[SOWSection]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class SOWSection(Base):
    __tablename__ = "sow_sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sow_document_id: Mapped[str] = mapped_column(ForeignKey("sow_documents.id"))
    section_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(512), default="")
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    document: Mapped[SOWDocument] = relationship(back_populates="sections")
    chunks: Mapped[list[SOWChunk]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )


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
    # The role that owns the task. `assignee` names a *person* and stays
    # empty: the platform has no user directory, so inventing a name would
    # be the one kind of hallucination it exists to prevent.
    assignee_role: Mapped[str] = mapped_column(String(64), default="")
    requirement_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_requirements.id"), nullable=True
    )
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(32), default="backlog")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    estimated_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Where the duration came from. An effort estimate is not in the SOW and
    # never will be, so it is an assumption by nature — and a schedule built on
    # one must not present itself as though someone had measured the work.
    estimate_source: Mapped[str] = mapped_column(String(16), default="assumed")
    source_status: Mapped[str] = mapped_column(String(16), default="explicit")
    source_sow_section_id: Mapped[str | None] = mapped_column(
        ForeignKey("sow_sections.id"), nullable=True
    )
    source_chunk_keys: Mapped[str] = mapped_column(Text, default="")  # comma-separated chunk keys
    grounding_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(32), default="pending")
    external_ref: Mapped[str] = mapped_column(String(255), default="")  # Trello card / Plane issue
    # A digest of the title and body last written to the board. Without it,
    # a card that differs from the plan is ambiguous: it could be a person's
    # edit, or a plan that has moved on since the card was written.
    board_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    # Set when a task that is already on the board changes, so pushing the rest
    # of the project updates the cards that moved on and leaves the others
    # alone. Re-sending every card each time costs ~3 Trello calls per card.
    board_dirty: Mapped[bool] = mapped_column(Boolean, default=False)
    # PM review, tracked per task so rejecting one item regenerates only that
    # item rather than discarding the whole plan (brief section 23).
    review_status: Mapped[str] = mapped_column(String(16), default="pending")
    review_note: Mapped[str] = mapped_column(Text, default="")
    regeneration_count: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="tasks")
    requirement: Mapped[ProjectRequirement | None] = relationship(back_populates="tasks")
    drift: Mapped[list[BoardDrift]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    source_section: Mapped[SOWSection | None] = relationship()
    depends_on: Mapped[list[ProjectTask]] = relationship(
        secondary=task_dependencies,
        primaryjoin=id == task_dependencies.c.task_id,
        secondaryjoin=id == task_dependencies.c.depends_on_id,
    )


class Person(Base):
    """Somebody who actually does the work.

    Not tied to a project: one directory the whole platform draws on, so a
    person who leaves is corrected once rather than in every plan that named
    them. Nothing here is ever extracted or inferred — a row exists only
    because a human typed it.
    """

    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    # Kept rather than deleted: a person who has left still explains who was
    # assigned what on a plan that already ran.
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    roles: Mapped[list[ProjectRole]] = relationship(back_populates="person")

    capabilities: Mapped[list[PersonRole]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )

    @property
    def role_titles(self) -> list[str]:
        return sorted(row.role_title for row in self.capabilities)


class PersonRole(Base):
    """One thing a person can be put on.

    A separate row per role rather than a column: people on a small team wear
    several hats, and a single "their role" field would force everything after
    the first to be staffed by hand — which is the work this exists to remove.
    """

    __tablename__ = "person_roles"
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), primary_key=True)
    role_title: Mapped[str] = mapped_column(String(64), primary_key=True)

    person: Mapped[Person] = relationship(back_populates="capabilities")


class BoardDrift(Base):
    """A difference between the board and the plan that nobody has settled yet.

    Kept as rows rather than recomputed each time so a difference is reported
    once and then left alone. An alert that repeats every cycle until someone
    acts is an alert people turn off, and the next one after that goes unread.
    """

    __tablename__ = "board_drift"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("project_tasks.id"))
    kind: Mapped[str] = mapped_column(String(16))  # moved | edited | deleted
    detail: Mapped[str] = mapped_column(Text, default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Null until the PM has been told. A row can exist unsent when no webhook
    # is configured, and it is still worth showing in the UI.
    notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set when a later check no longer finds the difference — the card was put
    # back, or the plan was changed to agree with it.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    project: Mapped[Project] = relationship(back_populates="drift")
    task: Mapped[ProjectTask] = relationship(back_populates="drift")


class ProjectRequirement(Base):
    """What the SOW says must be delivered, before it is broken into tasks.

    Extraction has always produced these; nothing stored them, so the
    SOW -> requirement -> task chain the platform is built on was broken at
    its middle link and user stories had nothing to hang off.
    """

    __tablename__ = "project_requirements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    requirement_key: Mapped[str] = mapped_column(String(32))  # REQ-001
    team: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    source_status: Mapped[str] = mapped_column(String(16), default="explicit")
    source_sow_section_id: Mapped[str | None] = mapped_column(
        ForeignKey("sow_sections.id"), nullable=True
    )
    source_chunk_keys: Mapped[str] = mapped_column(Text, default="")

    project: Mapped[Project] = relationship(back_populates="requirements")
    tasks: Mapped[list[ProjectTask]] = relationship(back_populates="requirement")
    # Declared purely so the ORM knows a requirement must be deleted before the
    # section it points at. Without it, deleting a project raises a foreign key
    # violation on sow_sections.
    source_section: Mapped[SOWSection | None] = relationship()


class ProjectRole(Base):
    """A role on the delivery team, and where it came from.

    A SOW that names its team is believed and the rows are EXPLICIT; one that
    does not gets the default roster, recorded as ASSUMED like any other gap.
    """

    __tablename__ = "project_roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    team: Mapped[str] = mapped_column(String(32))
    role_title: Mapped[str] = mapped_column(String(64))
    responsibility: Mapped[str] = mapped_column(Text, default="")
    headcount: Mapped[int] = mapped_column(Integer, default=1)
    source_status: Mapped[str] = mapped_column(String(16), default="assumed")
    # Who holds this role on this project. Null until somebody says; the plan
    # is complete and usable without it, which is why it is not required.
    person_id: Mapped[str | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    source_chunk_keys: Mapped[str] = mapped_column(Text, default="")

    project: Mapped[Project] = relationship(back_populates="roles")
    person: Mapped[Person | None] = relationship(back_populates="roles")


class ProjectMilestone(Base):
    """A date the SOW commits to, and the tasks that have to land by it.

    Kept apart from Project.go_live_date because the dates in between are what
    the timeline check was blind to: it asked only whether the plan beat the
    final deadline, said yes, and passed a schedule that ignored every agreed
    checkpoint along the way.
    """

    __tablename__ = "project_milestones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(512))
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_status: Mapped[str] = mapped_column(String(16), default="explicit")
    source_chunk_keys: Mapped[str] = mapped_column(Text, default="")

    project: Mapped[Project] = relationship(back_populates="milestones")
    tasks: Mapped[list[ProjectTask]] = relationship(secondary=milestone_tasks)


class ProjectDetail(Base):
    """One item the SOW enumerates, with the team that owns its category.

    `team` is denormalised from the category so the structure view and the
    per-team queries do not have to import the taxonomy to group rows.
    """

    __tablename__ = "project_details"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    category: Mapped[str] = mapped_column(String(48), index=True)
    team: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    source_status: Mapped[str] = mapped_column(String(16), default="explicit")
    source_chunk_keys: Mapped[str] = mapped_column(Text, default="")

    project: Mapped[Project] = relationship(back_populates="details")


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


class UserStory(Base):
    """A requirement restated as something a person wants to be able to do.

    Never EXPLICIT: a story is a restatement, so its provenance is inherited
    from the requirement it came from rather than claimed on its own.
    """

    __tablename__ = "user_stories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    requirement_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_requirements.id"), nullable=True
    )
    story_key: Mapped[str] = mapped_column(String(32))  # US-001
    team: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(128))
    capability: Mapped[str] = mapped_column(Text)
    benefit: Mapped[str] = mapped_column(Text, default="")
    source_status: Mapped[str] = mapped_column(String(16), default="inferred")
    source_chunk_keys: Mapped[str] = mapped_column(Text, default="")
    # True when the actor is one of our own delivery roles. Such a story is a
    # task in disguise — it describes work rather than value, so no one outside
    # the project can say whether it is done.
    actor_is_delivery_side: Mapped[bool] = mapped_column(Boolean, default=False)

    project: Mapped[Project] = relationship(back_populates="user_stories")
    requirement: Mapped[ProjectRequirement | None] = relationship()
    acceptance_criteria: Mapped[list[AcceptanceCriterion]] = relationship(
        back_populates="user_story", cascade="all, delete-orphan"
    )
    test_cases: Mapped[list[ProjectTestCase]] = relationship(
        back_populates="user_story", cascade="all, delete-orphan"
    )

    @property
    def sentence(self) -> str:
        return f"As a {self.actor}, I want {self.capability} so that {self.benefit}"


class AcceptanceCriterion(Base):
    __tablename__ = "acceptance_criteria"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_story_id: Mapped[str] = mapped_column(ForeignKey("user_stories.id"))
    criterion_key: Mapped[str] = mapped_column(String(32))  # AC-001
    text: Mapped[str] = mapped_column(Text)

    user_story: Mapped[UserStory] = relationship(back_populates="acceptance_criteria")


class ProjectTestCase(Base):
    """A check someone will actually run, and what it is standing on.

    `rests_on_assumption` is the reason this table exists rather than a list in
    a description. A case asserting "$100 earns 1,000 points" against an earn
    rate the platform assumed would let a QA engineer sign off a number nobody
    ever specified, and that verdict has to travel with the case everywhere it
    is shown.
    """

    __tablename__ = "test_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    user_story_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_stories.id"), nullable=True
    )
    case_key: Mapped[str] = mapped_column(String(32))  # TC-001
    title: Mapped[str] = mapped_column(String(512))
    preconditions: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(Text, default="")
    expected_result: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(32), default="functional")
    rests_on_assumption: Mapped[bool] = mapped_column(Boolean, default=False)
    # Which planning fields, so the warning can name them.
    assumed_fields: Mapped[str] = mapped_column(Text, default="")

    user_story: Mapped[UserStory | None] = relationship(back_populates="test_cases")


class ClarificationQuestion(Base):
    """Something the SOW left unanswered, phrased as a question to ask.

    These are not generated: every ASSUMED planning field already *is* a
    question the document failed to settle, and FieldSpec already carries the
    wording. So the question set is a projection of the assumption engine's
    output rather than another model call — cheaper, and it cannot ask about
    something the SOW actually answered.
    """

    __tablename__ = "clarification_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    question_key: Mapped[str] = mapped_column(String(32))  # Q-001
    scope: Mapped[str] = mapped_column(String(16), index=True)  # merchant | offer | project
    category: Mapped[str] = mapped_column(String(64))
    field_key: Mapped[str] = mapped_column(String(64))
    team: Mapped[str] = mapped_column(String(32), default="")
    text: Mapped[str] = mapped_column(Text)
    # What the plan currently runs on while the question is unanswered, so the
    # reader can see the cost of leaving it open.
    working_assumption: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | answered
    answer: Mapped[str] = mapped_column(Text, default="")

    project: Mapped[Project] = relationship(back_populates="questions")


class ClaimRecord(Base):
    """One atomic assertion made by a generated task, with its verdict.

    Stored per claim rather than per task so the UI can show exactly which
    detail failed, and so hallucination rate is measurable over time.
    """

    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("project_tasks.id"), nullable=True)
    claim_key: Mapped[str] = mapped_column(String(32))  # C-001
    text: Mapped[str] = mapped_column(Text)
    is_quantitative: Mapped[bool] = mapped_column(Boolean, default=False)
    verdict: Mapped[str] = mapped_column(String(24), default="unsupported")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    supporting_chunk_keys: Mapped[str] = mapped_column(Text, default="")


class ValidationLog(Base):
    """Outcome of one validation stage for one project (brief section 22)."""

    __tablename__ = "validation_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    stage: Mapped[str] = mapped_column(String(48))
    passed: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    grounding_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    validator_version: Mapped[str] = mapped_column(String(16), default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


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
