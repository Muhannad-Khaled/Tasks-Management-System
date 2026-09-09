"""Pushing one reviewed task at a time onto a board that outlives the push.

The PM approves task by task, so the board has to be created once and reused.
Before this, every push built a board of its own — which meant pushing three
tasks separately gave you three boards, each holding one card.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.llm.client import get_llm_client
from app.main import app
from app.models import Project, ProjectTask
from app.schemas.enums import ProjectStatus
from app.taskmanager import watch
from app.taskmanager.base import BoardTask, CardSnapshot, TaskManagerInterface
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
        # Titles a person changed on the board, which is not the same as the
        # plan changing: the card differs from what was last written to it.
        self.edits: dict[str, str] = {}
        self.archived: set[str] = set()
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

    def card_snapshots(self, board_id: str) -> dict[str, CardSnapshot]:
        return {
            card_id: CardSnapshot(
                location=self.lists[card_id],
                title=self.edits.get(card_id, task.title),
                description=task.rendered_description(),
                archived=card_id in self.archived,
            )
            for card_id, task in self.cards.items()
        }

    # ---- what a person does to a board, by hand -------------------------
    def rewrite(self, card_id: str, title: str) -> None:
        """Somebody retitled the card in Trello."""
        self.edits[card_id] = title

    def delete(self, card_id: str) -> None:
        """Gone for good, the way Trello's second, deliberate step leaves it."""
        self.cards.pop(card_id, None)
        self.lists.pop(card_id, None)

    def archive(self, card_id: str) -> None:
        """What Trello's menu actually does, and what an accident usually is."""
        self.archived.add(card_id)

    def restore_card(self, board_id: str, external_id: str) -> bool:
        if external_id not in self.cards:
            return False  # deleted, not archived
        self.archived.discard(external_id)
        return True

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
    # Regeneration goes through the model. Without this the endpoint builds a
    # real client and the test becomes a check on Google's availability.
    app.dependency_overrides[get_llm_client] = lambda: StubLLM
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
    first, second, *_ = tasks_of(client, project)
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

    assert any(dependent["depends_on"][0]["title"] in w for w in warnings)
    # A warning, not a refusal: the PM reviews in whatever order suits them.
    assert board.cards


def test_push_remaining_sends_only_what_is_missing(client, project, board):
    first = tasks_of(client, project)[0]
    approve(client, project, first)
    push(client, project, first)

    client.post(f"/projects/{project.id}/approve")
    result = client.post(f"/projects/{project.id}/push").json()

    assert result["cards_created"] == 3  # the first was already there
    assert result["cards_updated"] == 0
    assert len(board.cards) == 4
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
            task_id="t", title="t", description="", team=team, status="backlog"
        )
        assert adapter.expected_location(task) in LISTS
    unknown = BoardTask(
        task_id="t", title="t", description="", team="marketing", status="backlog"
    )
    assert adapter.expected_location(unknown) == "Backlog"


def _card_of(board, task_id: str, client, project) -> str:
    """The card id a task was pushed to."""
    listed = {t["id"]: t for t in client.get(f"/projects/{project.id}/tasks").json()}
    return listed[task_id]["external_ref"]


def test_a_card_someone_rewrote_is_reported(client, project, board):
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)

    card_id = next(iter(board.cards))
    board.rewrite(card_id, "Somebody typed this straight into Trello")

    warnings = push(client, project, task).json()["warnings"]

    assert any("edited on the board" in w for w in warnings), warnings


def test_a_card_matching_what_was_pushed_is_not_called_edited(client, project, board):
    """The fingerprint has to survive a push that changed nothing."""
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)

    warnings = push(client, project, task).json()["warnings"]

    assert not [w for w in warnings if "edited" in w], warnings


def test_a_deleted_card_is_reported_and_not_recreated(client, project, board):
    """Recreating it would undo a decision somebody made on purpose."""
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)
    assert len(board.cards) == 1

    board.delete(next(iter(board.cards)))
    response = push(client, project, task)

    assert response.status_code == 200
    assert any("not recreated" in w for w in response.json()["warnings"])
    assert board.cards == {}, "the card was put back without being asked"


def test_a_difference_is_reported_once_not_every_push(client, project, board):
    """An alert that repeats every cycle is an alert people mute."""
    first, second, *_ = tasks_of(client, project)
    for task in (first, second):
        approve(client, project, task)
    push(client, project, first)

    card_id = next(iter(board.lists))
    board.lists[card_id] = "Done"

    said_first = [w for w in push(client, project, first).json()["warnings"] if "Done" in w]
    said_again = [w for w in push(client, project, second).json()["warnings"] if "Done" in w]

    assert said_first, "the move was never reported"
    assert not said_again, "the same unresolved move was reported twice"


def test_a_difference_that_is_settled_and_returns_is_news_again(client, project, board):
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)
    card_id = next(iter(board.lists))
    planned = board.lists[card_id]

    board.lists[card_id] = "Done"
    push(client, project, task)          # reported
    board.lists[card_id] = planned       # somebody put it back
    push(client, project, task)          # settled
    board.lists[card_id] = "Done"        # and moved again

    warnings = push(client, project, task).json()["warnings"]

    assert any("Done" in w for w in warnings), warnings


def test_open_drift_is_listed_for_the_ui(client, project, board):
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)
    board.lists[next(iter(board.lists))] = "Done"
    push(client, project, task)

    listed = client.get(f"/projects/{project.id}/drift").json()

    assert [row["kind"] for row in listed] == ["moved"]
    assert listed[0]["task_id"] == task["id"]
    assert listed[0]["notified"] is False  # no webhook configured in tests


def test_an_unreadable_board_reports_nothing_rather_than_everything(client, project, board):
    """An empty read is not proof that every card was deleted."""
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)

    board.card_snapshots = lambda board_id: {}
    warnings = push(client, project, task).json()["warnings"]

    assert not [w for w in warnings if "no longer on the board" in w], warnings


async def _run_watcher_until(calls: list, wanted: int, timeout: float = 5.0) -> None:
    """Drive the watcher until it has run `wanted` times, then stop it.

    Waiting a fixed number of milliseconds and hoping made the test flaky: the
    check runs in a worker thread, so how many cycles fit in 50ms is a property
    of the machine, not of the code being tested.
    """
    import asyncio

    task = asyncio.create_task(watch.watch_boards(interval_seconds=0))
    deadline = asyncio.get_running_loop().time() + timeout
    while len(calls) < wanted and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def test_the_watcher_actually_runs_on_its_timer():
    """The one thing this feature cannot verify by being used: it runs unattended."""
    import asyncio

    calls = []

    async def drive():
        original = watch.check_all_boards
        watch.check_all_boards = lambda: calls.append(1) or watch.WatchReport()
        try:
            await _run_watcher_until(calls, wanted=1)
        finally:
            watch.check_all_boards = original

    asyncio.run(drive())
    assert calls, "the watcher never called the check"


def test_the_watcher_survives_a_check_that_raises():
    """A watcher that dies on one bad response stops watching, silently."""
    import asyncio

    calls = []

    def explode():
        calls.append(1)
        raise RuntimeError("Trello fell over")

    async def drive():
        original = watch.check_all_boards
        watch.check_all_boards = explode
        try:
            await _run_watcher_until(calls, wanted=3)
        finally:
            watch.check_all_boards = original

    asyncio.run(drive())
    assert len(calls) >= 3, f"the watcher stopped after {len(calls)} failure(s)"


def test_an_archived_card_is_not_reported_as_deleted(client, project, board):
    """Telling the PM to rebuild would throw away comments and ticks for nothing."""
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)
    board.archive(next(iter(board.cards)))

    push(client, project, task)
    listed = client.get(f"/projects/{project.id}/drift").json()

    assert [row["kind"] for row in listed] == ["archived"]
    assert "cannot be brought back" not in listed[0]["detail"]


def test_an_archived_card_can_be_put_back(client, project, board):
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)
    card_id = next(iter(board.cards))
    board.archive(card_id)
    push(client, project, task)

    response = client.post(f"/projects/{project.id}/tasks/{task['id']}/restore-card")

    assert response.status_code == 200
    assert card_id not in board.archived
    # And the same card, not a replacement: whatever it held is still on it.
    assert card_id in board.cards
    assert client.get(f"/projects/{project.id}/drift").json() == []


def test_restoring_a_deleted_card_says_it_cannot_be_done(client, project, board):
    """A success message that put nothing back would be the worst answer here."""
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)
    board.delete(next(iter(board.cards)))

    response = client.post(f"/projects/{project.id}/tasks/{task['id']}/restore-card")

    assert response.status_code == 409
    assert "deleted, not archived" in response.json()["detail"]


def test_an_archived_card_is_not_also_called_moved(client, project, board):
    """Archived cards sit in no list; reporting a move as well is noise."""
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)
    card_id = next(iter(board.cards))
    board.archive(card_id)
    board.lists[card_id] = "Done"

    push(client, project, task)

    kinds = [row["kind"] for row in client.get(f"/projects/{project.id}/drift").json()]
    assert kinds == ["archived"], kinds


def test_a_regenerated_task_marks_its_card_out_of_date(client, project, board):
    """Its wording changed, so the card no longer describes it either."""
    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)

    listed = {t["id"]: t for t in client.get(f"/projects/{project.id}/tasks").json()}
    assert listed[task["id"]]["board_dirty"] is False

    client.post(
        f"/projects/{project.id}/tasks/{task['id']}/reject",
        json={"reason": "wrong scope", "regenerate": True},
    )

    after = {t["id"]: t for t in client.get(f"/projects/{project.id}/tasks").json()}
    assert after[task["id"]]["board_dirty"] is True


def test_a_successful_read_records_when_the_board_was_looked_at(client, project, board):
    """'No differences' means nothing without knowing when it was checked."""
    from app.taskmanager.watch import check_project

    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)
    db = next(iter(app.dependency_overrides.values()))()
    fresh = db.get(Project, project.id)
    assert fresh.board_checked_at is None

    check_project(db, fresh, board)

    assert fresh.board_checked_at is not None


def test_a_board_that_could_not_be_read_is_not_marked_as_checked(client, project, board):
    """A time saying the board was looked at, when it was not, is worse than none.

    It turns 'nobody has looked since you made that change' into 'the board
    matches the plan' — the exact confusion the timestamp was added to end.
    """
    from app.taskmanager.trello import TrelloError
    from app.taskmanager.watch import check_project

    task = tasks_of(client, project)[0]
    approve(client, project, task)
    push(client, project, task)
    db = next(iter(app.dependency_overrides.values()))()
    fresh = db.get(Project, project.id)

    def refuse(board_id: str):
        raise TrelloError("Trello is down")

    board.card_snapshots = refuse
    with pytest.raises(TrelloError):
        check_project(db, fresh, board)

    assert fresh.board_checked_at is None


def test_the_watch_interval_comes_from_the_env_file():
    """A setting that silently ignores .env is a setting nobody has changed.

    BOARD_WATCH_SECONDS was read straight from os.environ while every other
    setting came from .env, so putting it in the file did nothing at all and
    said nothing about it.
    """
    from app.core.config import Settings

    assert Settings(_env_file=None).board_watch_seconds is None, "no default hidden here"
    assert Settings(_env_file=None, board_watch_seconds=45).board_watch_seconds == 45
