"""Dependency validation and critical-path scheduling.

Expected values here are computed by hand rather than captured from the code,
so a regression in the CPM maths fails the test instead of updating it.
"""

from datetime import date

import pytest

from app.planning.dependencies import build_dependency_graph
from app.planning.schedule import PlanTask, add_working_days, compute_schedule

# A deliberately chosen Monday, so weekend handling is unambiguous.
MONDAY = date(2026, 10, 5)


def _graph(deps: dict[str, list[str]]):
    return build_dependency_graph(deps)


def test_valid_chain_has_no_issues():
    graph = _graph({"A": [], "B": ["A"], "C": ["B"]})
    assert graph.is_valid
    assert graph.topological_order() == ["A", "B", "C"]


def test_self_dependency_is_removed_and_reported():
    graph = _graph({"A": ["A"], "B": ["A"]})
    assert not graph.is_valid
    assert graph.issues[0].kind == "self_reference"
    assert graph.dependencies_of("A") == []


def test_dependency_on_a_task_that_does_not_exist_is_removed():
    graph = _graph({"A": ["GHOST"], "B": ["A"]})
    kinds = [i.kind for i in graph.issues]
    assert "unknown_task" in kinds
    assert graph.dependencies_of("A") == []
    assert graph.topological_order()  # still schedulable


def test_circular_dependency_is_broken_and_named():
    # A -> B -> C -> A, the example from the brief.
    graph = _graph({"A": ["C"], "B": ["A"], "C": ["B"]})
    cycles = [i for i in graph.issues if i.kind == "cycle"]
    assert len(cycles) == 1
    assert "circular dependency" in cycles[0].detail
    assert graph.topological_order(), "graph must be schedulable after breaking the cycle"


def test_two_independent_cycles_are_both_broken():
    graph = _graph({"A": ["B"], "B": ["A"], "C": ["D"], "D": ["C"]})
    assert len([i for i in graph.issues if i.kind == "cycle"]) == 2
    assert len(graph.topological_order()) == 4


def test_working_days_skip_weekends():
    friday = date(2026, 10, 9)
    assert add_working_days(friday, 1) == date(2026, 10, 12)  # Monday
    assert add_working_days(MONDAY, 5) == date(2026, 10, 12)
    assert add_working_days(MONDAY, 0) == MONDAY


def test_serial_chain_schedule_and_critical_path():
    # 3 tasks of 2 days each, strictly serial: 6 working days, all critical.
    tasks = [
        PlanTask("A", "Contract", "commercial", estimated_hours=16),
        PlanTask("B", "Build", "technical", estimated_hours=16),
        PlanTask("C", "Train", "operations", estimated_hours=16),
    ]
    graph = _graph({"A": [], "B": ["A"], "C": ["B"]})
    schedule = compute_schedule(tasks, graph, MONDAY)

    assert schedule.duration_days == 6
    assert schedule.project_start == MONDAY
    assert schedule.project_end == date(2026, 10, 12)  # Mon + 5 working days
    assert [t.task_id for t in schedule.critical_path] == ["A", "B", "C"]
    assert all(t.slack_days == 0 for t in schedule.tasks.values())

    assert schedule.tasks["A"].start_date == MONDAY
    assert schedule.tasks["A"].due_date == date(2026, 10, 6)
    assert schedule.tasks["B"].start_date == date(2026, 10, 7)


def test_parallel_branch_gets_slack_and_is_not_critical():
    #        /-> B (1d) -\
    #  A(1d)              -> D(1d)     B has 3 days of slack, C has none.
    #        \-> C (4d) -/
    tasks = [
        PlanTask("A", "Start", estimated_hours=8),
        PlanTask("B", "Short", estimated_hours=8),
        PlanTask("C", "Long", estimated_hours=32),
        PlanTask("D", "End", estimated_hours=8),
    ]
    graph = _graph({"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]})
    schedule = compute_schedule(tasks, graph, MONDAY)

    assert schedule.duration_days == 6  # 1 + 4 + 1
    assert schedule.tasks["B"].slack_days == 3
    assert not schedule.tasks["B"].is_critical
    assert schedule.tasks["C"].slack_days == 0
    assert [t.task_id for t in schedule.critical_path] == ["A", "C", "D"]


def test_task_without_an_estimate_still_occupies_a_day():
    tasks = [PlanTask("A", "Unknown effort"), PlanTask("B", "Next", estimated_hours=8)]
    schedule = compute_schedule(tasks, _graph({"A": [], "B": ["A"]}), MONDAY)
    assert schedule.tasks["A"].duration_days == 1
    assert schedule.duration_days == 2


@pytest.mark.parametrize("hours,expected", [(1, 1), (8, 1), (9, 2), (16, 2), (40, 5)])
def test_partial_days_round_up(hours, expected):
    # Half a day of work still consumes a day on a calendar.
    task = PlanTask("A", "T", estimated_hours=hours)
    assert task.duration_days == expected


def test_schedule_past_the_sow_deadline_is_flagged():
    tasks = [PlanTask("A", "Long build", estimated_hours=80)]  # 10 working days
    schedule = compute_schedule(tasks, _graph({"A": []}), MONDAY, deadline=date(2026, 10, 9))
    assert schedule.deadline_breach
    assert "past the SOW date" in schedule.deadline_breach


def test_schedule_within_the_deadline_is_not_flagged():
    tasks = [PlanTask("A", "Quick", estimated_hours=8)]
    schedule = compute_schedule(tasks, _graph({"A": []}), MONDAY, deadline=date(2026, 12, 1))
    assert schedule.deadline_breach is None


def test_empty_project_does_not_crash():
    schedule = compute_schedule([], _graph({}), MONDAY)
    assert schedule.tasks == {}
    assert schedule.critical_path == []


def test_project_starting_on_a_weekend_moves_to_monday():
    saturday = date(2026, 10, 3)
    schedule = compute_schedule(
        [PlanTask("A", "T", estimated_hours=8)], _graph({"A": []}), saturday
    )
    assert schedule.project_start == MONDAY
