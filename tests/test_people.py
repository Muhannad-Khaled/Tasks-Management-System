"""Names come from a person, and travel to the card with their role."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.main import app
from app.models import Person, Project, ProjectRole, ProjectTask
from app.schemas.enums import ProjectStatus
from app.taskmanager.cards import _board_task, artifacts_for_tasks
from tests.factories import StubLLM

CORPUS = Path(__file__).parent.parent / "data" / "sample_sows"


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
    project = Project(name="Staffing", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    run_sow_pipeline(db, project.id, str(sow), "SOW-001", client=StubLLM(citations=citations))
    return project


def _staffed_role(client, project, db) -> tuple[dict, ProjectTask]:
    """A role the extraction actually put tasks on, with one of those tasks.

    Taking roles[0] skipped these tests whenever the first role alphabetically
    happened to own nothing, which is most of the time.
    """
    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project.id).all()
    for role in client.get(f"/projects/{project.id}/roles").json():
        for task in tasks:
            if task.assignee_role == role["role_title"]:
                return role, task
    raise AssertionError("no role owns any task; the fixture is not exercising assignment")


def test_no_name_is_ever_invented_by_extraction(project, db):
    """The rule the whole roster was built around, kept as a test.

    A role comes from the SOW. A name does not, and a plan that arrived with
    people already on it would mean the model made them up.
    """
    roles = db.query(ProjectRole).filter(ProjectRole.project_id == project.id).all()
    assert roles, "the SOW names roles; none were extracted"
    assert all(role.person_id is None for role in roles)
    assert db.query(Person).count() == 0

    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project.id).all()
    assert all(not task.assignee for task in tasks)


def test_a_person_can_be_added_and_put_on_a_role(client, project, db):
    person = client.post("/people", json={"name": "Ahmed Fathy"}).json()
    role = client.get(f"/projects/{project.id}/roles").json()[0]

    response = client.patch(
        f"/projects/{project.id}/roles/{role['role_id']}",
        json={"person_id": person["id"]},
    )

    assert response.status_code == 200
    assert response.json()["person"] == "Ahmed Fathy"
    assert client.get(f"/projects/{project.id}/roles").json()[0]["person"] == "Ahmed Fathy"


def test_the_name_reaches_the_card_beside_its_role(client, project, db):
    """The point of the feature: whoever opens the card knows who to ask."""
    person = client.post("/people", json={"name": "Ahmed Fathy"}).json()
    role, task = _staffed_role(client, project, db)
    client.patch(
        f"/projects/{project.id}/roles/{role['role_id']}", json={"person_id": person["id"]}
    )

    card = _board_task(db, task, artifacts_for_tasks(db, project.id))

    assert card.assignee_name == "Ahmed Fathy"
    assert "**Owner:** Ahmed Fathy (" in card.rendered_description()


def test_a_role_with_nobody_on_it_shows_only_the_role(client, project, db):
    task = (
        db.query(ProjectTask)
        .filter(ProjectTask.project_id == project.id, ProjectTask.assignee_role != "")
        .first()
    )
    card = _board_task(db, task, artifacts_for_tasks(db, project.id))

    assert card.assignee_name == ""
    assert f"**Owner:** {task.assignee_role}" in card.rendered_description()


def test_changing_who_holds_a_role_marks_their_cards_out_of_date(client, project, db):
    """Every card naming that role now says something different."""
    person = client.post("/people", json={"name": "Ahmed Fathy"}).json()
    role, task = _staffed_role(client, project, db)
    task.external_ref = "card-1"
    task.board_dirty = False
    db.commit()

    client.patch(
        f"/projects/{project.id}/roles/{role['role_id']}", json={"person_id": person["id"]}
    )

    db.refresh(task)
    assert task.board_dirty is True


def test_a_task_can_name_somebody_other_than_the_role_holder(client, project, db):
    """One task going to a second pair of hands must not restaff the role."""
    person = client.post("/people", json={"name": "Ahmed Fathy"}).json()
    role, task = _staffed_role(client, project, db)
    client.patch(
        f"/projects/{project.id}/roles/{role['role_id']}", json={"person_id": person["id"]}
    )
    task.assignee = "Sara Nabil"
    db.commit()

    card = _board_task(db, task, artifacts_for_tasks(db, project.id))

    assert card.assignee_name == "Sara Nabil"


def test_somebody_who_leaves_keeps_their_name_on_the_plan(client, project, db):
    """Deleting the row would erase who was assigned what on a plan that ran."""
    person = client.post("/people", json={"name": "Ahmed Fathy"}).json()
    role = client.get(f"/projects/{project.id}/roles").json()[0]
    client.patch(
        f"/projects/{project.id}/roles/{role['role_id']}", json={"person_id": person["id"]}
    )

    client.delete(f"/people/{person['id']}")

    assert client.get(f"/projects/{project.id}/roles").json()[0]["person"] == "Ahmed Fathy"
    listed = client.get("/people").json()
    assert [p["active"] for p in listed] == [False]


def test_re_adding_somebody_brings_them_back_rather_than_duplicating(client, db):
    person = client.post("/people", json={"name": "Ahmed Fathy"}).json()
    client.delete(f"/people/{person['id']}")

    again = client.post("/people", json={"name": "Ahmed Fathy"})

    assert again.status_code == 200
    assert again.json()["id"] == person["id"]
    assert db.query(Person).count() == 1


def test_adding_the_same_active_person_twice_is_refused(client):
    client.post("/people", json={"name": "Ahmed Fathy"})
    assert client.post("/people", json={"name": "Ahmed Fathy"}).status_code == 409


def test_a_role_can_be_left_with_nobody(client, project):
    person = client.post("/people", json={"name": "Ahmed Fathy"}).json()
    role = client.get(f"/projects/{project.id}/roles").json()[0]
    client.patch(
        f"/projects/{project.id}/roles/{role['role_id']}", json={"person_id": person["id"]}
    )

    response = client.patch(
        f"/projects/{project.id}/roles/{role['role_id']}", json={"person_id": None}
    )

    assert response.json()["person"] is None
