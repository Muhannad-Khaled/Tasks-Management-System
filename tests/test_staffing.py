"""Automatic staffing propagates what a human said, and refuses to guess."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.graph.staffing import autostaff, candidates_for
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.main import app
from app.models import Person, PersonRole, Project, ProjectRole
from app.schemas.enums import ProjectStatus
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


def _run(db, name: str = "Staffed") -> Project:
    sow = CORPUS / "sow_a_cairomart.pdf"
    doc = parse_document(sow, "SOW-001")
    citations = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()][:2]
    project = Project(name=name, status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    run_sow_pipeline(db, project.id, str(sow), "SOW-001", client=StubLLM(citations=citations))
    return project


def _role(db, project, title: str) -> ProjectRole:
    return (
        db.query(ProjectRole)
        .filter(ProjectRole.project_id == project.id, ProjectRole.role_title == title)
        .first()
    )


def test_the_only_person_who_can_do_a_role_is_put_on_it(db):
    """The whole point: upload a SOW and it arrives staffed."""
    ahmed = _person(db, "Ahmed Hassan", "Backend Engineer")

    project = _run(db)

    role = _role(db, project, "Backend Engineer")
    assert role is not None, "the sample SOW asks for a Backend Engineer"
    assert role.person_id == ahmed.id


def test_two_candidates_leaves_the_role_open_and_says_so(db):
    """A choice between two real people is not the system's to make."""
    _person(db, "Ahmed Hassan", "Backend Engineer")
    _person(db, "Sara Nabil", "Backend Engineer")

    project = _run(db)

    role = _role(db, project, "Backend Engineer")
    assert role.person_id is None
    notes = autostaff(db, project.id)
    assert any("2 people can do this" in n and "Backend Engineer" in n for n in notes)


def test_nobody_for_a_role_is_reported_rather_than_left_silent(db):
    project = _run(db)

    notes = autostaff(db, project.id)

    assert any("nobody in the directory does this yet" in n for n in notes)


def test_somebody_wearing_two_hats_is_staffed_on_both(db):
    """A small team is the normal case, not the exception."""
    both = _person(db, "Ahmed Hassan", "QA Engineer", "Support Agent")

    project = _run(db)

    assert _role(db, project, "QA Engineer").person_id == both.id
    assert _role(db, project, "Support Agent").person_id == both.id


def test_a_choice_the_pm_already_made_is_never_overwritten(db):
    """A rule must not quietly replace a human decision."""
    chosen = _person(db, "Sara Nabil")
    _person(db, "Ahmed Hassan", "Backend Engineer")
    project = _run(db)
    role = _role(db, project, "Backend Engineer")
    role.person_id = chosen.id
    db.commit()

    autostaff(db, project.id)
    # Committed before reading back. autostaff leaves that to its caller, and
    # refresh() on an uncommitted session discards the pending write — which
    # made this assertion pass even with the guard deleted.
    db.commit()

    assert role.person_id == chosen.id
    assert db.get(ProjectRole, role.id).person_id == chosen.id


def test_somebody_who_left_is_not_staffed(db):
    person = _person(db, "Ahmed Hassan", "Backend Engineer")
    person.active = False
    db.commit()

    project = _run(db)

    assert _role(db, project, "Backend Engineer").person_id is None


def test_a_role_nobody_declared_matches_nobody(db):
    """Names carry no information about what somebody does."""
    _person(db, "Ahmed Hassan")  # in the directory, no roles

    assert candidates_for(db, "Backend Engineer") == []


def test_an_unknown_role_title_is_not_stored(client):
    """A title the roster does not know could never match a project role."""
    person = client.post(
        "/people",
        json={"name": "Ahmed Hassan", "roles": ["Backend Engineer", "Wizard"]},
    ).json()

    assert person["roles"] == ["Backend Engineer"]


def test_changing_the_directory_leaves_reviewed_plans_alone(client, db):
    """A plan the PM has already looked at must not restaff itself."""
    project = _run(db)
    assert _role(db, project, "Backend Engineer").person_id is None

    person = client.post(
        "/people", json={"name": "Ahmed Hassan", "roles": ["Backend Engineer"]}
    ).json()
    client.patch(
        f"/people/{person['id']}", json={"name": "Ahmed Hassan", "roles": ["QA Engineer"]}
    )

    db.expire_all()
    assert _role(db, project, "Backend Engineer").person_id is None


def test_the_roster_offered_matches_the_one_staffing_compares_against(client):
    """Two lists that could disagree would stop matching without a word."""
    from app.schemas.roles import TEAM_ROLES

    served = {r["title"] for r in client.get("/people/roles").json()}

    assert served == {r.title for r in TEAM_ROLES}


def _scheduled(db, project, title: str, start, end, who: str):
    """A task with dates, handed to somebody by name."""
    from app.models import ProjectTask

    task = ProjectTask(
        project_id=project.id,
        title=title,
        team="technical",
        assignee=who,
        start_date=start,
        due_date=end,
    )
    db.add(task)
    db.commit()
    return task


def test_one_person_on_two_tasks_at_once_is_reported(db):
    from datetime import date

    from app.validation.pipeline import validate_staffing

    project = Project(name="Clash", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    tasks = [
        _scheduled(db, project, "Build the API", date(2026, 10, 1), date(2026, 10, 20), "Karim"),
        _scheduled(db, project, "Run the tests", date(2026, 10, 15), date(2026, 10, 25), "Karim"),
    ]

    result = validate_staffing(db, tasks)

    assert not result.passed
    assert "Karim" in result.detail
    assert "6 day(s) overlapping" in result.detail
    assert sorted(result.offending_task_ids) == sorted(t.id for t in tasks)


def test_a_long_task_clashing_with_a_later_one_is_not_missed(db):
    """The case comparing neighbours would miss.

    Sorted by start date, the short job in the middle clashes with nothing, so
    a pairwise walk stops there and never checks the long task against the one
    after it.
    """
    from datetime import date

    from app.validation.pipeline import validate_staffing

    project = Project(name="Long", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    tasks = [
        _scheduled(db, project, "Build", date(2026, 10, 1), date(2026, 10, 30), "Karim"),
        _scheduled(db, project, "Quick call", date(2026, 10, 3), date(2026, 10, 4), "Karim"),
        _scheduled(db, project, "Deploy", date(2026, 10, 20), date(2026, 10, 22), "Karim"),
    ]

    result = validate_staffing(db, tasks)

    assert result.detail.count("at the same time") == 2, result.detail
    assert "'Build' and 'Deploy'" in result.detail


def test_back_to_back_work_is_not_a_clash(db):
    from datetime import date

    from app.validation.pipeline import validate_staffing

    project = Project(name="Sequential", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    tasks = [
        _scheduled(db, project, "First", date(2026, 10, 1), date(2026, 10, 5), "Karim"),
        _scheduled(db, project, "Second", date(2026, 10, 6), date(2026, 10, 9), "Karim"),
    ]

    assert validate_staffing(db, tasks).passed


def test_finishing_and_starting_the_same_day_counts_as_a_clash(db):
    """Not a clean handover — it is one person working both that day."""
    from datetime import date

    from app.validation.pipeline import validate_staffing

    project = Project(name="Same day", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    tasks = [
        _scheduled(db, project, "First", date(2026, 10, 1), date(2026, 10, 5), "Karim"),
        _scheduled(db, project, "Second", date(2026, 10, 5), date(2026, 10, 9), "Karim"),
    ]

    assert not validate_staffing(db, tasks).passed


def test_two_people_overlapping_is_the_normal_case(db):
    """Teams work in parallel; only one person in two places is a problem."""
    from datetime import date

    from app.validation.pipeline import validate_staffing

    project = Project(name="Parallel", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    tasks = [
        _scheduled(db, project, "Build", date(2026, 10, 1), date(2026, 10, 20), "Karim"),
        _scheduled(db, project, "Sell", date(2026, 10, 1), date(2026, 10, 20), "Sara"),
    ]

    assert validate_staffing(db, tasks).passed


def test_an_unstaffed_task_cannot_clash(db):
    """Nobody is on it, so it takes nobody's time."""
    from datetime import date

    from app.validation.pipeline import validate_staffing

    project = Project(name="Open", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    tasks = [
        _scheduled(db, project, "One", date(2026, 10, 1), date(2026, 10, 20), ""),
        _scheduled(db, project, "Two", date(2026, 10, 5), date(2026, 10, 9), ""),
    ]

    assert validate_staffing(db, tasks).passed


def test_the_clash_follows_the_role_holder_not_just_the_task_field(db):
    """Most tasks name a role, not a person; the check has to resolve it."""
    from datetime import date

    from app.models import ProjectRole, ProjectTask
    from app.validation.pipeline import validate_staffing

    karim = _person(db, "Karim Tarek", "Backend Engineer")
    project = Project(name="ByRole", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    db.add(
        ProjectRole(
            project_id=project.id,
            team="technical",
            role_title="Backend Engineer",
            person_id=karim.id,
        )
    )
    tasks = [
        ProjectTask(
            project_id=project.id,
            title=title,
            team="technical",
            assignee_role="Backend Engineer",
            start_date=start,
            due_date=end,
        )
        for title, start, end in [
            ("Build", date(2026, 10, 1), date(2026, 10, 20)),
            ("Fix", date(2026, 10, 10), date(2026, 10, 15)),
        ]
    ]
    db.add_all(tasks)
    db.commit()

    result = validate_staffing(db, tasks)

    assert not result.passed
    assert "Karim Tarek" in result.detail
