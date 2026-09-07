"""Putting a name on a card is not the same as assigning somebody to it.

The card body has carried an owner's name for a while. Trello only puts the
work in that person's own list when the card names their *account*, and no
amount of matching on a name could establish which account that is. So the
link is typed by a human, once, and everything here defends that boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.main import app
from app.models import Person, PersonRole, Project, ProjectRole, ProjectTask
from app.schemas.enums import ProjectStatus
from app.taskmanager.cards import _artifacts_by_requirement, _board_task
from tests.factories import StubLLM

CORPUS = Path(__file__).parent.parent / "data" / "sample_sows"


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _person(db, name: str, *roles: str) -> Person:
    person = Person(name=name)
    db.add(person)
    db.flush()
    for role in roles:
        db.add(PersonRole(person_id=person.id, role_title=role))
    db.commit()
    return person


def _run(db, name: str = "Members") -> Project:
    sow = CORPUS / "sow_a_cairomart.pdf"
    doc = parse_document(sow, "SOW-001")
    citations = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()][:2]
    project = Project(name=name, status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    run_sow_pipeline(db, project.id, str(sow), "SOW-001", client=StubLLM(citations=citations))
    return project


def _card_for(db, project: Project, role_title: str):
    task = (
        db.query(ProjectTask)
        .filter(
            ProjectTask.project_id == project.id,
            ProjectTask.assignee_role == role_title,
        )
        .first()
    )
    assert task is not None, f"no task owned by {role_title}"
    return _board_task(db, task, _artifacts_by_requirement(db, project.id))


# --- the link -----------------------------------------------------------


def test_a_person_starts_with_no_trello_account(client):
    person = client.post("/people", json={"name": "Ahmed Hassan", "roles": []}).json()

    listed = {p["id"]: p for p in client.get("/people").json()}

    assert listed[person["id"]]["trello_member_id"] == ""


def test_linking_an_account_is_remembered(client):
    person = client.post("/people", json={"name": "Ahmed Hassan", "roles": []}).json()

    client.patch(f"/people/{person['id']}/trello", json={"member_id": "mem-1"})

    listed = {p["id"]: p for p in client.get("/people").json()}
    assert listed[person["id"]]["trello_member_id"] == "mem-1"


def test_one_account_cannot_be_two_people(client):
    """Both would be assigned the same cards and neither would look wrong."""
    first = client.post("/people", json={"name": "Ahmed Hassan", "roles": []}).json()
    second = client.post("/people", json={"name": "Ahmed Kamal", "roles": []}).json()
    client.patch(f"/people/{first['id']}/trello", json={"member_id": "mem-1"})

    clash = client.patch(f"/people/{second['id']}/trello", json={"member_id": "mem-1"})

    assert clash.status_code == 409
    assert "Ahmed Hassan" in clash.json()["detail"]


def test_an_account_can_be_unlinked(client):
    person = client.post("/people", json={"name": "Ahmed Hassan", "roles": []}).json()
    client.patch(f"/people/{person['id']}/trello", json={"member_id": "mem-1"})

    client.patch(f"/people/{person['id']}/trello", json={"member_id": ""})

    listed = {p["id"]: p for p in client.get("/people").json()}
    assert listed[person["id"]]["trello_member_id"] == ""


def test_relinking_marks_their_cards_out_of_date(client, db):
    """The card said their name before and says the same name now.

    Nothing in the text changed, so nothing would mark it stale — but the card
    can now carry an assignment it does not carry yet.
    """
    person = _person(db, "Karim Tarek", "Backend Engineer")
    project = _run(db)
    task = (
        db.query(ProjectTask)
        .filter(
            ProjectTask.project_id == project.id,
            ProjectTask.assignee_role == "Backend Engineer",
        )
        .first()
    )
    task.external_ref = "card-1"
    task.board_dirty = False
    db.commit()

    client.patch(f"/people/{person.id}/trello", json={"member_id": "mem-1"})

    db.refresh(task)
    assert task.board_dirty is True


# --- what reaches the card ----------------------------------------------


def test_a_linked_owner_reaches_the_card_as_an_account(db):
    karim = _person(db, "Karim Tarek", "Backend Engineer")
    karim.trello_member_id = "mem-1"
    db.commit()

    project = _run(db)

    card = _card_for(db, project, "Backend Engineer")
    assert card.assignee_name == "Karim Tarek"
    assert card.assignee_member_id == "mem-1"


def test_an_unlinked_owner_is_named_but_not_assigned(db):
    """The normal case: most of a company has no Trello login."""
    _person(db, "Karim Tarek", "Backend Engineer")

    project = _run(db)

    card = _card_for(db, project, "Backend Engineer")
    assert card.assignee_name == "Karim Tarek"
    assert card.assignee_member_id == ""


def test_a_name_typed_onto_one_task_carries_no_account(db):
    """`assignee` is free text. It names somebody without being anybody."""
    karim = _person(db, "Karim Tarek", "Backend Engineer")
    karim.trello_member_id = "mem-1"
    db.commit()
    project = _run(db)
    task = (
        db.query(ProjectTask)
        .filter(
            ProjectTask.project_id == project.id,
            ProjectTask.assignee_role == "Backend Engineer",
        )
        .first()
    )
    task.assignee = "Karim Tarek"
    db.commit()

    card = _board_task(db, task, _artifacts_by_requirement(db, project.id))

    assert card.assignee_name == "Karim Tarek"
    assert card.assignee_member_id == "", "a typed name must not resolve to an account"


# --- what Trello is sent -------------------------------------------------


def test_the_card_is_created_with_the_member_on_it():
    import httpx

    from app.taskmanager.base import BoardTask
    from app.taskmanager.trello import TrelloAdapter

    sent: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/lists"):
            return httpx.Response(200, json=[{"id": "l1", "name": "Backlog"}])
        if path.endswith("/labels") and request.method == "POST":
            return httpx.Response(200, json={"id": "lab-1"})
        if path.endswith("/cards") and request.method == "POST":
            sent.append(dict(request.url.params))
            return httpx.Response(200, json={"id": "c1"})
        return httpx.Response(200, json=[])

    adapter = TrelloAdapter(api_key="k", token="t")
    adapter.http = httpx.Client(base_url="https://api.trello.com/1", transport=httpx.MockTransport(respond))
    adapter.push_tasks(
        "b1", [BoardTask(task_id="T1", title="Build", description="", team="technical",
                  priority="medium", status="backlog", assignee_member_id="mem-1")]
    )

    assert sent and sent[0].get("idMembers") == "mem-1"


def test_a_card_with_no_linked_owner_leaves_members_alone():
    """Sending an empty list would strip whoever a person assigned by hand."""
    import httpx

    from app.taskmanager.base import BoardTask
    from app.taskmanager.trello import TrelloAdapter

    sent: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/labels") and request.method == "POST":
            return httpx.Response(200, json={"id": "lab-1"})
        if request.method == "PUT":
            sent.append(dict(request.url.params))
            return httpx.Response(200, json={"id": "c1"})
        return httpx.Response(200, json=[])

    adapter = TrelloAdapter(api_key="k", token="t")
    adapter.http = httpx.Client(base_url="https://api.trello.com/1", transport=httpx.MockTransport(respond))
    adapter.update_task(
        "b1",
        "c1",
        BoardTask(
            task_id="T1", title="Build", description="", team="technical",
            priority="medium", status="backlog",
        ),
    )

    assert sent, "the card was never updated"
    assert "idMembers" not in sent[0]


def test_the_board_roster_is_read_from_trello():
    import httpx

    from app.taskmanager.trello import TrelloAdapter

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"id": "mem-1", "username": "muhannad", "fullName": "Muhannad Khaled"}],
        )

    adapter = TrelloAdapter(api_key="k", token="t")
    adapter.http = httpx.Client(transport=httpx.MockTransport(respond))

    assert adapter.board_members("b1") == [
        {"id": "mem-1", "username": "muhannad", "full_name": "Muhannad Khaled"}
    ]


# --- what the PM is told -------------------------------------------------


def test_a_push_says_who_was_named_but_not_assigned(db):
    """Silence would read as success: the card looks staffed either way."""
    from app.taskmanager.base import BoardTask
    from app.taskmanager.sync import _unassignable_owners

    task = ProjectTask(id="T1", project_id="P1", title="Build", team="technical")
    linked = BoardTask(
        task_id="T1", title="Build", description="", team="technical",
        priority="medium", status="backlog",
        assignee_name="Sara Nabil", assignee_member_id="mem-2",
    )
    unlinked = BoardTask(
        task_id="T2", title="Ship", description="", team="technical",
        priority="medium", status="backlog", assignee_name="Karim Tarek",
    )
    other = ProjectTask(id="T2", project_id="P1", title="Ship", team="technical")

    warnings = _unassignable_owners([task, other], {"T1": linked, "T2": unlinked})

    assert len(warnings) == 1
    assert "Karim Tarek (1)" in warnings[0]
    assert "Sara Nabil" not in warnings[0], "a linked owner is not a problem"


def test_nobody_named_at_all_is_not_reported_as_unassignable(db):
    from app.taskmanager.base import BoardTask
    from app.taskmanager.sync import _unassignable_owners

    task = ProjectTask(id="T1", project_id="P1", title="Build", team="technical")
    card = BoardTask(
        task_id="T1", title="Build", description="", team="technical",
        priority="medium", status="backlog",
    )

    assert _unassignable_owners([task], {"T1": card}) == []


def test_the_board_members_endpoint_needs_a_board(client, db):
    project = Project(name="No board", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()

    response = client.get(f"/projects/{project.id}/board-members")

    assert response.status_code == 409


def test_staffing_a_role_puts_the_right_account_on_every_card_it_owns(db):
    """One link, not one per task — that is the point of assigning by role."""
    sara = _person(db, "Sara Nabil", "Backend Engineer", "AI Engineer")
    sara.trello_member_id = "mem-2"
    db.commit()
    project = _run(db)

    owned = (
        db.query(ProjectRole)
        .filter(ProjectRole.project_id == project.id, ProjectRole.role_title == "Backend Engineer")
        .one()
    )
    assert owned.person_id == sara.id
    assert _card_for(db, project, "Backend Engineer").assignee_member_id == "mem-2"


# --- what the board is caught doing --------------------------------------


def _snapshot(members=(), title="Build", desc="body"):
    from app.taskmanager.base import CardSnapshot

    return CardSnapshot(location="Technical", title=title, description=desc, members=members)


def _pair(member_id: str = "", name: str = ""):
    """A task on the board and the card the plan last wrote for it."""
    from app.taskmanager.base import BoardTask

    task = ProjectTask(
        id="T1", project_id="P1", title="Build", team="technical", external_ref="c1"
    )
    card = BoardTask(
        task_id="T1", title="Build", description="", team="technical",
        priority="medium", status="backlog",
        assignee_name=name, assignee_member_id=member_id,
    )
    return task, {"T1": card}


class _Adapter:
    def expected_location(self, task) -> str:
        return "Technical"


def test_a_card_taken_from_its_planned_owner_is_reported(db):
    """The gap assignment opened: nobody was watching the members field.

    The fingerprint covers the title and the body. Somebody removing the owner
    and putting themselves on the card changes neither, so before this the
    platform reported the board as matching the plan while it did not.
    """
    from app.taskmanager import drift

    task, cards = _pair("mem-1", "Karim Tarek")

    found = drift.detect([task], cards, {"c1": _snapshot(members=("mem-9",))}, _Adapter())

    assert [d.kind for d in found] == [drift.REASSIGNED]
    assert "Karim Tarek" in found[0].detail


def test_a_card_still_holding_its_owner_is_not_reported(db):
    from app.taskmanager import drift

    task, cards = _pair("mem-1", "Karim Tarek")

    found = drift.detect([task], cards, {"c1": _snapshot(members=("mem-1", "mem-9"))}, _Adapter())

    assert found == [], "a second person alongside the owner is not a disagreement"


def test_members_on_a_card_the_plan_has_no_account_for_are_left_alone(db):
    """Push never sets members there, so whoever is on it was put there by hand.

    Reporting that would be the platform objecting to the one thing it has
    already decided not to touch.
    """
    from app.taskmanager import drift

    task, cards = _pair()

    assert drift.detect([task], cards, {"c1": _snapshot(members=("mem-9",))}, _Adapter()) == []


def test_a_reassignment_is_not_reported_as_an_edit(db):
    """Two different problems with two different answers.

    Text is replaced by pushing again; an owner is somebody's decision about
    who does the work. Folding members into the fingerprint would say the card
    was rewritten when not a character of it changed.
    """
    from app.taskmanager import drift

    task, cards = _pair("mem-1", "Karim Tarek")
    task.board_fingerprint = _snapshot().fingerprint()

    found = drift.detect([task], cards, {"c1": _snapshot(members=("mem-9",))}, _Adapter())

    assert [d.kind for d in found] == [drift.REASSIGNED]


def test_the_snapshot_reads_members_off_the_board():
    import httpx

    from app.taskmanager.trello import TrelloAdapter

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/lists"):
            return httpx.Response(200, json=[{"id": "l1", "name": "Technical"}])
        return httpx.Response(
            200,
            json=[
                {
                    "id": "c1",
                    "idList": "l1",
                    "name": "Build",
                    "desc": "",
                    "closed": False,
                    "idMembers": ["mem-1", "mem-2"],
                }
            ],
        )

    adapter = TrelloAdapter(api_key="k", token="t")
    adapter.http = httpx.Client(transport=httpx.MockTransport(respond))

    assert adapter.card_snapshots("b1")["c1"].members == ("mem-1", "mem-2")
