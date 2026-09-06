"""The whole journey, once, through the API the UI actually calls.

Every other test file checks one part in isolation. This one exists for the
faults that only appear where the parts meet: a plan that staffs itself before
it is scheduled, a card that carries a name the database no longer agrees with,
a project that deletes cleanly until the day a table is added and forgotten.

It stubs the model and the board — the point is the platform's own logic, not
Gemini's mood or Trello's uptime — and then refuses to trust any of it,
checking the chain SOW -> requirement -> task -> story -> criterion -> card at
every link rather than that each endpoint returned 200.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_db
from app.main import app
from app.models import (
    AcceptanceCriterion,
    Person,
    PersonRole,
    Project,
    ProjectTask,
    UserStory,
)
from app.schemas.enums import ProjectStatus, ReviewStatus
from tests.conftest import _CLEANUP_ORDER
from tests.test_incremental_push import FakeBoard

CORPUS = Path(__file__).parent.parent / "data" / "sample_sows"
SOW = CORPUS / "sow_a_cairomart.pdf"


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def board(monkeypatch):
    fake = FakeBoard()
    monkeypatch.setattr("app.api.projects.TrelloAdapter", lambda: fake)
    return fake


@pytest.fixture
def stub_llm(monkeypatch, db):
    """Make the upload route run the pipeline against a stub rather than Gemini."""
    from app.graph import workflow
    from app.ingestion.parser import parse_document
    from tests.factories import StubLLM

    doc = parse_document(SOW, "SOW-E2E")
    citations = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()][:2]
    real = workflow.run_sow_pipeline

    def _stubbed(db_, project_id, file_path, doc_key, client=None, **kw):
        return real(db_, project_id, file_path, doc_key, client=StubLLM(citations=citations), **kw)

    monkeypatch.setattr("app.api.projects.run_sow_pipeline", _stubbed)


def _staff_the_directory(db) -> dict[str, Person]:
    """A directory covering the roles this SOW asks for, with one tie left in."""
    people = {}
    for name, roles in {
        "Karim Tarek": ["Technical Lead", "Backend Engineer"],
        "Sara Nabil": ["Backend Engineer"],  # deliberately ties with Karim
        "Omar Adel": ["QA Engineer", "Support Agent"],
        "Ahmed Hassan": ["Commercial Manager", "Account Manager"],
        "Marwan Youssef": ["Operations Manager", "Merchant Success Specialist"],
    }.items():
        person = Person(name=name)
        db.add(person)
        db.flush()
        for role in roles:
            db.add(PersonRole(person_id=person.id, role_title=role))
        people[name] = person
    db.commit()
    return people


def _upload(client) -> dict:
    with SOW.open("rb") as handle:
        response = client.post("/projects/upload", files={"file": (SOW.name, handle.read())})
    assert response.status_code == 200, response.text
    body = response.json()
    assert not body.get("error"), body
    return body


# ---------------------------------------------------------------- the journey


def test_the_whole_flow_holds_together(client, db, board, stub_llm):
    """One pass from PDF to board, checking what each step promised the next."""
    _staff_the_directory(db)
    uploaded = _upload(client)
    project_id = uploaded["project_id"]

    # -- the plan arrives complete ------------------------------------------
    structure = client.get(f"/projects/{project_id}/structure").json()
    totals = structure["totals"]
    assert totals["tasks"] > 0, "a SOW that produced no work is not a plan"
    assert totals["requirements"] > 0

    tasks = client.get(f"/projects/{project_id}/tasks").json()
    assert len(tasks) == totals["tasks"], "the tree and the list disagree on the work"
    assert all(t["review_status"] == ReviewStatus.PENDING.value for t in tasks), (
        "nothing may arrive pre-approved"
    )

    # -- and arrives staffed, without anyone choosing between two people ----
    roles = client.get(f"/projects/{project_id}/roles").json()
    by_title = {r["role_title"]: r for r in roles}
    assert by_title["Technical Lead"]["person"] == "Karim Tarek"
    assert by_title["Backend Engineer"]["person"] is None, (
        "two people can do this; picking one is not the system's call"
    )

    # -- the dates exist and the staffing check can read them ---------------
    timeline = client.get(f"/projects/{project_id}/timeline").json()
    assert timeline["project_start"] and timeline["project_end"]
    assert isinstance(timeline["staffing_clashes"], list)

    # -- one task at a time onto one board ----------------------------------
    first, second = tasks[0], tasks[1]
    for task in (first, second):
        assert client.post(f"/projects/{project_id}/tasks/{task['id']}/approve").status_code == 200
        assert client.post(f"/projects/{project_id}/tasks/{task['id']}/push").status_code == 200

    assert board.boards_created == 1, "a board per push is the bug this feature removed"
    assert len(board.cards) == 2

    project = client.get("/projects").json()
    mine = next(p for p in project if p["id"] == project_id)
    assert mine["status"] == ProjectStatus.PARTIALLY_SYNCED

    # -- what the board says matches what the plan says ---------------------
    card = board.cards[next(iter(board.cards))]
    body = card.rendered_description()
    assert card.title == first["title"]
    if first["assignee_role"]:
        assert first["assignee_role"] in body

    # -- an edit reaches the same card, not a new one -----------------------
    client.patch(f"/projects/{project_id}/tasks/{first['id']}", json={"title": "Renamed by the PM"})
    refreshed = next(
        t for t in client.get(f"/projects/{project_id}/tasks").json() if t["id"] == first["id"]
    )
    assert refreshed["board_dirty"] is True, "an edited task must know its card is stale"

    before = set(board.cards)
    client.post(f"/projects/{project_id}/tasks/{first['id']}/push")
    assert set(board.cards) == before, "the update duplicated the card"

    # Re-read: the list fetched before the push still says this task has no
    # card, and trusting it would have compared against whichever card came
    # first in the dictionary.
    pushed = next(
        t for t in client.get(f"/projects/{project_id}/tasks").json() if t["id"] == first["id"]
    )
    assert board.cards[pushed["external_ref"]].title == "Renamed by the PM"
    assert pushed["board_dirty"] is False, "a pushed card is no longer out of date"

    # -- the rest go up together --------------------------------------------
    client.post(f"/projects/{project_id}/approve")
    pushed = client.post(f"/projects/{project_id}/push").json()
    assert pushed["cards_created"] == len(tasks) - 2
    assert len(board.cards) == len(tasks)


def test_the_board_and_the_plan_disagree_out_loud(client, db, board, stub_llm):
    """Somebody tampers with each card in a different way; all of it is caught."""
    _staff_the_directory(db)
    project_id = _upload(client)["project_id"]
    tasks = client.get(f"/projects/{project_id}/tasks").json()
    for task in tasks:
        client.post(f"/projects/{project_id}/tasks/{task['id']}/approve")
    client.post(f"/projects/{project_id}/approve")
    client.post(f"/projects/{project_id}/push")

    cards = list(board.cards)
    assert len(cards) >= 3, f"need three cards to tamper with, got {len(cards)}"
    board.lists[cards[0]] = "Done"
    board.rewrite(cards[1], "Somebody typed this into Trello")
    board.archive(cards[2])

    client.post(f"/projects/{project_id}/check-board")
    drift = client.get(f"/projects/{project_id}/drift").json()

    assert sorted(row["kind"] for row in drift) == ["archived", "edited", "moved"]
    # None of it was undone.
    assert board.lists[cards[0]] == "Done"
    assert cards[2] in board.archived

    # The archived one comes back whole, and settling it closes the report.
    archived = next(r for r in drift if r["kind"] == "archived")
    assert (
        client.post(
            f"/projects/{project_id}/tasks/{archived['task_id']}/restore-card"
        ).status_code
        == 200
    )
    assert cards[2] not in board.archived

    # Now the one difference that cannot be undone.
    deleted_card = cards[2]
    deleted_task = archived["task_id"]
    board.delete(deleted_card)
    client.post(f"/projects/{project_id}/check-board")

    now = {r["task_id"]: r for r in client.get(f"/projects/{project_id}/drift").json()}
    assert now[deleted_task]["kind"] == "deleted"
    assert "cannot be brought back" in now[deleted_task]["detail"]
    assert (
        client.post(f"/projects/{project_id}/tasks/{deleted_task}/restore-card").status_code == 409
    )


def test_every_derived_thing_points_at_something_real(client, db, stub_llm):
    """The traceability chain, walked link by link rather than counted."""
    project_id = _upload(client)["project_id"]

    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project_id).all()
    stories = db.query(UserStory).filter(UserStory.project_id == project_id).all()
    # Reached through their stories: a criterion has no project of its own,
    # which is exactly the chain this test is here to walk.
    criteria = (
        db.query(AcceptanceCriterion)
        .join(UserStory, UserStory.id == AcceptanceCriterion.user_story_id)
        .filter(UserStory.project_id == project_id)
        .all()
    )

    requirement_ids = {r.id for r in db.get(Project, project_id).requirements}
    story_ids = {s.id for s in stories}

    for task in tasks:
        if task.requirement_id:
            assert task.requirement_id in requirement_ids, f"{task.title!r} is orphaned"
    for story in stories:
        assert story.requirement_id in requirement_ids, f"{story.story_key} is orphaned"
    for criterion in criteria:
        assert criterion.user_story_id in story_ids, f"{criterion.id} is orphaned"

    # Evidence: every chunk a task cites must be one the parser actually made.
    evidence = client.get(f"/projects/{project_id}/evidence").json()
    for task in tasks:
        for key in (k for k in task.source_chunk_keys.split(",") if k):
            assert key in evidence, f"{task.title!r} cites {key}, which does not exist"


def test_every_endpoint_agrees_about_the_same_project(client, db, stub_llm):
    """Numbers shown on different tabs have to come from the same plan."""
    project_id = _upload(client)["project_id"]

    tasks = client.get(f"/projects/{project_id}/tasks").json()
    structure = client.get(f"/projects/{project_id}/structure").json()
    review = client.get(f"/projects/{project_id}/review").json()
    timeline = client.get(f"/projects/{project_id}/timeline").json()
    listed = next(p for p in client.get("/projects").json() if p["id"] == project_id)

    assert structure["totals"]["tasks"] == len(tasks) == review["total"] == listed["task_count"]
    assert sum(review["counts"].values()) == len(tasks)

    # The timeline's span has to contain the work it claims to describe.
    scheduled = [t for t in tasks if t["start_date"] and t["due_date"]]
    assert scheduled, "nothing was scheduled"
    assert min(t["start_date"] for t in scheduled) == timeline["project_start"]
    assert max(t["due_date"] for t in scheduled) == timeline["project_end"]

    per_team = sum(node["task_count"] for node in structure["teams"].values())
    assert per_team == len(tasks), "a task belongs to exactly one team"


def test_a_project_deletes_without_leaving_anything_behind(client, db, board, stub_llm):
    """The check that catches a new table nobody added to the cascade.

    Three separate cascade faults have reached this codebase already, each
    found only when a delete failed in front of somebody.
    """
    _staff_the_directory(db)
    project_id = _upload(client)["project_id"]
    tasks = client.get(f"/projects/{project_id}/tasks").json()
    client.post(f"/projects/{project_id}/tasks/{tasks[0]['id']}/approve")
    client.post(f"/projects/{project_id}/tasks/{tasks[0]['id']}/push")
    client.post(f"/projects/{project_id}/check-board")

    project = db.get(Project, project_id)
    db.delete(project)
    db.commit()

    for table in _CLEANUP_ORDER:
        if table in {"people", "person_roles", "llm_requests"}:
            continue  # not owned by a project
        columns = {
            row[0]
            for row in db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :t"
                ),
                {"t": table},
            )
        }
        if "project_id" not in columns:
            continue
        left = db.execute(
            text(f"SELECT count(*) FROM {table} WHERE project_id = :p"), {"p": project_id}
        ).scalar()
        assert left == 0, f"{left} orphaned row(s) left in {table}"


def test_a_rejected_task_cannot_reach_the_board_by_any_route(client, db, board, stub_llm):
    """The review gate, tried from both directions."""
    project_id = _upload(client)["project_id"]
    tasks = client.get(f"/projects/{project_id}/tasks").json()
    target = tasks[0]

    client.post(
        f"/projects/{project_id}/tasks/{target['id']}/reject",
        json={"reason": "not in scope", "regenerate": False},
    )

    assert client.post(f"/projects/{project_id}/tasks/{target['id']}/push").status_code == 409

    client.post(f"/projects/{project_id}/approve")
    assert client.post(f"/projects/{project_id}/push").status_code == 409
    assert board.boards_created == 0, "a refused push must not leave a board behind"


def test_the_schedule_never_starts_work_before_what_it_depends_on(client, stub_llm):
    """CPM's whole promise, checked against the dates it actually wrote."""
    project_id = _upload(client)["project_id"]
    tasks = client.get(f"/projects/{project_id}/tasks").json()
    by_title = {t["title"]: t for t in tasks}

    for task in tasks:
        for blocker_title in task.get("depends_on", []):
            blocker = by_title.get(blocker_title)
            if not blocker or not (blocker["due_date"] and task["start_date"]):
                continue
            assert date.fromisoformat(task["start_date"]) > date.fromisoformat(
                blocker["due_date"]
            ), f"{task['title']!r} starts before {blocker_title!r} is finished"
