"""API-level tests for the approval gate and evidence lookup."""

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.main import app
from app.models import Project
from app.schemas.enums import ProjectStatus
from app.schemas.fields import PLANNING_FIELDS
from tests.factories import CORPUS, StubLLM


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded_project(db):
    sow = CORPUS / "sow_a_cairomart.pdf"
    doc = parse_document(sow, "SOW-001")
    citations = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()][:2]
    project = Project(name="Seeded", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    run_sow_pipeline(db, project.id, str(sow), "SOW-001", client=StubLLM(citations=citations))
    return project


def test_tasks_expose_their_evidence_keys(client, seeded_project):
    tasks = client.get(f"/projects/{seeded_project.id}/tasks").json()
    assert len(tasks) == 3
    assert all(t["source_chunk_keys"] for t in tasks)


def test_evidence_endpoint_returns_the_cited_sow_text(client, seeded_project):
    task = client.get(f"/projects/{seeded_project.id}/tasks").json()[0]
    key = task["source_chunk_keys"][0]
    evidence = client.get(f"/projects/{seeded_project.id}/evidence/{key}").json()
    assert evidence["chunk_key"] == key
    assert evidence["text"].strip()


def test_unknown_evidence_key_is_404(client, seeded_project):
    resp = client.get(f"/projects/{seeded_project.id}/evidence/SOW-001-S99-C99")
    assert resp.status_code == 404


def test_push_is_blocked_until_the_pm_approves(client, seeded_project):
    # The approval gate is the point of the HITL design: no task may reach a
    # task manager on the AI's say-so alone.
    resp = client.post(f"/projects/{seeded_project.id}/push")
    assert resp.status_code == 409
    assert "approved" in resp.json()["detail"].lower()


def test_approve_moves_project_to_approved(client, seeded_project, db):
    resp = client.post(f"/projects/{seeded_project.id}/approve")
    assert resp.status_code == 200
    db.refresh(seeded_project)
    assert seeded_project.status == ProjectStatus.APPROVED


def test_upload_rejects_unsupported_formats(client):
    resp = client.post("/projects/upload", files={"file": ("sow.rtf", b"data")})
    assert resp.status_code == 400


def test_failed_extraction_leaves_no_orphan_project(client, db, monkeypatch):
    # A shell project that can never be approved or pushed is worse than none.
    from app.api import projects as projects_api
    from app.llm.client import LLMError

    def _boom(*args, **kwargs):
        raise LLMError("no api key")

    monkeypatch.setattr(projects_api, "run_sow_pipeline", _boom)
    before = db.query(Project).count()
    resp = client.post(
        "/projects/upload",
        files={"file": ("sow_a_cairomart.pdf", (CORPUS / "sow_a_cairomart.pdf").read_bytes())},
    )
    assert resp.status_code == 502
    assert db.query(Project).count() == before


def test_timeline_reports_a_cross_team_critical_path(client, seeded_project):
    timeline = client.get(f"/projects/{seeded_project.id}/timeline").json()
    assert timeline["duration_working_days"] > 0
    assert timeline["project_start"] and timeline["project_end"]
    # The stub's tasks form a strict commercial -> technical -> operations chain,
    # so every one of them is critical and the path spans all three teams.
    path = timeline["critical_path"]
    assert [t["team"] for t in path] == ["commercial", "technical", "operations"]
    assert all(t["start_date"] and t["due_date"] for t in path)


def test_timeline_404s_for_an_unknown_project(client):
    assert client.get("/projects/does-not-exist/timeline").status_code == 404


def test_tasks_carry_their_scheduled_dates(client, seeded_project):
    tasks = client.get(f"/projects/{seeded_project.id}/tasks").json()
    assert all(t["start_date"] and t["due_date"] for t in tasks)


def test_grounding_endpoint_reports_scores_and_names_the_failures(client, seeded_project):
    data = client.get(f"/projects/{seeded_project.id}/grounding").json()
    # The stub supports one claim per task and fails the quantitative one.
    assert data["overall_score"] == pytest.approx(0.5)
    assert data["total_claims"] == 6
    assert set(data["by_team"]) == {"commercial", "technical", "operations"}
    assert len(data["failures"]) == 3
    assert all(f["claim"] and f["reasoning"] for f in data["failures"])
    assert {s["stage"] for s in data["validation_stages"]} == {
        "schema",
        "grounding",
        "evidence",
        "business_rules",
        "dependencies",
        "timeline",
    }


def test_grounding_endpoint_404s_for_an_unknown_project(client):
    assert client.get("/projects/nope/grounding").status_code == 404


def test_tasks_expose_their_grounding_score(client, seeded_project):
    tasks = client.get(f"/projects/{seeded_project.id}/tasks").json()
    assert all(t["grounding_score"] is not None for t in tasks)
    assert all(t["validation_status"] in {"accept", "review", "reject"} for t in tasks)


def test_project_list_reports_task_and_assumption_counts(client, seeded_project):
    projects = client.get("/projects").json()
    row = next(p for p in projects if p["id"] == seeded_project.id)
    assert row["task_count"] == 3
    assert row["assumption_count"] == len(PLANNING_FIELDS) - 1
