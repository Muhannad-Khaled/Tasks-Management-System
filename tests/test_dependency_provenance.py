"""An arrow between two tasks has to say where it came from.

Dependencies were the last claim in the plan carrying no recorded source, and
the most consequential one: a task's citation explains a single card, but an
arrow moves every date after it. These tests hold the line at each place the
arrow travels — the extraction shape, persistence, validation, the API, and
the board.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.graph.persistence import persist_document, persist_extraction
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.main import app
from app.models import Project, ProjectTask, TaskDependency
from app.schemas.enums import ProjectStatus
from app.schemas.sow import ExtractedDependency
from app.validation.pipeline import validate_dependencies
from tests.factories import StubLLM

CORPUS = Path(__file__).parent.parent / "data" / "sample_sows"
SOW = CORPUS / "sow_a_cairomart.pdf"


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _project(db, name: str) -> Project:
    project = Project(name=name, status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    return project


def _run(db, name: str = "Provenance") -> Project:
    """A project through the real pipeline, with the stub's extraction."""
    doc = parse_document(SOW, "SOW-001")
    citations = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()][:2]
    project = _project(db, name)
    run_sow_pipeline(db, project.id, str(SOW), "SOW-001", client=StubLLM(citations=citations))
    return project


def _persist_once(db, name: str, depends_on: list[ExtractedDependency]):
    """Persist the stub extraction once, with T-002's arrows replaced.

    Deliberately not layered on top of a pipeline run: persisting twice would
    write a second copy of every task, and the assertions below identify tasks
    by title.
    """
    project = _project(db, name)
    doc = parse_document(SOW, "SOW-001")
    _, chunks_by_key = persist_document(db, project, doc, str(SOW), "ok")
    stub = StubLLM(citations=list(chunks_by_key)[:2])
    extraction = stub._extraction()
    extraction.tasks[1].depends_on = depends_on
    _, warnings = persist_extraction(db, project, extraction, chunks_by_key)
    return project, warnings


def _task(db, project: Project, title_starts: str) -> ProjectTask:
    return (
        db.query(ProjectTask)
        .filter(ProjectTask.project_id == project.id, ProjectTask.title.like(f"{title_starts}%"))
        .one()
    )


def _arrow(db, project: Project, title_starts: str) -> TaskDependency:
    task = _task(db, project, title_starts)
    assert task.blockers, f"{task.title!r} was expected to wait on something"
    return task.blockers[0]


# --- the shape the model may answer in --------------------------------------


def test_a_bare_task_id_is_still_accepted():
    """Structured output is not obliged to follow a schema change.

    An arrow with unrecorded provenance is worth far more than an extraction
    that fails outright over a shape, so the older answer still parses.
    """
    link = ExtractedDependency.model_validate("T-001")

    assert link.depends_on_id == "T-001"
    assert str(link.source_status) == "inferred"
    assert link.source_chunk_keys == []


def test_provenance_survives_the_round_trip(db):
    """From the extraction, through persistence, onto the row."""
    project = _run(db)

    stated = _arrow(db, project, "Develop POS API")
    judged = _arrow(db, project, "Configure merchant")

    assert stated.source_status == "explicit"
    assert stated.source_chunk_keys, "an explicit arrow must keep its citation"
    assert "contract is signed" in stated.rationale
    assert judged.source_status == "assumed"
    assert judged.source_chunk_keys == ""


# --- what persistence refuses to write --------------------------------------


def test_an_explicit_arrow_with_no_citation_is_demoted_not_trusted(db):
    """The one status a PM would not think to question.

    "The SOW ordered this" is the strongest thing an arrow can claim. Left
    standing on nothing, it is a constraint the system invented and dressed up
    as the client's own.
    """
    project, warnings = _persist_once(
        db,
        "Demoted",
        [ExtractedDependency(depends_on_id="T-001", source_status="explicit")],
    )

    assert _arrow(db, project, "Develop POS API").source_status == "inferred"
    assert any("called explicit with no citation" in w for w in warnings), warnings


def test_a_citation_the_document_does_not_contain_is_dropped(db):
    """The rule tasks already live under, now applied to arrows."""
    project, warnings = _persist_once(
        db,
        "Invented",
        [
            ExtractedDependency(
                depends_on_id="T-001",
                source_status="explicit",
                source_chunk_keys=["SOW-999-S01-C01"],
            )
        ],
    )

    arrow = _arrow(db, project, "Develop POS API")
    assert arrow.source_chunk_keys == ""
    assert arrow.source_status == "inferred"
    assert any("called explicit with no citation" in w for w in warnings), warnings


def test_an_arrow_to_a_task_that_does_not_exist_is_dropped(db):
    project, _ = _persist_once(
        db, "Dangling", [ExtractedDependency(depends_on_id="T-404", source_status="inferred")]
    )

    assert _task(db, project, "Develop POS API").blockers == []


def test_the_same_arrow_twice_is_stored_once(db):
    """Two rows with one primary key would fail the insert outright."""
    project, _ = _persist_once(
        db,
        "Duplicate",
        [
            ExtractedDependency(depends_on_id="T-001", source_status="inferred"),
            ExtractedDependency(depends_on_id="T-001", source_status="assumed"),
        ],
    )

    blockers = _task(db, project, "Develop POS API").blockers
    assert len(blockers) == 1
    # The first wins: a later contradiction is not the more authoritative one.
    assert blockers[0].source_status == "inferred"


# --- what validation catches ------------------------------------------------


def _bare(db, project_id: str, title: str) -> ProjectTask:
    task = ProjectTask(project_id=project_id, title=title, team="technical")
    db.add(task)
    db.flush()
    return task


def test_validation_fails_an_arrow_claiming_the_sow_without_citing_it(db):
    """Reachable even though persistence demotes: rows outlive their writer."""
    project = _project(db, "Bare")
    upstream = _bare(db, project.id, "Sign the contract")
    downstream = _bare(db, project.id, "Build the API")
    downstream.blockers.append(TaskDependency(depends_on_id=upstream.id, source_status="explicit"))
    db.commit()

    result = validate_dependencies([upstream, downstream])

    assert not result.passed
    assert "cites nothing" in result.detail
    assert downstream.id in result.offending_task_ids


def test_a_clean_pass_still_says_how_much_of_the_ordering_was_judgement(db):
    """Passing silently would hide the thing worth knowing."""
    project = _run(db, "Counted")
    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project.id).all()

    result = validate_dependencies(tasks)

    assert result.passed
    assert "2 dependency(s)" in result.detail, result.detail
    # One of the two is the stub's assumed arrow; the explicit one is not counted.
    assert "1 not stated by the SOW" in result.detail, result.detail


# --- what the PM sees -------------------------------------------------------


def test_the_api_hands_back_each_arrow_with_its_source(client, db):
    project = _run(db, "Served")

    tasks = client.get(f"/projects/{project.id}/tasks").json()
    blocked = next(t for t in tasks if t["title"].startswith("Develop POS API"))

    arrow = blocked["depends_on"][0]
    assert arrow["title"] == "Finalize merchant contract"
    assert arrow["source_status"] == "explicit"
    assert arrow["source_chunk_keys"]
    assert arrow["rationale"]
