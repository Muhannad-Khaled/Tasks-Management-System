"""LangGraph SOW processing workflow.

                          /-> extract   -\\
    parse -> validate_parsing            -> persist -> schedule -> grounding
                          \\-> gap_audit -/

The parsing gate is a real branch, not a formality: if parsing failed the graph
stops instead of letting the model reason over garbage (brief section 6).

Extraction and the gap audit are independent reads of the same document, so
they run concurrently. Scheduling then grounding run in order because the
timeline validation stage needs the dates scheduling produces. The HITL
interrupt attaches after grounding in a later milestone.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.graph.assumptions import (
    build_assumptions,
    build_questions,
    render_field_list,
    validate_findings,
)
from app.graph.persistence import (
    invented_citations,
    persist_artifacts,
    persist_assumptions,
    persist_document,
    persist_extraction,
    persist_grounding,
    persist_questions,
)
from app.graph.staffing import autostaff
from app.grounding.engine import ground_project, project_grounding_score
from app.ingestion.parser import parse_document
from app.ingestion.validation import ParsingStatus, validate_parsed_document
from app.llm.client import GeminiClient, LLMError, build_chunked_text
from app.llm.prompts import GAP_DETECTION, SOW_EXTRACTION, TECHNICAL_ARTIFACTS
from app.models import (
    Assumption,
    Project,
    ProjectDetail,
    ProjectRequirement,
    ProjectTask,
)
from app.notifications.discord import notify_review_ready
from app.planning.service import resolve_deadline, schedule_project
from app.rag.index import index_document, search
from app.schemas.artifacts import TechnicalArtifacts
from app.schemas.details import render_category_guide
from app.schemas.enums import ProjectStatus, SourceStatus, Team
from app.schemas.fields import PLANNING_FIELDS
from app.schemas.gaps import GapReport
from app.schemas.roles import render_role_guide
from app.schemas.sow import StructuredSOW
from app.validation.pipeline import record_stage, run_validation, validate_derived_artifacts

logger = logging.getLogger(__name__)


class SOWState(TypedDict, total=False):
    project_id: str
    file_path: str
    doc_key: str
    parsed_doc: Any
    parsing_status: str
    parsing_report: Any
    extraction: StructuredSOW
    gap_findings: dict
    assumptions: list[dict]
    questions: list[dict]
    schedule: Any
    critical_path: list[str]
    groundings: list[Any]
    grounding_score: float
    validation: Any
    task_count: int
    assumption_count: int
    question_count: int
    story_count: int
    test_case_count: int
    warnings: Annotated[list[str], lambda a, b: a + b]
    error: str


def _node_parse(state: SOWState) -> SOWState:
    doc = parse_document(Path(state["file_path"]), state["doc_key"])
    return {"parsed_doc": doc}


def _node_validate_parsing(state: SOWState) -> SOWState:
    report = validate_parsed_document(state["parsed_doc"])
    warnings = [f"{c.name}: {c.detail}" for c in report.failures]
    return {"parsing_status": str(report.status), "parsing_report": report, "warnings": warnings}


def _route_after_parsing(state: SOWState) -> str | list[str]:
    """Fan out to extraction and the gap audit, or stop for human review."""
    if state["parsing_status"] == ParsingStatus.FAILED:
        return "parsing_failed"
    return ["extract", "gap_audit"]


def _node_parsing_failed(state: SOWState) -> SOWState:
    return {"error": "Parsing failed; document needs human review before extraction."}


@contextmanager
def _node_session(db: Session | None) -> Iterator[Session | None]:
    """A session private to one node.

    Extraction and the gap audit run concurrently, and a SQLAlchemy Session is
    not thread-safe — sharing one raises "this session is provisioning a new
    connection" the moment both branches log an LLM request at once.
    """
    if db is None:
        yield None
        return
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def make_extract_node(client: GeminiClient, db: Session | None):
    """Read the document into a structured plan.

    An extraction carrying no tasks is treated as a failure rather than as an
    empty answer, and this is the whole reason the node is not three lines.

    `requirements` and `tasks` both default to an empty list, so a response
    that simply omits them validates cleanly. A live run did exactly that: the
    model returned project_info, an 11-role roster, 11 milestones and 26
    details, silently skipped the two sections in between, and the pipeline
    carried on and reported "Created a new project with 0 tasks" in a green
    success box. Everything downstream was correct about nothing.

    A document that parsed into chunks always has work in it, so an empty task
    list is a defect in the answer, not a fact about the SOW. It is retried
    once, because the same prompt on the same document produced twelve tasks
    the day before and skipping sections is a lapse rather than a verdict.
    """

    def _extract(state: SOWState) -> StructuredSOW:
        doc = state["parsed_doc"]
        with _node_session(db) as session:
            return client.generate_structured(
                prompt=SOW_EXTRACTION,
                schema=StructuredSOW,
                db=session,
                project_id=state["project_id"],
                graph_node="sow_extraction",
                doc_key=doc.doc_key,
                chunked_text=build_chunked_text(doc),
                role_guide=render_role_guide(),
                category_guide=render_category_guide(),
            )

    def _node_extract(state: SOWState) -> SOWState:
        extraction = _extract(state)
        if extraction.tasks:
            return {"extraction": extraction}

        missing = ", ".join(
            name for name in ("requirements", "tasks") if not getattr(extraction, name)
        )
        warnings = [
            f"extraction came back with no {missing}; asking once more before giving up"
        ]
        logger.warning("Extraction returned no tasks; retrying once")
        # A retry costs one of twenty daily calls. An empty project costs the
        # whole run and looks like a successful one.
        retried = _extract(state)
        if retried.tasks:
            return {"extraction": retried, "warnings": warnings}

        return {
            "extraction": retried,
            "warnings": warnings,
            "error": (
                f"The model read the document but returned no {missing}. This is a "
                "fault in the extraction, not something the SOW is missing — the "
                "document parsed into "
                f"{sum(1 for _ in state['parsed_doc'].iter_chunks())} chunks. "
                "Nothing was planned from it. Upload it again."
            ),
        }

    return _node_extract


def make_gap_audit_node(client: GeminiClient, db: Session | None):
    """Audit the SOW field by field so gaps are recorded, not silently skipped."""

    def _node_gap_audit(state: SOWState) -> SOWState:
        doc = state["parsed_doc"]
        valid_keys = {doc.chunk_key(s, c) for s, c in doc.iter_chunks()}
        with _node_session(db) as session:
            report = client.generate_structured(
                prompt=GAP_DETECTION,
                schema=GapReport,
                db=session,
                project_id=state["project_id"],
                graph_node="gap_audit",
                doc_key=doc.doc_key,
                chunked_text=build_chunked_text(doc),
                field_list=render_field_list(PLANNING_FIELDS),
            )
        findings, warnings = validate_findings(report, PLANNING_FIELDS, valid_keys)
        # The same gaps, seen twice: what the plan runs on, and what to ask.
        assumptions = build_assumptions(findings)
        questions = build_questions(findings)
        # Said at ingest as well as in the UI. A count of gaps with no count of
        # what was searched invites the reader to treat it as the whole truth
        # about the document, which it is not and was never able to be.
        warnings.append(
            f"gap audit: checked {len(PLANNING_FIELDS)} planning field(s), "
            f"{len(questions)} left unanswered by the SOW. Subjects outside "
            "those fields were not examined."
        )
        return {
            "gap_findings": findings,
            "assumptions": assumptions,
            "questions": questions,
            "warnings": warnings,
        }

    return _node_gap_audit


def make_persist_node(db: Session):
    def _node_persist(state: SOWState) -> SOWState:
        project = db.get(Project, state["project_id"])
        if project is None:
            raise ValueError(f"Project {state['project_id']} disappeared mid-pipeline")
        doc = state["parsed_doc"]
        _, chunks_by_key = persist_document(
            db, project, doc, state["file_path"], state["parsing_status"]
        )
        extraction = state["extraction"]
        fabricated = invented_citations(extraction, chunks_by_key)
        tasks, extraction_warnings = persist_extraction(db, project, extraction, chunks_by_key)
        # The engine's audit is authoritative over whatever the extraction pass
        # happened to volunteer, so its assumptions replace those.
        assumptions = persist_assumptions(db, project, state.get("assumptions", []))
        questions = persist_questions(db, project, state.get("questions", []))
        # Straight after the tasks exist, so the plan arrives already staffed.
        # Only propagates what somebody has already said about who does what —
        # see app/graph/staffing.py for why it refuses to break a tie.
        staffing_notes = autostaff(db, project.id)
        info = extraction.project_info
        project.status = ProjectStatus.AWAITING_APPROVAL
        project.name = info.project_name or project.name
        project.merchant_name = info.merchant_name or ""
        project.start_date = info.start_date
        project.go_live_date = info.go_live_date or info.end_date
        db.commit()
        warnings = list(extraction_warnings) + staffing_notes
        if fabricated:
            warnings.append(
                f"dropped {len(fabricated)} citation(s) not present in the SOW: {fabricated[:5]}"
            )
        return {
            "task_count": len(tasks),
            "assumption_count": len(assumptions),
            "question_count": len(questions),
            "warnings": warnings,
        }

    return _node_persist


def make_schedule_node(db: Session):
    """Date the plan and find its critical path, after tasks exist to schedule."""

    def _node_schedule(state: SOWState) -> SOWState:
        project_id = state["project_id"]
        info = state["extraction"].project_info
        # A project start is a local calendar date, not an instant.
        start = info.start_date or date.today()  # noqa: DTZ011
        deadline = resolve_deadline(db, project_id)
        schedule, warnings = schedule_project(db, project_id, start, deadline)
        return {
            "schedule": schedule,
            "critical_path": [t.task_id for t in schedule.critical_path],
            "warnings": warnings,
        }

    return _node_schedule


def make_grounding_node(client: GeminiClient, db: Session, use_retrieval: bool = True):
    """Judge each task's claims against the SOW, then run the validation stages."""

    def _node_grounding(state: SOWState) -> SOWState:
        project_id = state["project_id"]
        doc = state["parsed_doc"]
        valid_keys = {doc.chunk_key(s, c) for s, c in doc.iter_chunks()}

        retriever = None
        if use_retrieval:
            # Index the chunks so claims that cite nothing can still find evidence.
            try:
                index_document(project_id, doc)
                retriever = search
            except Exception as exc:  # noqa: BLE001 - retrieval is an aid, not a gate
                logger.warning("Chroma indexing failed, grounding on citations only: %s", exc)

        tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project_id).all()
        groundings = ground_project(client, db, project_id, tasks, valid_keys, retriever)
        persist_grounding(db, db.get(Project, project_id), groundings)

        report = run_validation(
            db, project_id, tasks, groundings, resolve_deadline(db, project_id)
        )
        warnings = [f"validation {s.stage}: {s.detail}" for s in report.failures]

        project = db.get(Project, project_id)
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task.team] = counts.get(task.team, 0) + 1
        notify_review_ready(
            project_name=project.name if project else project_id,
            task_counts=counts,
            assumption_count=db.query(Assumption)
            .filter(Assumption.project_id == project_id)
            .count(),
            grounding_score=project_grounding_score(groundings),
            critical_path_length=len(state.get("critical_path", [])),
            failed_stages=[s.stage for s in report.failures],
        )
        return {
            "groundings": groundings,
            "grounding_score": project_grounding_score(groundings),
            "validation": report,
            "warnings": warnings,
        }

    return _node_grounding


def _render_requirements(requirements: list[ProjectRequirement]) -> str:
    return "\n".join(
        f"[{r.requirement_key}] ({r.team}) {r.title}"
        + (f"\n    {r.description}" if r.description else "")
        for r in requirements
    )


def _render_technical_tasks(tasks: list[ProjectTask]) -> str:
    """The tasks an engineer story may be written for, with their owners.

    The role is shown but not asked for back: it tells the model who the story
    is addressed to so the capability is written at the right level, while the
    actor itself is taken from the task in code.
    """
    return "\n".join(
        f"[{t.id}] {t.title} — owned by {t.assignee_role or 'the technical lead'}"
        + (f"\n    {t.description}" if t.description else "")
        for t in tasks
    )


def _render_details(details: list[ProjectDetail]) -> str:
    """The closed list technical notes and thresholds may be built from.

    Everything an engineer story is allowed to say about interfaces, protocols
    and environments has to come from here. Without it the model writes the
    stack it expects to see — plausible, specific, and belonging to no document
    anyone signed.
    """
    if not details:
        return "(the SOW stated no technical details)"
    return "\n".join(
        f"- {d.name}" + (f": {d.description}" if d.description else "") for d in details
    )


def _render_working_values(findings: dict) -> str:
    """The values the plan runs on, each marked with where it came from.

    The model needs them to write a runnable test; marking their status is what
    lets it declare honestly which ones a case depends on.
    """
    lines = []
    for spec in PLANNING_FIELDS:
        finding = findings.get(spec.key)
        if finding is not None and finding.status != SourceStatus.ASSUMED and finding.value:
            lines.append(f"- {spec.key}: {finding.value} (stated in the SOW)")
        else:
            hint = getattr(finding, "hint", "") if finding is not None else ""
            lines.append(f"- {spec.key}: {hint or spec.default} (assumed, not in the SOW)")
    return "\n".join(lines)


def make_artifacts_node(client: GeminiClient, db: Session):
    """Derive user stories, acceptance criteria and test cases.

    Runs last, and swallows an LLM failure into a warning rather than raising.
    A finished, scheduled, grounded plan must not be thrown away because an
    additional call ran out of quota — the derived layer is an enrichment, not
    the deliverable.
    """

    def _node_artifacts(state: SOWState) -> SOWState:
        project_id = state["project_id"]
        project = db.get(Project, project_id)
        requirements = (
            db.query(ProjectRequirement)
            .filter(ProjectRequirement.project_id == project_id)
            .all()
        )
        if not requirements or project is None:
            return {"warnings": ["no requirements to derive user stories from"]}

        technical_tasks = (
            db.query(ProjectTask)
            .filter(ProjectTask.project_id == project_id, ProjectTask.team == str(Team.TECHNICAL))
            .order_by(ProjectTask.title)
            .all()
        )
        technical_details = (
            db.query(ProjectDetail)
            .filter(
                ProjectDetail.project_id == project_id,
                ProjectDetail.team == str(Team.TECHNICAL),
            )
            .all()
        )

        findings = state.get("gap_findings", {})
        assumed_fields = {
            key
            for key, finding in findings.items()
            if getattr(finding, "status", None) == SourceStatus.ASSUMED
        }

        try:
            artifacts = client.generate_structured(
                prompt=TECHNICAL_ARTIFACTS,
                schema=TechnicalArtifacts,
                db=db,
                project_id=project_id,
                graph_node="technical_artifacts",
                requirements=_render_requirements(requirements),
                technical_tasks=_render_technical_tasks(technical_tasks),
                project_details=_render_details(technical_details),
                working_values=_render_working_values(findings),
            )
        except LLMError as exc:
            logger.warning("Derived artifacts skipped: %s", exc)
            return {"warnings": [f"user stories and test cases were not generated: {exc}"]}

        stories, warnings = persist_artifacts(
            db,
            project,
            artifacts,
            {r.requirement_key: r for r in requirements},
            assumed_fields,
            tasks={t.id: t for t in technical_tasks},
            details=technical_details,
        )
        if technical_tasks and not getattr(artifacts, "engineer_stories", []):
            warnings.append(
                f"{len(technical_tasks)} technical task(s) got no engineer story, so "
                "their cards carry no build detail"
            )
        db.commit()

        stage = validate_derived_artifacts(db, project_id)
        record_stage(db, project_id, stage)
        if not stage.passed:
            warnings.append(f"validation {stage.stage}: {stage.detail}")

        return {
            "story_count": len(stories),
            "test_case_count": sum(len(s.test_cases) for s in stories),
            "warnings": warnings,
        }

    return _node_artifacts


def build_graph(client: GeminiClient, db: Session, use_retrieval: bool = True):
    graph = StateGraph(SOWState)
    graph.add_node("parse", _node_parse)
    graph.add_node("validate_parsing", _node_validate_parsing)
    graph.add_node("parsing_failed", _node_parsing_failed)
    graph.add_node("extract", make_extract_node(client, db))
    graph.add_node("gap_audit", make_gap_audit_node(client, db))
    graph.add_node("schedule", make_schedule_node(db))
    graph.add_node("grounding", make_grounding_node(client, db, use_retrieval))
    graph.add_node("persist", make_persist_node(db))
    graph.add_node("artifacts", make_artifacts_node(client, db))

    graph.set_entry_point("parse")
    graph.add_edge("parse", "validate_parsing")
    # Extraction and the gap audit both read the parsed document and neither
    # depends on the other, so they fan out and rejoin at persistence.
    graph.add_conditional_edges(
        "validate_parsing",
        _route_after_parsing,
        ["extract", "gap_audit", "parsing_failed"],
    )
    graph.add_edge("parsing_failed", END)
    graph.add_edge("extract", "persist")
    graph.add_edge("gap_audit", "persist")
    # Scheduling needs the tasks and their dependency edges to exist first, and
    # the timeline validation stage needs the dates scheduling produces.
    graph.add_edge("persist", "schedule")
    graph.add_edge("schedule", "grounding")
    # Derived artifacts come last: they enrich a plan that is already
    # complete, so a failure here costs the enrichment and nothing else.
    graph.add_edge("grounding", "artifacts")
    graph.add_edge("artifacts", END)
    return graph.compile()


def run_sow_pipeline(
    db: Session,
    project_id: str,
    file_path: str,
    doc_key: str,
    client: GeminiClient | None = None,
    use_retrieval: bool = True,
) -> SOWState:
    client = client or GeminiClient()
    graph = build_graph(client, db, use_retrieval)
    return graph.invoke(
        {"project_id": project_id, "file_path": file_path, "doc_key": doc_key, "warnings": []}
    )
