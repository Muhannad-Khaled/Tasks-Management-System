"""An extraction with no tasks is a failure, not an empty answer.

A live NileBank run returned project_info, an 11-role roster, 11 milestones and
26 details, and silently omitted `requirements` and `tasks` altogether. Both
default to an empty list, so the response validated, the pipeline persisted a
project with nothing in it, and the UI reported it in a green box:

    Created a new project with 0 tasks (parsing status: valid).

Every warning underneath it was true and useless — eleven milestones with no
task attached, three roles awaiting a person, no requirements to derive stories
from. The one thing nobody was told is that the run had failed.

A document that parsed into 65 chunks has work in it. An empty task list says
something about the answer, never about the SOW.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.graph.workflow import run_sow_pipeline
from app.main import app
from app.models import Project, ProjectTask
from app.schemas.enums import ProjectStatus
from app.schemas.sow import StructuredSOW
from tests.factories import StubLLM

CORPUS = Path(__file__).parent.parent / "data" / "sample_sows"
SOW = CORPUS / "sow_a_cairomart.pdf"


class DroppedSections(StubLLM):
    """Returns the shape the model actually produced.

    `empty_runs` is how many extraction calls come back with the two sections
    missing before a full one arrives; a large number means it never recovers.
    """

    def __init__(self, empty_runs: int, **kwargs):
        super().__init__(**kwargs)
        self.empty_runs = empty_runs
        self.extractions = 0

    def _extraction(self) -> StructuredSOW:
        full = super()._extraction()
        self.extractions += 1
        if self.extractions > self.empty_runs:
            return full
        # Everything except the two sections the model skipped. Keeping the
        # rest is the point: the response looked substantial.
        return StructuredSOW(
            project_info=full.project_info,
            team_roster=full.team_roster,
            milestones=full.milestones,
            details=full.details,
            assumptions=full.assumptions,
        )


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _run(db, stub) -> tuple[Project, dict]:
    project = Project(name="Empty extraction", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    state = run_sow_pipeline(db, project.id, str(SOW), "SOW-001", client=stub)
    return project, state


def test_a_single_lapse_is_retried_and_the_run_survives(db):
    """The same prompt on the same document worked the day before.

    Skipping sections is a lapse, so it costs one more of twenty daily calls
    rather than the whole run.
    """
    stub = DroppedSections(empty_runs=1, citations=[])
    project, state = _run(db, stub)

    assert stub.extractions == 2, "the empty answer should have been challenged once"
    assert state.get("error") is None
    assert state["task_count"] > 0
    assert any("asking once more" in w for w in state.get("warnings", []))


def test_a_run_that_never_recovers_is_reported_as_failed(db):
    stub = DroppedSections(empty_runs=99, citations=[])
    project, state = _run(db, stub)

    assert stub.extractions == 2, "one retry, not an unbounded loop"
    assert state.get("error"), "an empty plan must not pass as a result"
    assert "returned no requirements, tasks" in state["error"]
    assert db.query(ProjectTask).filter_by(project_id=project.id).count() == 0


def test_the_failure_names_the_document_rather_than_blaming_it(db):
    """The reader has to know whether to fix the SOW or re-run.

    "0 tasks" alone reads as a verdict on the document, and sends somebody
    looking for what their SOW is missing when nothing is.
    """
    _, state = _run(db, DroppedSections(empty_runs=99, citations=[]))

    error = state["error"]
    assert "fault in the extraction" in error
    assert "chunks" in error, "say the document had content, so it is not the suspect"
    assert "Upload it again" in error


def test_the_upload_route_reports_it_as_an_error_not_a_success(client, db, monkeypatch):
    """The green box is the actual defect. Everything else was only noise."""
    import app.api.projects as projects_module

    monkeypatch.setattr(
        projects_module,
        "run_sow_pipeline",
        lambda db_, pid, path, key: run_sow_pipeline(
            db_, pid, path, key, client=DroppedSections(empty_runs=99, citations=[])
        ),
    )

    with SOW.open("rb") as fh:
        response = client.post("/projects/upload", files={"file": (SOW.name, fh.read())})

    assert response.status_code == 200
    body = response.json()
    assert body["error"], "an empty plan was presented as a finished one"
    assert body["task_count"] == 0
    assert db.get(Project, body["project_id"]).status == ProjectStatus.PARSING_FAILED
