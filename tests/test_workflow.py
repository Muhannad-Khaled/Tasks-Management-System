"""End-to-end pipeline tests with a stubbed LLM.

These cover the guarantees the platform is built on: the parsing gate stops bad
documents before extraction, citations resolve to real SOW text, and fabricated
citations never reach the database.
"""

from pathlib import Path

from app.graph.workflow import build_graph, run_sow_pipeline
from app.ingestion.parser import parse_document
from app.models import Assumption, Project, ProjectTask, SOWChunk
from app.schemas.enums import ProjectStatus

from tests.factories import CORPUS, StubLLM


def _new_project(db, name="Test Project") -> Project:
    project = Project(name=name, status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    return project


def _real_chunk_keys(slug: str, doc_key: str, limit: int = 2) -> list[str]:
    doc = parse_document(CORPUS / f"{slug}.pdf", doc_key)
    return [doc.chunk_key(s, c) for s, c in doc.iter_chunks()][:limit]


def test_pipeline_produces_tasks_linked_to_real_evidence(db):
    project = _new_project(db)
    citations = _real_chunk_keys("sow_a_cairomart", "SOW-001")
    state = run_sow_pipeline(
        db,
        project.id,
        str(CORPUS / "sow_a_cairomart.pdf"),
        "SOW-001",
        client=StubLLM(citations=citations),
    )

    assert state["parsing_status"] == "valid"
    assert state["task_count"] == 3

    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project.id).all()
    assert {t.team for t in tasks} == {"commercial", "technical", "operations"}

    for task in tasks:
        keys = [k for k in task.source_chunk_keys.split(",") if k]
        assert keys, f"{task.title} has no evidence"
        for key in keys:
            assert db.query(SOWChunk).filter(SOWChunk.chunk_key == key).first() is not None
        assert task.source_sow_section_id, "task must resolve to a SOW section"


def test_cross_team_dependencies_are_persisted(db):
    project = _new_project(db)
    run_sow_pipeline(
        db,
        project.id,
        str(CORPUS / "sow_a_cairomart.pdf"),
        "SOW-001",
        client=StubLLM(citations=_real_chunk_keys("sow_a_cairomart", "SOW-001")),
    )
    tasks = {t.title: t for t in db.query(ProjectTask).filter(ProjectTask.project_id == project.id)}
    technical = tasks["Develop POS API integration"]
    operations = tasks["Configure merchant and offers"]
    assert [d.title for d in technical.depends_on] == ["Finalize merchant contract"]
    assert [d.title for d in operations.depends_on] == ["Develop POS API integration"]


def test_invented_citations_are_dropped_and_reported(db):
    # The model citing a chunk that does not exist is the exact hallucination
    # this platform must catch, so it is never persisted as evidence.
    project = _new_project(db)
    real = _real_chunk_keys("sow_a_cairomart", "SOW-001", limit=1)
    state = run_sow_pipeline(
        db,
        project.id,
        str(CORPUS / "sow_a_cairomart.pdf"),
        "SOW-001",
        client=StubLLM(citations=real + ["SOW-001-S99-C99"]),
    )

    assert any("dropped" in w for w in state["warnings"])
    for task in db.query(ProjectTask).filter(ProjectTask.project_id == project.id):
        assert "S99" not in task.source_chunk_keys


def test_parsing_failure_stops_before_extraction(db, tmp_path: Path):
    empty = tmp_path / "broken.txt"
    empty.write_text("too short", encoding="utf-8")
    project = _new_project(db)
    stub = StubLLM()

    state = run_sow_pipeline(db, project.id, str(empty), "SOW-999", client=stub)

    assert state["parsing_status"] == "parsing_failed"
    assert state["error"]
    assert stub.calls == [], "the LLM must not be called on a failed parse"
    assert db.query(ProjectTask).filter(ProjectTask.project_id == project.id).count() == 0


def test_assumptions_are_recorded_separately_from_tasks(db):
    project = _new_project(db)
    run_sow_pipeline(
        db,
        project.id,
        str(CORPUS / "sow_b_quickbite.pdf"),
        "SOW-002",
        client=StubLLM(citations=_real_chunk_keys("sow_b_quickbite", "SOW-002")),
    )
    assumptions = db.query(Assumption).filter(Assumption.project_id == project.id).all()
    assert len(assumptions) == 1
    assert assumptions[0].category == "TEAM_SIZE"
    assert assumptions[0].reason
    assert assumptions[0].status == "assumed"


def test_project_moves_to_awaiting_approval_not_straight_to_sync(db):
    project = _new_project(db)
    run_sow_pipeline(
        db,
        project.id,
        str(CORPUS / "sow_a_cairomart.pdf"),
        "SOW-001",
        client=StubLLM(citations=_real_chunk_keys("sow_a_cairomart", "SOW-001")),
    )
    db.refresh(project)
    assert project.status == ProjectStatus.AWAITING_APPROVAL


def test_graph_compiles_with_expected_nodes():
    graph = build_graph(StubLLM(), db=None)
    nodes = set(graph.get_graph().nodes)
    assert {"parse", "validate_parsing", "extract", "persist", "parsing_failed"} <= nodes
