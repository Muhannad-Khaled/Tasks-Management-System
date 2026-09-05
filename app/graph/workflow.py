"""LangGraph SOW processing workflow.

                          /-> extract   -\\
    parse -> validate_parsing            -> persist
                          \\-> gap_audit -/

The parsing gate is a real branch, not a formality: if parsing failed the graph
stops instead of letting the model reason over garbage (brief section 6).

Extraction and the gap audit are independent reads of the same document, so
they run concurrently. Grounding, dependency/timeline nodes and the HITL
interrupt attach to this same graph in later milestones.
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
from app.graph.assumptions import build_assumptions, render_field_list, validate_findings
from app.graph.persistence import (
    invented_citations,
    persist_assumptions,
    persist_document,
    persist_extraction,
)
from app.ingestion.parser import parse_document
from app.ingestion.validation import ParsingStatus, validate_parsed_document
from app.llm.client import GeminiClient, build_chunked_text
from app.llm.prompts import GAP_DETECTION, SOW_EXTRACTION
from app.models import Project
from app.planning.service import resolve_deadline, schedule_project
from app.schemas.enums import ProjectStatus
from app.schemas.fields import PLANNING_FIELDS
from app.schemas.gaps import GapReport
from app.schemas.sow import StructuredSOW

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
    schedule: Any
    critical_path: list[str]
    task_count: int
    assumption_count: int
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
    def _node_extract(state: SOWState) -> SOWState:
        doc = state["parsed_doc"]
        with _node_session(db) as session:
            extraction = client.generate_structured(
                prompt=SOW_EXTRACTION,
                schema=StructuredSOW,
                db=session,
                project_id=state["project_id"],
                graph_node="sow_extraction",
                doc_key=doc.doc_key,
                chunked_text=build_chunked_text(doc),
            )
        return {"extraction": extraction}

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
        assumptions = build_assumptions(findings)
        return {
            "gap_findings": findings,
            "assumptions": assumptions,
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
        tasks = persist_extraction(db, project, extraction, chunks_by_key)
        # The engine's audit is authoritative over whatever the extraction pass
        # happened to volunteer, so its assumptions replace those.
        assumptions = persist_assumptions(db, project, state.get("assumptions", []))
        info = extraction.project_info
        project.status = ProjectStatus.AWAITING_APPROVAL
        project.name = info.project_name or project.name
        project.merchant_name = info.merchant_name or ""
        project.start_date = info.start_date
        project.go_live_date = info.go_live_date or info.end_date
        db.commit()
        warnings = (
            [f"dropped {len(fabricated)} citation(s) not present in the SOW: {fabricated[:5]}"]
            if fabricated
            else []
        )
        return {
            "task_count": len(tasks),
            "assumption_count": len(assumptions),
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


def build_graph(client: GeminiClient, db: Session):
    graph = StateGraph(SOWState)
    graph.add_node("parse", _node_parse)
    graph.add_node("validate_parsing", _node_validate_parsing)
    graph.add_node("parsing_failed", _node_parsing_failed)
    graph.add_node("extract", make_extract_node(client, db))
    graph.add_node("gap_audit", make_gap_audit_node(client, db))
    graph.add_node("schedule", make_schedule_node(db))
    graph.add_node("persist", make_persist_node(db))

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
    # Scheduling needs the tasks and their dependency edges to exist first.
    graph.add_edge("persist", "schedule")
    graph.add_edge("schedule", END)
    return graph.compile()


def run_sow_pipeline(
    db: Session, project_id: str, file_path: str, doc_key: str, client: GeminiClient | None = None
) -> SOWState:
    client = client or GeminiClient()
    graph = build_graph(client, db)
    return graph.invoke(
        {"project_id": project_id, "file_path": file_path, "doc_key": doc_key, "warnings": []}
    )
