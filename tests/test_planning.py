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


# --------------------------------------------------- estimates and honesty


def test_a_task_with_no_estimate_is_marked_as_running_on_the_default():
    # The scheduler silently used one day for anything unestimated, so a plan
    # where nine tasks in ten were never sized still produced dates that looked
    # measured.
    from app.planning.schedule import PlanTask

    assert PlanTask(task_id="T1", title="x").duration_is_assumed
    assert not PlanTask(task_id="T2", title="y", estimated_hours=16).duration_is_assumed


def test_a_zero_or_negative_estimate_counts_as_no_estimate():
    from app.planning.schedule import PlanTask

    assert PlanTask(task_id="T1", title="x", estimated_hours=0).duration_is_assumed
    assert PlanTask(task_id="T2", title="y", estimated_hours=-4).duration_is_assumed


def test_the_schedule_reports_how_much_of_it_was_estimated():
    from app.planning.dependencies import build_dependency_graph
    from app.planning.schedule import PlanTask, compute_schedule

    tasks = [
        PlanTask(task_id="T1", title="Sized", estimated_hours=16),
        PlanTask(task_id="T2", title="Unsized"),
        PlanTask(task_id="T3", title="Also unsized"),
    ]
    graph = build_dependency_graph({"T1": [], "T2": ["T1"], "T3": ["T2"]})

    schedule = compute_schedule(tasks, graph, date(2026, 10, 1))

    assert schedule.estimate_coverage == pytest.approx(1 / 3)
    assert {t.title for t in schedule.assumed_durations} == {"Unsized", "Also unsized"}


def test_a_fully_estimated_schedule_reports_full_coverage():
    from app.planning.dependencies import build_dependency_graph
    from app.planning.schedule import PlanTask, compute_schedule

    tasks = [
        PlanTask(task_id="T1", title="A", estimated_hours=8),
        PlanTask(task_id="T2", title="B", estimated_hours=24),
    ]
    schedule = compute_schedule(
        tasks, build_dependency_graph({"T1": [], "T2": ["T1"]}), date(2026, 10, 1)
    )

    assert schedule.estimate_coverage == 1.0
    assert schedule.assumed_durations == []


def test_the_assumed_flag_travels_onto_each_scheduled_task():
    from app.planning.dependencies import build_dependency_graph
    from app.planning.schedule import PlanTask, compute_schedule

    tasks = [
        PlanTask(task_id="T1", title="Sized", estimated_hours=40),
        PlanTask(task_id="T2", title="Unsized"),
    ]
    schedule = compute_schedule(
        tasks, build_dependency_graph({"T1": [], "T2": ["T1"]}), date(2026, 10, 1)
    )

    assert schedule.tasks["T1"].duration_is_assumed is False
    assert schedule.tasks["T2"].duration_is_assumed is True
    # A five-day task and a defaulted one must not look alike.
    assert schedule.tasks["T1"].duration_days == 5
    assert schedule.tasks["T2"].duration_days == 1


def test_an_empty_project_does_not_claim_missing_estimates():
    from app.planning.dependencies import build_dependency_graph
    from app.planning.schedule import compute_schedule

    schedule = compute_schedule([], build_dependency_graph({}), date(2026, 10, 1))
    assert schedule.estimate_coverage == 1.0


# ------------------------------------------------- the SOW's own milestones


def _check(target, dues):
    from app.planning.milestones import check_milestones

    return check_milestones([("Integration complete", target, [(f"T{i}", d) for i, d in enumerate(dues)])])[0]


def test_a_milestone_the_plan_misses_is_late():
    from app.planning.milestones import MilestoneStatus

    check = _check(date(2026, 11, 10), [date(2026, 11, 20)])

    assert check.status == MilestoneStatus.LATE
    assert check.variance_days == 10
    assert "past the agreed 2026-11-10" in check.detail


def test_a_milestone_the_plan_lands_far_before_is_flagged_too():
    # Six weeks early is not being ahead. It is what a schedule looks like when
    # nobody sized the work, and it was invisible while only the final deadline
    # was checked.
    from app.planning.milestones import MilestoneStatus

    check = _check(date(2026, 11, 10), [date(2026, 10, 5)])

    assert check.status == MilestoneStatus.EARLY
    assert check.variance_days == -36
    assert "before the agreed" in check.detail


def test_landing_slightly_early_is_on_track_not_a_warning():
    from app.planning.milestones import MilestoneStatus

    assert _check(date(2026, 11, 10), [date(2026, 11, 5)]).status == MilestoneStatus.ON_TRACK
    assert _check(date(2026, 11, 10), [date(2026, 11, 10)]).status == MilestoneStatus.ON_TRACK


def test_a_milestone_is_reached_when_its_last_task_finishes():
    check = _check(date(2026, 11, 10), [date(2026, 10, 5), date(2026, 11, 9), date(2026, 10, 1)])
    assert check.projected_date == date(2026, 11, 9)


def test_a_milestone_with_no_tasks_cannot_be_checked():
    from app.planning.milestones import MilestoneStatus

    check = _check(date(2026, 11, 10), [])

    assert check.status == MilestoneStatus.UNSCHEDULED
    assert check.variance_days is None
    assert "no scheduled task" in check.detail


def test_a_milestone_with_no_date_is_not_treated_as_a_breach():
    from app.planning.milestones import MilestoneStatus

    check = _check(None, [date(2026, 10, 5)])
    assert check.status == MilestoneStatus.UNSCHEDULED


def test_the_summary_separates_breaches_from_warnings():
    from app.planning.milestones import check_milestones, milestone_summary

    checks = check_milestones(
        [
            ("Late one", date(2026, 11, 10), [("a", date(2026, 11, 20))]),
            ("Early one", date(2026, 11, 10), [("b", date(2026, 9, 1))]),
            ("Fine one", date(2026, 11, 10), [("c", date(2026, 11, 8))]),
        ]
    )
    summary = milestone_summary(checks)

    assert summary["late"] == 1 and summary["early"] == 1 and summary["on_track"] == 1
    # Only the breach fails a build; being early is a signal, not a broken promise.
    assert len(summary["breaches"]) == 1
    assert "Late one" in summary["breaches"][0]
    assert any("Early one" in w for w in summary["warnings"])


# ------------------------------- calibrating estimates against the SOW


def _segments(start, milestones):
    """milestones: [(name, target, projected)]"""
    from app.planning.milestones import MilestoneCheck, calibrate

    checks = [
        MilestoneCheck(name=n, target_date=t, projected_date=p) for n, t, p in milestones
    ]
    return calibrate(start, checks)


def test_a_step_the_plan_underfills_is_measured_against_its_own_window():
    # The SOW gives integration 28 working days; the plan uses 9. That gap is
    # invisible in a whole-project total, which is why the comparison is made
    # between consecutive checkpoints.
    segments = _segments(
        date(2026, 10, 1),
        [
            ("Contract", date(2026, 10, 1), date(2026, 10, 1)),
            ("Integration", date(2026, 11, 10), date(2026, 10, 14)),
        ],
    )

    integration = segments[1]
    assert integration.after == "Contract"
    assert integration.allowed_days == 28
    assert integration.planned_days == 9
    assert integration.unaccounted_days == 19
    assert integration.coverage == pytest.approx(9 / 28)


def test_a_step_needing_more_than_the_sow_allows_reports_over_full():
    # Operations steps came out over 100%: the plan needs more time than was
    # agreed, which is the opposite problem and must not read as slack.
    segments = _segments(
        date(2026, 10, 1),
        [
            ("Config", date(2026, 10, 2), date(2026, 10, 2)),
            ("Training", date(2026, 10, 5), date(2026, 10, 8)),
        ],
    )

    training = segments[1]
    assert training.coverage > 1.0
    assert training.unaccounted_days == 0


def test_the_first_step_is_measured_from_the_project_start():
    segments = _segments(date(2026, 10, 1), [("Contract", date(2026, 10, 8), date(2026, 10, 6))])
    assert segments[0].after == ""
    assert segments[0].allowed_days == 5
    assert segments[0].planned_days == 3


def test_a_milestone_without_both_dates_is_left_out_of_calibration():
    segments = _segments(
        date(2026, 10, 1),
        [
            ("No target", None, date(2026, 10, 5)),
            ("No projection", date(2026, 10, 9), None),
            ("Complete", date(2026, 10, 9), date(2026, 10, 6)),
        ],
    )
    assert [s.name for s in segments] == ["Complete"]


def test_a_zero_width_window_cannot_be_scored():
    # The SOW puts contract signature on the project's own start date, so it
    # allows no working days at all.
    segments = _segments(date(2026, 10, 1), [("Contract", date(2026, 10, 1), date(2026, 10, 8))])
    assert segments[0].allowed_days == 0
    assert segments[0].coverage is None
    assert "cannot be compared" in segments[0].detail


def test_the_summary_points_at_the_widest_gaps_first():
    from app.planning.milestones import calibration_summary

    segments = _segments(
        date(2026, 10, 1),
        [
            ("Small gap", date(2026, 10, 8), date(2026, 10, 7)),
            ("Huge gap", date(2026, 11, 20), date(2026, 10, 14)),
        ],
    )
    summary = calibration_summary(segments)

    assert summary["widest_gaps"][0].startswith("Huge gap")
    assert summary["coverage"] is not None
    assert summary["unaccounted_days"] > 0


def test_calibration_of_nothing_is_empty_rather_than_an_error():
    from app.planning.milestones import calibration_summary

    summary = calibration_summary([])
    assert summary["segments"] == 0
    assert summary["coverage"] is None
    assert summary["widest_gaps"] == []


def test_tasks_attach_themselves_to_the_milestone_they_name(db):
    # The link runs task -> milestone. While it ran the other way, milestones
    # had to name task ids that did not exist yet, which forced them to be
    # extracted last — and there the model dropped them entirely.
    from app.graph.persistence import persist_document, persist_extraction
    from app.ingestion.parser import parse_document
    from app.models import Project, ProjectMilestone
    from app.schemas.enums import ProjectStatus
    from tests.factories import CORPUS, StubLLM

    doc = parse_document(CORPUS / "sow_a_cairomart.pdf", "SOW-001")
    project = Project(name="Milestones", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    _, chunks = persist_document(db, project, doc, "unused.pdf", "ok")

    _, warnings = persist_extraction(
        db, project, StubLLM(citations=list(chunks)[:2])._extraction(), chunks
    )
    db.commit()

    rows = {
        m.name: m
        for m in db.query(ProjectMilestone).filter(ProjectMilestone.project_id == project.id)
    }
    assert set(rows) == {"Contract signed", "Go-live"}
    assert len(rows["Contract signed"].tasks) == 1
    assert len(rows["Go-live"].tasks) == 2
    assert not [w for w in warnings if "no task attached" in w]


def test_a_task_naming_a_milestone_the_sow_lacks_is_reported(db):
    from app.graph.persistence import persist_document, persist_extraction
    from app.ingestion.parser import parse_document
    from app.models import Project
    from app.schemas.enums import ProjectStatus
    from tests.factories import CORPUS, StubLLM

    doc = parse_document(CORPUS / "sow_a_cairomart.pdf", "SOW-001")
    project = Project(name="Bad milestone", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    _, chunks = persist_document(db, project, doc, "unused.pdf", "ok")
    extraction = StubLLM(citations=list(chunks)[:2])._extraction()
    extraction.tasks[0].milestone = "Phase 9 handover"

    _, warnings = persist_extraction(db, project, extraction, chunks)
    db.commit()

    assert any("Phase 9 handover" in w for w in warnings)


def test_a_milestone_nothing_attaches_to_is_reported(db):
    # Its date cannot be compared with anything, so a silent row would be a
    # promise nobody ever checks.
    from app.graph.persistence import persist_document, persist_extraction
    from app.ingestion.parser import parse_document
    from app.models import Project
    from app.schemas.enums import ProjectStatus
    from tests.factories import CORPUS, StubLLM

    doc = parse_document(CORPUS / "sow_a_cairomart.pdf", "SOW-001")
    project = Project(name="Orphan milestone", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    _, chunks = persist_document(db, project, doc, "unused.pdf", "ok")
    extraction = StubLLM(citations=list(chunks)[:2])._extraction()
    for task in extraction.tasks:
        task.milestone = "Contract signed"

    _, warnings = persist_extraction(db, project, extraction, chunks)
    db.commit()

    assert any("Go-live" in w and "no task attached" in w for w in warnings)
