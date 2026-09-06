"""Pushing one reviewed task at a time onto a board that outlives the push.

The PM approves task by task, so the board has to be created once and reused.
Before this, every push built a board of its own — which meant pushing three
tasks separately gave you three boards, each holding one card.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.main import app
from app.models import Project, ProjectTask
from app.schemas.enums import ProjectStatus
from app.taskmanager.base import BoardTask, TaskManagerInterface
from tests.factories import CORPUS, StubLLM


class FakeBoard(TaskManagerInterface):
    """An in-memory board that records what it was asked to do."""

    name = "fake"

    def __init__(self):
        self.boards_created = 0
        self.cards: dict[str, BoardTask] = {}
        self.updated: list[str] = []
        # Where each card sits. A real board lets a person drag a card into
        # another list, and nothing in the push path may quietly undo that.
        self.lists: dict[str, str] = {}
        self._counter = 0

    def create_board(self, project_name: str) -> tuple[str, str]:
        self.boards_created += 1
        return f"board-{self.boards_created}", f"https://trello.test/b/{self.boards_created}"

    def push_tasks(self, board_id: str, tasks: list[BoardTask]) -> dict[str, str]:
        created = {}
        for task in tasks:
            self._counter += 1
            card_id = f"card-{self._counter}"
            self.cards[card_id] = task
            self.lists[card_id] = self.expected_location(task)
            created[task.task_id] = card_id
        return created

    def expected_location(self, task: BoardTask) -> str:
        return task.team.capitalize()

    def card_locations(self, board_id: str) -> dict[str, str]:
        return dict(self.lists)

    def update_task(self, board_id: str, external_id: str, task: BoardTask) -> bool:
        if external_id not in self.cards:
            return False  # somebody deleted the card by hand
        self.cards[external_id] = task
        self.updated.append(external_id)
        return True


@pytest.fixture
def board(monkeypatch):
    fake = FakeBoard()
    monkeypatch.setattr("app.api.projects.TrelloAdapter", lambda: fake)
    return fake


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
    project = Project(name="Incremental", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    run_sow_pipeline(db, project.id, str(sow), "SOW-001", client=StubLLM(citations=citations))
    return project


def tasks_of(client, project) -> list[dict]:
    """Tasks in dependency order: the contract, then the integration, then setup."""
    listed = client.get(f"/projects/{project.id}/tasks").json()
    return sorted(listed, key=lambda t: len(t["depends_on"]))


def approve(client, project, task) -> None:
    assert client.post(f"/projects/{project.id}/tasks/{task['id']}/approve").status_code == 200


def push(client, project, task):
    return client.post(f"/projects/{project.id}/tasks/{task['id']}/push")


def test_two_separate_pushes_land_on_one_board(client, project, board):
    first, second, _ = tasks_of(client, project)
    for task in (first, second):
        approve(client, project, task)
        assert push(client, project, task).status_code == 200

    # The regression this whole feature turns on: a board per push meant the
    # PM's second approved task went somewhere the first one could not be seen.
    assert board.boards_created == 1
    assert len(board.cards) == 2


def test_a_task_can_be_pushed_without_approving_the_project(client, project, board, db):
    task = tasks_of(client, project)[0]
    approve(client, project, task)

    assert push(client, project, task).status_code == 200

    db.refresh(project)
    # Never went through /approve, and did not need to: requiring the plan-wide
    # sign-off to send one task is exactly what this replaces.
    assert project.status == ProjectStatus.PARTIALLY_SYNCED


def test_an_unreviewed_task_is_refused(client, project, board):
    task = tasks_of(client, project)[0]
    resp = push(client, project, task)
    assert resp.status_code == 409
    assert "approve" in resp.json()["detail"].lower()
    assert board.boards_created == 0


def test_a_rejected_task_is_refused(client, project, board):
    task = tasks_of(client, project)[0]
    client.post(
        f"/projects/{project.id}/tasks/{task['id']}/reject",
        json={"reason": "out of scope", "regenerate": False},
    )
    resp = push(client, project, task)
    assert resp.status_code == 409
    assert "rejected" in resp.json()["detail"].lower()


def test_pushing_an_edited_task_updates_its_card(client, project, board):
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    first = push(client, project, task).json()
    assert first["action"] == "created"

    client.patch(f"/projects/{project.id}/tasks/{task['id']}", json={"title": "Reworded by the PM"})
    second = push(client, project, task).json()

    assert second["action"] == "updated"
    assert second["card_id"] == first["card_id"]
    assert len(board.cards) == 1  # not a duplicate
    assert board.cards[first["card_id"]].title == "Reworded by the PM"


def test_an_edit_marks_the_card_out_of_date(client, project, board, db):
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)

    client.patch(f"/projects/{project.id}/tasks/{task['id']}", json={"title": "Changed"})
    assert db.get(ProjectTask, task["id"]).board_dirty is True

    push(client, project, task)
    assert db.get(ProjectTask, task["id"]).board_dirty is False


def test_a_card_deleted_from_the_board_is_created_again(client, project, board):
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    original = push(client, project, task).json()["card_id"]

    del board.cards[original]  # somebody removed it in Trello
    again = push(client, project, task).json()

    assert again["action"] == "created"
    assert again["card_id"] != original
    assert any("no longer on the board" in w for w in again["warnings"])


def test_pushing_ahead_of_a_blocker_says_so(client, project, board):
    dependent = next(t for t in tasks_of(client, project) if t["depends_on"])
    approve(client, project, dependent)

    warnings = push(client, project, dependent).json()["warnings"]

    assert any(dependent["depends_on"][0] in w for w in warnings)
    # A warning, not a refusal: the PM reviews in whatever order suits them.
    assert board.cards


def test_push_remaining_sends_only_what_is_missing(client, project, board):
    first = tasks_of(client, project)[0]
    approve(client, project, first)
    push(client, project, first)

    client.post(f"/projects/{project.id}/approve")
    result = client.post(f"/projects/{project.id}/push").json()

    assert result["cards_created"] == 2  # the third was already there
    assert result["cards_updated"] == 0
    assert len(board.cards) == 3
    assert board.boards_created == 1


def test_push_remaining_is_a_no_op_when_nothing_changed(client, project, board):
    client.post(f"/projects/{project.id}/approve")
    client.post(f"/projects/{project.id}/push")
    board.updated.clear()

    result = client.post(f"/projects/{project.id}/push").json()

    # Re-sending unchanged cards costs about three Trello calls each and would
    # overwrite them with what they already say.
    assert (result["cards_created"], result["cards_updated"]) == (0, 0)
    assert board.updated == []


def test_status_follows_what_is_actually_on_the_board(client, project, board, db):
    all_tasks = tasks_of(client, project)
    for task in all_tasks[:-1]:
        approve(client, project, task)
        push(client, project, task)
    db.refresh(project)
    assert project.status == ProjectStatus.PARTIALLY_SYNCED

    last = all_tasks[-1]
    approve(client, project, last)
    push(client, project, last)
    db.refresh(project)
    assert project.status == ProjectStatus.SYNCED


def test_push_remaining_still_works_after_a_partial_push(client, project, board):
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)  # leaves the project partially_synced

    client.post(f"/projects/{project.id}/approve")
    # Without partially_synced in the allowed set, pushing one task first would
    # have locked the PM out of sending the rest.
    assert client.post(f"/projects/{project.id}/push").status_code == 200


def test_a_card_someone_moved_is_reported_not_dragged_back(client, project, board):
    """The board is the team's. A move is news to report, not a mistake to fix."""
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)

    card_id = next(iter(board.lists))
    board.lists[card_id] = "Done"  # somebody dragged it across the board

    response = push(client, project, task)

    assert response.status_code == 200
    warnings = response.json()["warnings"]
    assert any("Done" in w for w in warnings), warnings
    assert board.lists[card_id] == "Done", "the push dragged a human's card back"


def test_a_card_left_where_the_plan_put_it_says_nothing(client, project, board):
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)

    moved = [w for w in push(client, project, task).json()["warnings"] if "sits in" in w]
    assert not moved, moved


def test_cards_are_created_where_drift_is_measured_from():
    """The two must agree, or every freshly pushed card reports itself moved."""
    from app.taskmanager.trello import LISTS, TrelloAdapter

    adapter = TrelloAdapter.__new__(TrelloAdapter)
    for team in ("commercial", "technical", "operations"):
        task = BoardTask(
            task_id="t", title="t", description="", team=team, priority="high", status="backlog"
        )
        assert adapter.expected_location(task) in LISTS
    unknown = BoardTask(
        task_id="t", title="t", description="", team="marketing", priority="high", status="backlog"
    )
    assert adapter.expected_location(unknown) == "Backlog"
