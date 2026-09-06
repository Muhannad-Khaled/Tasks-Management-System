"""Board representation.

The board is what the team acts on. A label there carries more weight than
anything in the app, so it must not overstate how well a task is supported —
and the card body must not bury a real warning under metadata nobody needs.
"""

from datetime import date

import pytest

from app.schemas.roles import TEAM_ROLES
from app.taskmanager.base import BoardCase, BoardTask
from app.taskmanager.labels import (
    FALLBACK_COLOR,
    LABEL_COLORS,
    ROLE_COLOR,
    ROLE_PREFIX,
    TRELLO_LABEL_COLORS,
    label_color,
    role_label,
)
from app.taskmanager.trello import TrelloAdapter, TrelloError


def _task(**kwargs) -> BoardTask:
    defaults = {
        "task_id": "T1",
        "title": "Develop the POS integration",
        "description": "Build it.",
        "team": "technical",
        "priority": "high",
        "status": "backlog",
    }
    return BoardTask(**{**defaults, **kwargs})


def test_a_task_that_failed_grounding_is_never_labelled_grounded():
    # Regression: GROUNDED came from the source being explicit, so a task the
    # system had rejected reached the board claiming full SOW support.
    labels = _task(source_status="explicit", validation_status="reject").labels()
    assert "GROUNDED" not in labels
    assert "GROUNDING-FAILED" in labels


def test_provenance_and_grounding_are_separate_labels():
    labels = _task(source_status="explicit", validation_status="accept").labels()
    assert "FROM-SOW" in labels, "where it came from"
    assert "GROUNDED" in labels, "whether it held up"


@pytest.mark.parametrize(
    "validation_status,expected",
    [
        ("accept", "GROUNDED"),
        ("review", "REVIEW-REQUIRED"),
        ("reject", "GROUNDING-FAILED"),
        ("pending", "NOT-CHECKED"),
    ],
)
def test_every_grounding_verdict_reaches_the_board(validation_status, expected):
    assert expected in _task(validation_status=validation_status).labels()


@pytest.mark.parametrize(
    "source_status,expected",
    [("explicit", "FROM-SOW"), ("inferred", "INFERRED"), ("assumed", "ASSUMED")],
)
def test_provenance_reaches_the_board(source_status, expected):
    assert expected in _task(source_status=source_status).labels()


def test_team_and_priority_are_labelled():
    labels = _task(team="operations", priority="low").labels()
    assert "TEAM-OPERATIONS" in labels
    assert "PRIORITY-LOW" in labels


def test_the_role_is_a_label_so_the_board_can_be_filtered_by_it():
    # With no people on the board, filtering by role is the closest thing to
    # "show me my cards", which is the whole reason to assign at all.
    assert "ROLE-QA-ENGINEER" in _task(assignee_role="QA Engineer").labels()


def test_a_task_with_no_role_gets_no_role_label():
    assert not [lbl for lbl in _task().labels() if lbl.startswith(ROLE_PREFIX)]


def test_a_role_the_sow_invented_still_produces_a_usable_label():
    # Role titles are not a closed set, so the label must be derivable from any
    # of them rather than looked up.
    assert role_label("Integration Architect") == "ROLE-INTEGRATION-ARCHITECT"
    assert label_color(role_label("Integration Architect")) != "light-gray"


@pytest.mark.parametrize("role", TEAM_ROLES, ids=lambda r: r.title)
def test_every_roster_role_has_a_coloured_label(role):
    assert label_color(role_label(role.title)) != "light-gray"


def test_every_label_the_code_emits_has_a_colour():
    # An unmapped label silently falls back to grey, which is also what
    # "unknown" means — so a mistake would look deliberate.
    emitted = set()
    for source in ("explicit", "inferred", "assumed", "weird"):
        for verdict in ("accept", "review", "reject", "pending"):
            for team in ("commercial", "technical", "operations"):
                for priority in ("high", "medium", "low"):
                    emitted |= set(
                        _task(
                            source_status=source,
                            validation_status=verdict,
                            team=team,
                            priority=priority,
                        ).labels()
                    )
    unmapped = {lbl for lbl in emitted if lbl not in LABEL_COLORS}
    assert not unmapped, f"unmapped labels: {unmapped}"


# --------------------------------------------------------------- card body


def test_a_healthy_card_carries_no_platform_metadata():
    # Chunk keys identify nothing outside this system, "EXPLICIT" is our
    # vocabulary rather than the team's, and a 100% score asks for no action.
    # Printing them anyway trains people to skip the block that also carries
    # the warnings.
    body = _task(
        assignee_role="Backend Engineer",
        source_status="explicit",
        source_section="5. Technical Requirements",
        source_chunk_keys=["SOW-001-S05-C02"],
        grounding_score=1.0,
        validation_status="accept",
    ).rendered_description()

    assert "SOW-001-S05-C02" not in body
    assert "EXPLICIT" not in body
    assert "100%" not in body
    assert "⚠️" not in body


def test_a_healthy_card_still_says_who_owns_it_and_where_it_came_from():
    body = _task(
        assignee_role="Backend Engineer", source_section="5. Technical Requirements"
    ).rendered_description()

    assert "Backend Engineer" in body
    assert "5. Technical Requirements" in body


def test_a_blocked_card_says_what_it_is_waiting_for():
    body = _task(depends_on_titles=["Countersign the contract"]).rendered_description()
    assert "Blocked by" in body
    assert "Countersign the contract" in body


def test_an_unblocked_card_does_not_mention_being_blocked():
    assert "Blocked by" not in _task().rendered_description()


def test_a_poorly_grounded_card_says_so_with_its_score():
    body = _task(validation_status="reject", grounding_score=0.67).rendered_description()
    assert "67%" in body
    assert "⚠️" in body


def test_an_assumed_task_warns_that_the_sow_never_asked_for_it():
    body = _task(source_status="assumed").rendered_description()
    assert "assumption" in body.lower()
    assert "⚠️" in body


def test_a_test_case_resting_on_an_assumption_reaches_the_person_who_runs_it():
    # This is the warning that matters most: without it a QA engineer sees a
    # passing test and reports the system correct, having checked a number the
    # platform invented.
    body = _task(assumed_test_fields=["earn_rate"]).rendered_description()

    assert "earn_rate" in body
    assert "never stated" in body


def test_acceptance_criteria_reach_the_card_as_a_tickable_list():
    # A count told the person doing the work that two conditions existed and
    # sent them to another system to find out what they were. The conditions
    # are the whole point: without them "done" is an opinion.
    criteria = ["Points accrue at 10 per 1 USD.", "Points expire after 12 months."]
    task = _task(acceptance_criteria=criteria)

    assert task.checklists() == [("Acceptance criteria", criteria)]


def test_the_criteria_are_not_also_dumped_into_the_body():
    # They are on the card as a checklist; repeating them as prose would make
    # the block long enough that its warnings get skipped.
    body = _task(acceptance_criteria=["Points accrue at 10 per 1 USD."]).rendered_description()
    assert "Points accrue" not in body


def test_a_card_with_no_criteria_has_no_checklist():
    assert _task().checklists() == []


def test_a_checklist_never_carries_an_empty_name():
    # Trello rejects an empty check item, which would fail the whole push.
    for name, items in _task(acceptance_criteria=["a", "b"]).checklists():
        assert name.strip()
        assert all(i.strip() for i in items)


def test_a_task_with_no_description_still_renders():
    body = _task(description="").rendered_description()
    assert "no description" in body


def test_due_date_is_carried_for_the_card():
    task = _task(due_date=date(2026, 12, 1))
    assert task.due_date and task.due_date.isoformat() == "2026-12-01"


def test_test_cases_reach_the_card_as_their_own_checklist():
    task = _task(
        acceptance_criteria=["Points accrue at 10 per 1 USD."],
        test_cases=[BoardCase(title="Verify accrual", expected_result="100 points credited.")],
    )

    names = [name for name, _ in task.checklists()]
    assert names == ["Acceptance criteria", "Test cases"]


def test_a_test_case_item_carries_the_condition_that_makes_it_pass():
    # A tick has to assert something. "Verify accrual" alone does not say what
    # was verified.
    item = BoardCase(title="Verify accrual", expected_result="100 points credited.").as_item()
    assert "Verify accrual" in item
    assert "100 points credited." in item


def test_a_case_resting_on_an_assumption_is_marked_on_its_own_line():
    # The card-level warning says some test rests on an assumption; this says
    # which one, so the tester does not have to guess before signing it off.
    item = BoardCase(
        title="Validate accrual rate",
        expected_result="Rate holds at 10 per 1 USD.",
        rests_on_assumption=True,
        assumed_fields=["earn_rate"],
    ).as_item()

    assert item.startswith("⚠️")
    assert "earn_rate" in item


def test_a_sound_case_carries_no_warning_mark():
    item = BoardCase(title="Verify accrual", expected_result="100 points.").as_item()
    assert "⚠️" not in item


def test_a_case_with_no_expected_result_still_produces_a_usable_line():
    assert BoardCase(title="Smoke test").as_item() == "Smoke test"


def test_a_task_with_no_tests_gets_no_test_checklist():
    names = [name for name, _ in _task(acceptance_criteria=["a"]).checklists()]
    assert names == ["Acceptance criteria"]


class RecordingTrello(TrelloAdapter):
    """A TrelloAdapter whose HTTP layer is replaced by a script and a log.

    The checklist rules below are the ones worth pinning down: they decide
    whether a PM fixing a typo silently destroys a QA engineer's record of what
    has been verified.
    """

    def __init__(self, checklists):
        self.api_key, self.token = "k", "t"
        self.http = None
        self._checklists = checklists
        self.calls: list[tuple[str, str]] = []

    def _request(self, method, path, **params):
        self.calls.append((method, path))
        if path.endswith("/checklists") and method == "GET":
            return self._checklists
        if path.endswith("/labels") and method == "GET":
            return []
        if path == "/checklists":
            return {"id": "new-checklist"}
        return {"id": "x"}


def _checklist(name: str, *items: str) -> dict:
    """A checklist as Trello returns it, positions spaced out the way it spaces them."""
    return {
        "id": f"cl-{name}",
        "name": name,
        "checkItems": [
            {"id": f"ci-{item}", "name": item, "pos": i * 16384}
            for i, item in enumerate(items, start=1)
        ],
    }


def test_an_unchanged_checklist_is_left_alone():
    task = _task(acceptance_criteria=["Points accrue", "Receipt shows the balance"])
    trello = RecordingTrello([_checklist("Acceptance criteria", *task.checklists()[0][1])])

    trello._sync_checklists("card-1", task)

    assert not [c for c in trello.calls if c[0] != "GET"]


def test_adding_a_criterion_touches_only_the_new_line():
    task = _task(acceptance_criteria=["Points accrue", "Receipt shows the balance"])
    first = task.checklists()[0][1][0]
    trello = RecordingTrello([_checklist("Acceptance criteria", first)])

    trello._sync_checklists("card-1", task)

    # The checklist is never deleted, so a tick on the first criterion survives
    # somebody adding a second one.
    assert not [c for c in trello.calls if c[0] == "DELETE"]
    assert ("POST", "/checklists/cl-Acceptance criteria/checkItems") in trello.calls


def test_removing_a_criterion_deletes_that_item_alone():
    task = _task(acceptance_criteria=["Points accrue"])
    kept = task.checklists()[0][1][0]
    trello = RecordingTrello([_checklist("Acceptance criteria", kept, "A line nobody kept")])

    trello._sync_checklists("card-1", task)

    assert ("DELETE", "/cards/card-1/checkItem/ci-A line nobody kept") in trello.calls
    assert not [c for c in trello.calls if c[1] == "/checklists/cl-Acceptance criteria"]


def test_reordering_moves_items_rather_than_recreating_them():
    task = _task(acceptance_criteria=["First", "Second"])
    first, second = task.checklists()[0][1]
    trello = RecordingTrello([_checklist("Acceptance criteria", second, first)])

    trello._sync_checklists("card-1", task)

    # A PUT keeps the item's state, so each tick moves with the line it is on.
    assert [c for c in trello.calls if c[0] == "PUT"] == [
        ("PUT", f"/cards/card-1/checkItem/ci-{first}"),
        ("PUT", f"/cards/card-1/checkItem/ci-{second}"),
    ]
    assert not [c for c in trello.calls if c[0] == "DELETE"]


def test_a_checklist_the_task_no_longer_has_is_removed():
    task = _task(acceptance_criteria=["Points accrue"])
    trello = RecordingTrello(
        [
            _checklist("Acceptance criteria", *_task(acceptance_criteria=["Points accrue"]).checklists()[0][1]),
            _checklist("Test cases", "An old case nobody kept"),
        ]
    )

    trello._sync_checklists("card-1", task)

    # Leaving it would let the card keep asserting something the plan dropped.
    assert ("DELETE", "/checklists/cl-Test cases") in trello.calls


def test_a_deleted_card_reports_itself_gone_rather_than_raising():
    class Gone(RecordingTrello):
        def _request(self, method, path, **params):
            if method == "PUT":
                raise TrelloError("PUT /cards/x -> 404: not found", 404)
            return super()._request(method, path, **params)

    assert Gone([]).update_task("board-1", "card-1", _task()) is False


def test_a_real_trello_failure_is_not_mistaken_for_a_deleted_card():
    class Broken(RecordingTrello):
        def _request(self, method, path, **params):
            if method == "PUT":
                raise TrelloError("PUT /cards/x -> 500: server error", 500)
            return super()._request(method, path, **params)

    # Swallowing this as "the card is gone" would create a duplicate card every
    # time Trello had a bad minute.
    with pytest.raises(TrelloError):
        Broken([]).update_task("board-1", "card-1", _task())


def test_every_label_colour_is_one_trello_accepts():
    """A colour Trello rejects fails the whole push, not just its own label.

    Labels are created before any card, so `light-gray` — which is not in
    Trello's set — turned an edited task into a 400 that stopped every card
    from going up. Editing a task sets validation_status to "pending", which is
    exactly how the untested NOT-CHECKED label got reached for the first time.
    """
    invalid = {
        name: color
        for name, color in LABEL_COLORS.items()
        if color not in TRELLO_LABEL_COLORS
    }
    assert not invalid, f"Trello will reject these: {invalid}"
    assert {ROLE_COLOR, FALLBACK_COLOR} <= TRELLO_LABEL_COLORS


def test_an_edited_task_gets_a_pushable_colour_for_every_label():
    """The exact path that failed: edit a task, then push it."""
    task = _task()
    task.validation_status = "pending"  # what apply_edit leaves behind
    task.source_status = "something the table has never seen"

    colours = [label_color(name) for name in task.labels()]

    assert colours, "a task with no labels would hide its team and priority"
    assert all(c in TRELLO_LABEL_COLORS for c in colours), dict(
        zip(task.labels(), colours, strict=True)
    )


def test_credentials_never_reach_the_url():
    """httpx logs request URLs at INFO, so a URL is a place secrets get printed.

    This is not hypothetical: running the board check with INFO logging on
    printed a working key and token to the console in full.
    """
    import httpx

    from app.taskmanager.trello import API, TrelloAdapter

    adapter = TrelloAdapter(api_key="KEY-SHOULD-NOT-APPEAR", token="TOKEN-SHOULD-NOT-APPEAR")
    seen = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={})

    adapter.http = httpx.Client(
        transport=httpx.MockTransport(capture), headers=dict(adapter.http.headers)
    )
    adapter._request("GET", "/boards/b1/lists", fields="name")

    assert seen["url"].startswith(API)
    assert "SHOULD-NOT-APPEAR" not in seen["url"], seen["url"]
    assert "KEY-SHOULD-NOT-APPEAR" in seen["auth"]
