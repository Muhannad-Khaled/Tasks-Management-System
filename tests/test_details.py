"""What the SOW enumerates, and which team owns it.

The taxonomy is the point: an API belongs to the technical team whichever
section of the document happens to mention it, so the team is derived in code
rather than asked of the model.
"""

import pytest

from app.graph.persistence import persist_document, persist_extraction
from app.ingestion.parser import parse_document
from app.models import Project, ProjectDetail
from app.schemas.details import (
    CATEGORY_HELP,
    CATEGORY_TEAM,
    DetailCategory,
    categories_for_team,
    render_category_guide,
    team_for,
)
from app.schemas.enums import ProjectStatus, SourceStatus, Team
from tests.factories import CORPUS, StubLLM


@pytest.fixture
def seeded(db):
    doc = parse_document(CORPUS / "sow_a_cairomart.pdf", "SOW-001")
    project = Project(name="Details", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    _, chunks_by_key = persist_document(db, project, doc, "unused.pdf", "ok")
    return project, chunks_by_key, list(chunks_by_key)[:2]


def test_every_category_has_an_owning_team():
    assert set(CATEGORY_TEAM) == set(DetailCategory)


def test_every_category_is_explained_to_the_model():
    # An unexplained category gets filled by guesswork from its name.
    assert set(CATEGORY_HELP) == set(DetailCategory)


def test_every_team_owns_at_least_one_category():
    for team in Team:
        assert categories_for_team(team)


def test_the_category_guide_names_every_category():
    guide = render_category_guide()
    for category in DetailCategory:
        assert str(category) in guide


def test_the_team_comes_from_the_category():
    assert team_for(DetailCategory.API) is Team.TECHNICAL
    assert team_for(DetailCategory.OFFER) is Team.COMMERCIAL
    assert team_for(DetailCategory.TRAINING_TOPIC) is Team.OPERATIONS


def test_details_are_persisted_under_the_team_their_category_belongs_to(db, seeded):
    project, chunks_by_key, citations = seeded

    persist_extraction(db, project, StubLLM(citations=citations)._extraction(), chunks_by_key)

    rows = db.query(ProjectDetail).filter(ProjectDetail.project_id == project.id).all()
    by_category = {r.category: r for r in rows}
    assert by_category["offer"].team == str(Team.COMMERCIAL)
    assert by_category["api"].team == str(Team.TECHNICAL)
    assert by_category["system_configuration"].team == str(Team.OPERATIONS)


def test_details_keep_their_provenance(db, seeded):
    project, chunks_by_key, citations = seeded

    persist_extraction(db, project, StubLLM(citations=citations)._extraction(), chunks_by_key)

    rows = db.query(ProjectDetail).filter(ProjectDetail.project_id == project.id).all()
    assert {r.source_status for r in rows} == {
        str(SourceStatus.EXPLICIT),
        str(SourceStatus.INFERRED),
    }
    assert all(r.source_chunk_keys for r in rows)


def test_a_detail_citing_text_the_document_lacks_keeps_no_citation(db, seeded):
    project, chunks_by_key, _ = seeded

    persist_extraction(db, project, StubLLM(citations=["SOW-001-S99-C99"])._extraction(), chunks_by_key)

    rows = db.query(ProjectDetail).filter(ProjectDetail.project_id == project.id).all()
    assert rows and all(r.source_chunk_keys == "" for r in rows)


def test_re_extracting_replaces_details_rather_than_duplicating_them(db, seeded):
    project, chunks_by_key, citations = seeded
    extraction = StubLLM(citations=citations)._extraction()

    persist_extraction(db, project, extraction, chunks_by_key)
    first = db.query(ProjectDetail).filter(ProjectDetail.project_id == project.id).count()
    persist_extraction(db, project, extraction, chunks_by_key)
    second = db.query(ProjectDetail).filter(ProjectDetail.project_id == project.id).count()

    assert first == second


def test_deleting_a_project_takes_its_details(db, seeded):
    project, chunks_by_key, citations = seeded
    persist_extraction(db, project, StubLLM(citations=citations)._extraction(), chunks_by_key)

    db.delete(project)
    db.commit()

    assert db.query(ProjectDetail).count() == 0
