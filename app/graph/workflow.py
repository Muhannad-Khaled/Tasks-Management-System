"""LangGraph SOW processing workflow (M1 slice).

    parse -> validate_parsing -> extract -> persist

The parsing gate is a real branch, not a formality: if parsing failed the graph
stops instead of letting the model reason over garbage (brief section 6).
Grounding, per-team branches, dependency/timeline nodes and the HITL interrupt
attach to this same graph in later milestones.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.graph.persistence import invented_citations, persist_document, persist_extraction
from app.ingestion.parser import parse_document
from app.ingestion.validation import ParsingStatus, validate_parsed_document
from app.llm.client import GeminiClient, build_chunked_text
from app.llm.prompts import SOW_EXTRACTION
from app.models import Project
from app.schemas.enums import ProjectStatus
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
    task_count: int
    warnings: Annotated[list[str], lambda a, b: a + b]
    error: str


def _node_parse(state: SOWState) -> SOWState:
    doc = parse_document(Path(state["file_path"]), state["doc_key"])
    return {"parsed_doc": doc}


def _node_validate_parsing(state: SOWState) -> SOWState:
    report = validate_parsed_document(state["parsed_doc"])
    warnings = [f"{c.name}: {c.detail}" for c in report.failures]
    return {"parsing_status": str(report.status), "parsing_report": report, "warnings": warnings}


def _route_after_parsing(state: SOWState) -> str:
    return "failed" if state["parsing_status"] == ParsingStatus.FAILED else "ok"


def _node_parsing_failed(state: SOWState) -> SOWState:
    return {"error": "Parsing failed; document needs human review before extraction."}


def make_extract_node(client: GeminiClient, db: Session):
    def _node_extract(state: SOWState) -> SOWState:
        doc = state["parsed_doc"]
        extraction = client.generate_structured(
            prompt=SOW_EXTRACTION,
            schema=StructuredSOW,
            db=db,
            project_id=state["project_id"],
            graph_node="sow_extraction",
            doc_key=doc.doc_key,
            chunked_text=build_chunked_text(doc),
        )
        return {"extraction": extraction}

    return _node_extract


def make_persist_node(db: Session):
    def _node_persist(state: SOWState) -> SOWState:
        project = db.get(Project, state["project_id"])
        doc = state["parsed_doc"]
        sow_doc, chunks_by_key = persist_document(
            db, project, doc, state["file_path"], state["parsing_status"]
        )
        extraction = state["extraction"]
        fabricated = invented_citations(extraction, chunks_by_key)
        tasks = persist_extraction(db, project, extraction, chunks_by_key)
        project.status = ProjectStatus.AWAITING_APPROVAL
        project.name = extraction.project_info.project_name or project.name
        db.commit()
        warnings = (
            [f"dropped {len(fabricated)} citation(s) not present in the SOW: {fabricated[:5]}"]
            if fabricated
            else []
        )
        return {"task_count": len(tasks), "warnings": warnings}

    return _node_persist


def build_graph(client: GeminiClient, db: Session):
    graph = StateGraph(SOWState)
    graph.add_node("parse", _node_parse)
    graph.add_node("validate_parsing", _node_validate_parsing)
    graph.add_node("parsing_failed", _node_parsing_failed)
    graph.add_node("extract", make_extract_node(client, db))
    graph.add_node("persist", make_persist_node(db))

    graph.set_entry_point("parse")
    graph.add_edge("parse", "validate_parsing")
    graph.add_conditional_edges(
        "validate_parsing",
        _route_after_parsing,
        {"ok": "extract", "failed": "parsing_failed"},
    )
    graph.add_edge("parsing_failed", END)
    graph.add_edge("extract", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


def run_sow_pipeline(
    db: Session, project_id: str, file_path: str, doc_key: str, client: GeminiClient | None = None
) -> SOWState:
    client = client or GeminiClient()
    graph = build_graph(client, db)
    return graph.invoke(
        {"project_id": project_id, "file_path": file_path, "doc_key": doc_key, "warnings": []}
    )
