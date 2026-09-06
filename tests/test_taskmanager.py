"""Board representation.

The board is what the team acts on. A label there carries more weight than
anything in the app, so it must not overstate how well a task is supported —
and the card body must not bury a real warning under metadata nobody needs.
"""

from datetime import date

import pytest

from app.schemas.roles import TEAM_ROLES
from app.taskmanager.base import BoardCase, BoardTask
from app.taskmanager.labels import LABEL_COLORS, ROLE_PREFIX, label_color, role_label


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
