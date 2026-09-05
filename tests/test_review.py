"""PM review, partial regeneration, and notification formatting.

The guarantee that matters: rejecting one task must not disturb the rest of the
plan. A PM who has already approved eight items should not have to review them
again because the ninth was wrong.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.graph.review import MAX_REGENERATIONS, ReviewError, apply_edit, regenerate_task
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.main import app
from app.models import ClaimRecord, Project, ProjectTask
from app.notifications.discord import format_review_ready, notify_review_ready
from app.schemas.enums import ProjectStatus, ReviewStatus
from app.schemas.sow import ExtractedTask
from tests.factories import CORPUS, StubLLM


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def project(db):
    sow = CORPUS / "sow_a_cairomart.pdf"
    doc = parse_document(sow, "SOW-001")
    citations = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()][:2]
    project = Project(name="Review test", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    run_sow_pipeline(db, project.id, str(sow), "SOW-001", client=StubLLM(citations=citations))
    return project


def _tasks(db, project) -> list[ProjectTask]:
    return db.query(ProjectTask).filter(ProjectTask.project_id == project.id).all()


class RegeneratingStub(StubLLM):
    """Returns a rewritten task, then behaves like StubLLM for grounding."""

    def generate_structured(self, prompt, schema, **kwargs):
        if schema is ExtractedTask:
            self.calls.append({"prompt": prompt.name, **kwargs})
            return ExtractedTask(
                task_id="T-regen",
                team="technical",
                title="Rewritten without the unsupported duration",
                description="Scoped to what the SOW states.",
                priority="high",
                source_status="explicit",
                source_chunk_keys=self.citations,
            )
        return super().generate_structured(prompt, schema, **kwargs)


def test_tasks_start_pending_review(db, project):
    assert all(t.review_status == ReviewStatus.PENDING for t in _tasks(db, project))


def test_approving_one_task_leaves_the_others_pending(client, db, project):
    tasks = _tasks(db, project)
    resp = client.post(f"/projects/{project.id}/tasks/{tasks[0].id}/approve")
    assert resp.status_code == 200

    db.expire_all()
    statuses = {t.id: t.review_status for t in _tasks(db, project)}
    assert statuses[tasks[0].id] == ReviewStatus.APPROVED
    assert all(statuses[t.id] == ReviewStatus.PENDING for t in tasks[1:])


def test_editing_a_task_clears_grounding_that_described_the_old_wording(db, project):
    task = _tasks(db, project)[0]
    assert task.grounding_score is not None

    apply_edit(db, task, title="A title the PM wrote")

    assert task.title == "A title the PM wrote"
    assert task.review_status == ReviewStatus.EDITED
    assert task.grounding_score is None, "a human rewrote the text; the old score is not about it"
    assert db.query(ClaimRecord).filter(ClaimRecord.task_id == task.id).count() == 0


def test_edit_ignores_fields_that_are_not_editable(db, project):
    task = _tasks(db, project)[0]
    original = task.source_chunk_keys
    apply_edit(db, task, source_chunk_keys="SOW-001-S99-C99", title="New")
    assert task.source_chunk_keys == original, "evidence is not the PM's to invent"


def test_regeneration_replaces_only_the_rejected_task(db, project):
    tasks = _tasks(db, project)
    target, others = tasks[0], tasks[1:]
    before = {t.id: (t.title, t.description) for t in others}

    regenerate_task(RegeneratingStub(citations=[]), db, target, "Too vague")

    db.expire_all()
    assert target.title == "Rewritten without the unsupported duration"
    after = {t.id: (t.title, t.description) for t in _tasks(db, project) if t.id in before}
    assert after == before, "regenerating one task changed another"


def test_regeneration_keeps_the_task_id_so_dependencies_survive(db, project):
    tasks = _tasks(db, project)
    dependent = next(t for t in tasks if t.depends_on)
    upstream_id = dependent.depends_on[0].id
    upstream = db.get(ProjectTask, upstream_id)

    regenerate_task(RegeneratingStub(citations=[]), db, upstream, "reword")

    db.expire_all()
    assert [d.id for d in dependent.depends_on] == [upstream_id]


def test_regeneration_re_grounds_the_new_wording(db, project):
    task = _tasks(db, project)[0]
    db.query(ClaimRecord).filter(ClaimRecord.task_id == task.id).delete()
    db.commit()

    regenerate_task(RegeneratingStub(citations=[]), db, task, "reword")

    assert db.query(ClaimRecord).filter(ClaimRecord.task_id == task.id).count() > 0
    assert task.grounding_score is not None
    assert task.regeneration_count == 1


def test_regeneration_stops_after_repeated_attempts(db, project):
    task = _tasks(db, project)[0]
    task.regeneration_count = MAX_REGENERATIONS
    db.commit()
    with pytest.raises(ReviewError, match="already been regenerated"):
        regenerate_task(RegeneratingStub(citations=[]), db, task, "again")


def test_rejecting_without_regeneration_just_marks_the_task(client, db, project):
    task = _tasks(db, project)[0]
    resp = client.post(
        f"/projects/{project.id}/tasks/{task.id}/reject",
        json={"reason": "Not in scope", "regenerate": False},
    )
    assert resp.status_code == 200
    db.expire_all()
    assert task.review_status == ReviewStatus.REJECTED
    assert task.review_note == "Not in scope"


def test_review_state_reports_readiness(client, db, project):
    state = client.get(f"/projects/{project.id}/review").json()
    assert state["total"] == 3
    assert not state["ready"], "nothing has been reviewed yet"

    for task in _tasks(db, project):
        client.post(f"/projects/{project.id}/tasks/{task.id}/approve")
    assert client.get(f"/projects/{project.id}/review").json()["ready"]


def test_review_actions_reject_a_task_from_another_project(client, db, project):
    other = Project(name="Other", status=ProjectStatus.INGESTING)
    db.add(other)
    db.commit()
    task = _tasks(db, project)[0]
    assert client.post(f"/projects/{other.id}/tasks/{task.id}/approve").status_code == 404


def test_audit_endpoint_lists_every_llm_exchange(client, project):
    audit = client.get(f"/projects/{project.id}/audit").json()
    assert audit["total_requests"] >= 0
    assert {s["stage"] for s in audit["validation_stages"]} >= {"schema", "grounding"}
    for request in audit["llm_requests"]:
        assert request["graph_node"] and request["prompt_version"]


# --- notifications -------------------------------------------------------


def test_notification_summarises_the_plan():
    message = format_review_ready(
        project_name="Merchant XYZ",
        task_counts={"commercial": 10, "technical": 21, "operations": 11},
        assumption_count=5,
        grounding_score=0.94,
        critical_path_length=7,
        failed_stages=[],
    )
    assert "Merchant XYZ" in message
    assert "42" in message
    assert "94%" in message
    assert "all stages passed" in message


def test_notification_names_failed_stages():
    message = format_review_ready(
        project_name="P",
        task_counts={},
        assumption_count=0,
        grounding_score=0.4,
        critical_path_length=0,
        failed_stages=["grounding", "timeline"],
    )
    assert "grounding, timeline failed" in message


def test_notification_without_a_webhook_is_a_no_op():
    # A missing webhook must not raise: notifications are never load-bearing.
    assert (
        notify_review_ready(
            webhook_url="",
            project_name="P",
            task_counts={},
            assumption_count=0,
            grounding_score=None,
            critical_path_length=0,
            failed_stages=[],
        )
        is False
    )
