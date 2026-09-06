"""Roles, the roster, and task assignment.

A task that names a role nobody holds is no better than one naming nobody, so
the matcher's job is to always land on a real role and say when it corrected
something.
"""

import pytest

from app.graph.persistence import persist_document, persist_extraction
from app.ingestion.parser import parse_document
from app.models import Project, ProjectRequirement, ProjectRole, ProjectTask
from app.schemas.enums import ProjectStatus, SourceStatus
from app.schemas.roles import TEAM_ROLES, match_role, roles_for_team
from tests.factories import CORPUS, StubLLM


@pytest.fixture
def seeded(db):
    """A project with SOW A parsed and persisted, ready for an extraction."""
    doc = parse_document(CORPUS / "sow_a_cairomart.pdf", "SOW-001")
    project = Project(name="Roles", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    _, chunks_by_key = persist_document(db, project, doc, "unused.pdf", "ok")
    citations = list(chunks_by_key)[:2]
    return project, chunks_by_key, citations


@pytest.mark.parametrize(
    "team,raw,expected",
    [
        ("technical", "Backend Engineer", "Backend Engineer"),
        ("technical", "backend developer", "Backend Engineer"),
        ("technical", "ML Engineer", "AI Engineer"),
        ("technical", "QA", "QA Engineer"),
        ("operations", "ops manager", "Operations Manager"),
        ("operations", "trainer", "Merchant Success Specialist"),
        ("commercial", "lawyer", "Legal Advisor"),
    ],
)
def test_role_wording_resolves_to_a_real_role(team, raw, expected):
    role, warning = match_role(team, raw)
    assert role.title == expected
    assert not warning


def test_an_ambiguous_role_falls_back_rather_than_guessing():
    # "Software Engineer" shares only the generic word "Engineer" with five
    # technical roles. Picking one would be arbitrary.
    role, warning = match_role("technical", "Software Engineer")
    assert role.title == "Technical Lead"
    assert "unknown technical role" in warning


def test_a_role_from_another_team_is_corrected_and_reported():
    role, warning = match_role("technical", "Operations Manager")
    assert role.title == "Technical Lead"
    assert "belongs to the operations team" in warning


def test_every_team_has_exactly_one_lead_to_fall_back_to():
    for team in {r.team for r in TEAM_ROLES}:
        leads = [r for r in roles_for_team(team) if r.is_lead]
        assert len(leads) == 1, f"{team} has {len(leads)} leads"


def test_an_empty_role_is_not_a_warning():
    # The model declining to assign is a silence, not a mistake.
    role, warning = match_role("commercial", "")
    assert role.title == "Commercial Manager"
    assert not warning


def test_a_sow_without_a_roster_gets_the_default_one_marked_assumed(db, seeded):
    project, chunks_by_key, citations = seeded
    extraction = StubLLM(citations=citations)._extraction()

    persist_extraction(db, project, extraction, chunks_by_key)

    roles = db.query(ProjectRole).filter(ProjectRole.project_id == project.id).all()
    assert len(roles) == len(TEAM_ROLES)
    assert {r.source_status for r in roles} == {str(SourceStatus.ASSUMED)}


def test_a_role_the_sow_names_is_stored_verbatim(db, seeded):
    # Rewriting "Integration Architect" into "Technical Lead" would discard
    # something the document actually said.
    project, chunks_by_key, citations = seeded
    extraction = StubLLM(citations=citations, roster=True)._extraction()

    persist_extraction(db, project, extraction, chunks_by_key)

    roles = db.query(ProjectRole).filter(ProjectRole.project_id == project.id).all()
    stated = [r for r in roles if r.source_status == str(SourceStatus.EXPLICIT)]
    assert [r.role_title for r in stated] == ["Integration Architect"]


def test_a_role_the_sow_named_is_a_valid_assignment_target(db, seeded):
    project, chunks_by_key, citations = seeded
    extraction = StubLLM(citations=citations, roster=True)._extraction()
    technical = next(t for t in extraction.tasks if t.team == "technical")
    technical.assignee_role = "Integration Architect"

    _, warnings = persist_extraction(db, project, extraction, chunks_by_key)

    task = db.query(ProjectTask).filter(ProjectTask.title == technical.title).one()
    assert task.assignee_role == "Integration Architect"
    # It was accepted as given, not corrected to the team lead.
    assert not any("Integration Architect" in w and "unknown" in w for w in warnings)


def test_every_task_lands_on_a_role(db, seeded):
    project, chunks_by_key, citations = seeded
    extraction = StubLLM(citations=citations)._extraction()

    tasks, warnings = persist_extraction(db, project, extraction, chunks_by_key)

    assert all(t.assignee_role for t in tasks)
    assert not warnings


def test_an_invented_role_is_corrected_and_the_correction_is_reported(db, seeded):
    project, chunks_by_key, citations = seeded
    extraction = StubLLM(citations=citations)._extraction()
    extraction.tasks[1].assignee_role = "Chief Blockchain Officer"

    tasks, warnings = persist_extraction(db, project, extraction, chunks_by_key)

    technical = next(t for t in tasks if t.team == "technical")
    assert technical.assignee_role == "Technical Lead"
    assert any("Chief Blockchain Officer" in w for w in warnings)


def test_requirements_are_persisted_and_tasks_link_to_them(db, seeded):
    # Requirements were extracted and thrown away, breaking the
    # SOW -> requirement -> task chain at its middle link.
    project, chunks_by_key, citations = seeded
    extraction = StubLLM(citations=citations)._extraction()

    tasks, _ = persist_extraction(db, project, extraction, chunks_by_key)

    requirements = db.query(ProjectRequirement).filter(
        ProjectRequirement.project_id == project.id
    ).all()
    assert {r.requirement_key for r in requirements} == {"REQ-001", "REQ-002", "REQ-003"}
    assert all(t.requirement_id for t in tasks)
    assert all(t.requirement.team == t.team for t in tasks)


def test_a_task_citing_an_unknown_requirement_is_left_unlinked(db, seeded):
    project, chunks_by_key, citations = seeded
    extraction = StubLLM(citations=citations)._extraction()
    extraction.tasks[0].requirement_id = "REQ-999"

    tasks, warnings = persist_extraction(db, project, extraction, chunks_by_key)

    assert tasks[0].requirement_id is None
    assert any("REQ-999" in w for w in warnings)


def test_requirement_citations_that_are_not_in_the_document_are_dropped(db, seeded):
    project, chunks_by_key, _ = seeded
    extraction = StubLLM(citations=["SOW-001-S99-C99"])._extraction()

    persist_extraction(db, project, extraction, chunks_by_key)

    requirements = db.query(ProjectRequirement).filter(
        ProjectRequirement.project_id == project.id
    ).all()
    assert all(r.source_chunk_keys == "" for r in requirements)


def test_deleting_a_project_takes_its_requirements_and_roles(db, seeded):
    project, chunks_by_key, citations = seeded
    persist_extraction(db, project, StubLLM(citations=citations)._extraction(), chunks_by_key)

    db.delete(project)
    db.commit()

    assert db.query(ProjectRequirement).count() == 0
    assert db.query(ProjectRole).count() == 0


def test_a_partial_roster_is_completed_from_the_roles_tasks_actually_use(db, seeded):
    # A SOW that mentions one role in passing is not describing a one-person
    # team. Taking its word for the whole roster leaves the project showing an
    # Account Manager while its own tasks sit with engineers it does not have.
    project, chunks_by_key, citations = seeded
    extraction = StubLLM(citations=citations, roster=True)._extraction()
    assert len(extraction.team_roster) == 1, "the stub's SOW names one role"

    _, warnings = persist_extraction(db, project, extraction, chunks_by_key)

    roles = db.query(ProjectRole).filter(ProjectRole.project_id == project.id).all()
    listed = {(r.team, r.role_title) for r in roles}
    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project.id).all()
    for task in tasks:
        assert (task.team, task.assignee_role) in listed, (
            f"{task.assignee_role} owns a task but is not on the roster"
        )
    assert any("added as assumed" in w for w in warnings)


def test_roles_added_to_close_the_roster_are_marked_assumed(db, seeded):
    project, chunks_by_key, citations = seeded

    persist_extraction(
        db, project, StubLLM(citations=citations, roster=True)._extraction(), chunks_by_key
    )

    roles = db.query(ProjectRole).filter(ProjectRole.project_id == project.id).all()
    by_title = {r.role_title: r for r in roles}
    assert by_title["Integration Architect"].source_status == str(SourceStatus.EXPLICIT)
    # These were never in the SOW; the platform put them there.
    assert by_title["Backend Engineer"].source_status == str(SourceStatus.ASSUMED)


def test_a_complete_roster_is_left_alone(db, seeded):
    # Nothing should be added when every assigned role is already listed.
    project, chunks_by_key, citations = seeded

    _, warnings = persist_extraction(
        db, project, StubLLM(citations=citations)._extraction(), chunks_by_key
    )

    assert not any("added as assumed" in w for w in warnings)
    roles = db.query(ProjectRole).filter(ProjectRole.project_id == project.id).all()
    assert len(roles) == len(TEAM_ROLES)
