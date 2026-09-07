"""The audit has to say how far it looked, not only what it found.

A list of gaps with nothing beside it reads as the whole of what a SOW left
out. It is the whole of what the SOW left out *among the fields the audit
knows about* — and on a live run those were not the same thing: a document
requiring a data retention policy without stating a period produced no
question, because no field asks about retention.

For a platform whose claim is that it says what a document does not, a subject
that was checked and a subject that was never on the list cannot look alike.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.graph.assumptions import audit_coverage
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.main import app
from app.models import Project
from app.schemas.enums import ProjectStatus
from app.schemas.fields import PLANNING_FIELDS
from tests.factories import StubLLM

CORPUS = Path(__file__).parent.parent / "data" / "sample_sows"


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _run(db, name: str = "Coverage") -> Project:
    sow = CORPUS / "sow_a_cairomart.pdf"
    doc = parse_document(sow, "SOW-001")
    citations = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()][:2]
    project = Project(name=name, status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    run_sow_pipeline(db, project.id, str(sow), "SOW-001", client=StubLLM(citations=citations))
    return project


# --- the arithmetic -------------------------------------------------------


def test_every_field_is_accounted_for_as_answered_or_open():
    """No field may fall between the two, or the total stops meaning anything."""
    coverage = audit_coverage({"points_expiry", "training_sessions"})

    assert coverage["checked"] == len(PLANNING_FIELDS)
    assert coverage["answered"] + coverage["open"] == coverage["checked"]


def test_a_field_the_audit_settled_is_named():
    """Being able to see it was checked is the point of the whole report."""
    coverage = audit_coverage({"points_expiry"})

    named = {f["key"] for f in coverage["answered_fields"]}
    assert "merchant_count" in named
    assert "points_expiry" not in named


def test_a_question_naming_a_field_the_list_dropped_is_not_counted():
    """An old run can outlive the field it asked about.

    Counted, it would report more gaps than there are fields to have them in.
    """
    coverage = audit_coverage({"a_field_that_no_longer_exists"})

    assert coverage["open"] == 0
    assert coverage["answered"] == coverage["checked"]


def test_the_two_records_of_the_same_gaps_are_cross_checked():
    """Assumptions and questions come from one set of findings.

    If they disagree, the derivation behind 'answered' is not sound and saying
    so is better than publishing a number nobody can rely on.
    """
    assert audit_coverage({"points_expiry"}, assumption_count=1)["reliable"] is True
    assert audit_coverage({"points_expiry"}, assumption_count=6)["reliable"] is False


def test_the_boundary_is_stated_in_words_not_only_in_numbers():
    """The number alone does not tell a PM that silence is not a finding."""
    boundary = audit_coverage(set())["boundary"]

    assert str(len(PLANNING_FIELDS)) in boundary
    assert "not examined" in boundary


# --- what reaches the PM --------------------------------------------------


def test_the_questions_endpoint_reports_its_own_scope(client, db):
    project = _run(db)

    data = client.get(f"/projects/{project.id}/questions").json()

    coverage = data["coverage"]
    assert coverage["checked"] == len(PLANNING_FIELDS)
    assert coverage["open"] == data["total"], "the gaps and the scope must agree"
    assert coverage["boundary"]


def test_coverage_is_reported_even_when_the_sow_answered_everything():
    """The case the old wording got wrong.

    "The SOW answered every planning question" was shown for a document that
    had answered 39 of them and been asked nothing else.
    """
    coverage = audit_coverage(set())

    assert coverage["open"] == 0
    assert coverage["answered"] == len(PLANNING_FIELDS)
    assert "not examined" in coverage["boundary"]


def test_the_pipeline_says_what_it_searched_at_ingest(db):
    """Not only in the UI: whoever reads the upload warnings sees it too."""
    sow = CORPUS / "sow_a_cairomart.pdf"
    doc = parse_document(sow, "SOW-001")
    citations = [doc.chunk_key(section, chunk) for section, chunk in doc.iter_chunks()][:2]
    project = Project(name="Ingest warnings", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()

    state = run_sow_pipeline(
        db, project.id, str(sow), "SOW-001", client=StubLLM(citations=citations)
    )

    warnings = state.get("warnings", [])
    assert any(
        f"checked {len(PLANNING_FIELDS)} planning field(s)" in w for w in warnings
    ), warnings
    assert any("were not examined" in w for w in warnings), warnings
