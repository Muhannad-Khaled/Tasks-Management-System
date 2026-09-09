"""The structure, roles and questions endpoints.

These serve the view a PM actually reads: the project as a tree of what each
team owns, what it must prove, and what nobody has answered yet.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.main import app
from app.models import Project
from app.schemas.enums import ProjectStatus, SourceStatus
from app.schemas.fields import PLANNING_FIELDS
from app.schemas.roles import TEAM_ROLES
from tests.factories import CORPUS, StubLLM


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
    row = Project(name="Structure", status=ProjectStatus.INGESTING)
    db.add(row)
    db.commit()
    run_sow_pipeline(db, row.id, str(sow), "SOW-001", client=StubLLM(citations=citations))
    return row


def test_structure_reports_the_whole_tree(client, project):
    data = client.get(f"/projects/{project.id}/structure").json()

    assert set(data["teams"]) == {"commercial", "technical", "operations"}
    totals = data["totals"]
    assert totals["requirements"] == 3
    assert totals["user_stories"] == 5
    assert totals["engineer_stories"] == 2
    assert totals["test_cases"] == 6
    assert totals["open_questions"] > 0


def test_a_requirement_carries_its_stories_criteria_and_cases(client, project):
    data = client.get(f"/projects/{project.id}/structure").json()

    commercial = data["teams"]["commercial"]["requirements"]
    requirement = next(r for r in commercial if r["requirement_id"] == "REQ-001")
    story = requirement["user_stories"][0]
    assert story["sentence"].startswith("As a merchant")
    assert len(story["acceptance_criteria"]) == 2
    assert story["test_cases"][0]["case_id"] == "TC-001"


def test_the_tree_marks_a_case_that_rests_on_an_assumption(client, project):
    # The stub's gap report leaves the earn rate assumed, so the accrual case
    # must arrive flagged rather than reading as fact.
    data = client.get(f"/projects/{project.id}/structure").json()

    technical = data["teams"]["technical"]["requirements"]
    cases = [c for r in technical for s in r["user_stories"] for c in s["test_cases"]]
    accrual = next(c for c in cases if c["case_id"] == "TC-002")

    assert accrual["rests_on_assumption"]
    assert accrual["assumed_fields"] == ["earn_rate"]
    assert data["totals"]["test_cases_resting_on_assumptions"] == 1


def test_the_tree_groups_detail_items_by_category(client, project):
    data = client.get(f"/projects/{project.id}/structure").json()

    assert "api" in data["teams"]["technical"]["details"]
    assert "offer" in data["teams"]["commercial"]["details"]
    assert "system_configuration" in data["teams"]["operations"]["details"]


def test_structure_404s_for_an_unknown_project(client):
    assert client.get("/projects/nope/structure").status_code == 404


def test_roles_are_served_with_their_provenance(client, project):
    roles = client.get(f"/projects/{project.id}/roles").json()

    assert len(roles) == len(TEAM_ROLES)
    assert {r["source_status"] for r in roles} == {str(SourceStatus.ASSUMED)}
    assert {r["team"] for r in roles} == {"commercial", "technical", "operations"}


def test_questions_are_grouped_by_who_answers_them(client, project):
    data = client.get(f"/projects/{project.id}/questions").json()

    assert set(data["by_scope"]) == {"merchant", "offer", "project"}
    # The stub settles one field, so everything else is still a question.
    assert data["total"] == len(PLANNING_FIELDS) - 1
    assert data["counts"]["merchant"] and data["counts"]["offer"]


def test_a_question_says_what_the_plan_runs_on_meanwhile(client, project):
    data = client.get(f"/projects/{project.id}/questions").json()

    asked = [q for items in data["by_scope"].values() for q in items]
    assert all(q["working_assumption"] for q in asked)
    assert all(q["status"] == "open" for q in asked)


def test_tasks_report_the_role_that_owns_them(client, project):
    tasks = client.get(f"/projects/{project.id}/tasks").json()
    assert all(t["assignee_role"] for t in tasks)


def test_each_requirement_names_the_task_that_delivers_it(client, project):
    # Without this the tree shows a requirement, the reader looks for its card
    # under that requirement's team, does not find it, and concludes the work
    # is missing.
    data = client.get(f"/projects/{project.id}/structure").json()

    for team in data["teams"].values():
        for requirement in team["requirements"]:
            assert requirement["tasks"], requirement["requirement_id"]
            for task in requirement["tasks"]:
                assert task["title"] and task["team"] and task["assignee_role"]


def test_a_requirement_delivered_by_another_team_says_so(db, client, project):
    # An SLA is a commercial promise kept by operations, so the card lands on a
    # different board column than the requirement's own team.
    from app.models import ProjectRequirement, ProjectTask

    requirement = (
        db.query(ProjectRequirement)
        .filter(ProjectRequirement.project_id == project.id, ProjectRequirement.team == "commercial")
        .first()
    )
    task = db.query(ProjectTask).filter(ProjectTask.requirement_id == requirement.id).first()
    task.team = "operations"
    db.commit()

    data = client.get(f"/projects/{project.id}/structure").json()

    moved = next(
        r
        for r in data["teams"]["commercial"]["requirements"]
        if r["requirement_id"] == requirement.requirement_key
    )
    assert moved["tasks"][0]["delivered_by_another_team"] is True
    assert moved["tasks"][0]["team"] == "operations"


def test_a_task_that_matches_its_requirements_team_is_not_flagged(client, project):
    data = client.get(f"/projects/{project.id}/structure").json()
    flagged = [
        t
        for team in data["teams"].values()
        for r in team["requirements"]
        for t in r["tasks"]
        if t["delivered_by_another_team"]
    ]
    assert not flagged, "the stub's tasks all match their requirement's team"


def test_a_task_with_no_requirement_is_surfaced_rather_than_hidden(db, client, project):
    from app.models import ProjectTask

    task = db.query(ProjectTask).filter(ProjectTask.project_id == project.id).first()
    team, title = task.team, task.title
    task.requirement_id = None
    db.commit()

    data = client.get(f"/projects/{project.id}/structure").json()

    assert title in [t["title"] for t in data["teams"][team]["unlinked_tasks"]]
