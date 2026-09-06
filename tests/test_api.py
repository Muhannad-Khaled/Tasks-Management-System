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


def test_upload_never_merges_into_an_existing_project(client, db, seeded_project, monkeypatch):
    # The uploader sits near the selected project in the UI, so it is natural to
    # assume an upload adds to it. It must not: one SOW is one project, and
    # mixing two merchants' work into one plan would be silent corruption.
    from app.api import projects as projects_api
    from app.llm.client import LLMError

    tasks_before = client.get(f"/projects/{seeded_project.id}/tasks").json()
    monkeypatch.setattr(
        projects_api, "run_sow_pipeline", lambda *a, **k: (_ for _ in ()).throw(LLMError("stop"))
    )
    client.post(
        "/projects/upload",
        files={"file": ("sow_b_quickbite.pdf", (CORPUS / "sow_b_quickbite.pdf").read_bytes())},
    )

    after = client.get(f"/projects/{seeded_project.id}/tasks").json()
    assert after == tasks_before, "an upload changed a different project"


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
        # Checks the dates the SOW commits to between kickoff and go-live.
        "milestones",
        # Recorded after run_validation, by the node that derives the artifacts.
        "derived_artifacts",
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


def test_the_timeline_says_how_much_of_it_was_actually_estimated(client, seeded_project):
    # Dates built on a one-day default read exactly like dates built on real
    # estimates. The schedule has to disclose which it is.
    timeline = client.get(f"/projects/{seeded_project.id}/timeline").json()

    assert "estimate_coverage" in timeline
    assert timeline["estimate_coverage"] == 1.0, "the stub estimates every task"
    assert timeline["assumed_durations"] == []


def test_the_timeline_names_the_tasks_running_on_the_default(client, seeded_project, db):
    from app.models import ProjectTask

    task = db.query(ProjectTask).filter(ProjectTask.project_id == seeded_project.id).first()
    title = task.title
    task.estimated_hours = None
    db.commit()

    timeline = client.get(f"/projects/{seeded_project.id}/timeline").json()

    assert timeline["estimate_coverage"] < 1.0
    assert title in [t["title"] for t in timeline["assumed_durations"]]


def test_the_timeline_calibrates_estimates_against_the_sow_windows(client, seeded_project):
    timeline = client.get(f"/projects/{seeded_project.id}/timeline").json()

    assert "calibration" in timeline
    calibration = timeline["calibration"]
    assert set(calibration) >= {"segments", "allowed_days", "planned_days", "by_step"}


def test_calibration_reports_each_agreed_window_separately(client, seeded_project, db):
    # A whole-project total can look healthy while an individual step is
    # estimated at a third of the time it was given.
    from datetime import date

    from app.models import ProjectMilestone, ProjectTask

    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == seeded_project.id).all()
    # Replace the stub's own milestones so the windows under test are the only ones.
    from app.models import milestone_tasks

    existing = [
        m.id
        for m in db.query(ProjectMilestone.id).filter(
            ProjectMilestone.project_id == seeded_project.id
        )
    ]
    if existing:
        db.execute(milestone_tasks.delete().where(milestone_tasks.c.milestone_id.in_(existing)))
    db.query(ProjectMilestone).filter(
        ProjectMilestone.project_id == seeded_project.id
    ).delete()
    first = ProjectMilestone(
        project_id=seeded_project.id, name="Contract", target_date=date(2026, 10, 2)
    )
    first.tasks.append(tasks[0])
    second = ProjectMilestone(
        project_id=seeded_project.id, name="Integration", target_date=date(2026, 12, 1)
    )
    second.tasks.append(tasks[-1])
    db.add_all([first, second])
    db.commit()

    calibration = client.get(f"/projects/{seeded_project.id}/timeline").json()["calibration"]

    names = [s["name"] for s in calibration["by_step"]]
    assert names == ["Contract", "Integration"]
    integration = calibration["by_step"][1]
    assert integration["after"] == "Contract"
    assert integration["allowed_days"] > integration["planned_days"]
    assert integration["unaccounted_days"] > 0


def test_all_evidence_comes_back_in_one_call(client, seeded_project):
    # The task list fetched one chunk per citation, and Streamlit renders the
    # body of a collapsed expander too, so those ran on every page render.
    tasks = client.get(f"/projects/{seeded_project.id}/tasks").json()
    cited = {k for t in tasks for k in t["source_chunk_keys"]}

    bundle = client.get(f"/projects/{seeded_project.id}/evidence").json()

    assert set(bundle) == cited
    assert all(v["text"].strip() for v in bundle.values())


def test_the_bundle_matches_what_the_single_lookup_returns(client, seeded_project):
    bundle = client.get(f"/projects/{seeded_project.id}/evidence").json()
    key = next(iter(bundle))

    single = client.get(f"/projects/{seeded_project.id}/evidence/{key}").json()

    assert bundle[key] == single


def test_a_project_citing_nothing_gets_an_empty_bundle(client, db):
    from app.models import Project

    empty = Project(name="No citations", status=ProjectStatus.INGESTING)
    db.add(empty)
    db.commit()

    assert client.get(f"/projects/{empty.id}/evidence").json() == {}
