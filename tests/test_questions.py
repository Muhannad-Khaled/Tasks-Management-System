"""Clarification questions.

An assumption and a question are one gap seen from two sides: the plan runs on
the assumption, and the question is what would replace it. They are derived
from the same findings so they cannot drift apart — a PM must never be asked to
confirm something the SOW already settled, nor find the plan resting on a value
nobody was asked about.
"""

import pytest

from app.graph.assumptions import build_assumptions, build_questions
from app.graph.persistence import persist_questions
from app.models import ClarificationQuestion, Project
from app.schemas.enums import FieldScope, ProjectStatus, SourceStatus
from app.schemas.fields import FIELDS_BY_KEY, PLANNING_FIELDS, fields_for_scope
from app.schemas.gaps import FieldFinding


def _all_gaps() -> dict[str, FieldFinding]:
    return {
        f.key: FieldFinding(field_key=f.key, status=SourceStatus.ASSUMED, value="")
        for f in PLANNING_FIELDS
    }


def test_every_field_carries_a_scope_and_a_question():
    for spec in PLANNING_FIELDS:
        assert spec.scope in set(FieldScope), spec.key
        assert spec.question.strip().endswith("?"), spec.key


def test_every_scope_has_fields():
    for scope in FieldScope:
        assert fields_for_scope(scope), scope


def test_a_gap_becomes_a_question_in_the_fields_own_words():
    questions = build_questions(_all_gaps())
    by_field = {q["field_key"]: q for q in questions}
    assert by_field["offer_count"]["text"] == FIELDS_BY_KEY["offer_count"].question


def test_a_field_the_sow_settles_is_not_asked_about():
    findings = _all_gaps()
    findings["merchant_count"] = FieldFinding(
        field_key="merchant_count",
        status=SourceStatus.EXPLICIT,
        value="5",
        evidence_chunk_keys=["SOW-001-S01-C01"],
    )

    asked = {q["field_key"] for q in build_questions(findings)}

    assert "merchant_count" not in asked


def test_questions_and_assumptions_describe_the_same_gaps():
    # If these ever diverge the plan is resting on a value nobody was asked
    # about, or the PM is chasing an answer the plan does not use.
    findings = _all_gaps()
    findings["earn_rate"] = FieldFinding(
        field_key="earn_rate",
        status=SourceStatus.EXPLICIT,
        value="10 points per 1 USD",
        evidence_chunk_keys=["SOW-001-S01-C01"],
    )

    assumed = {a["category"] for a in build_assumptions(findings)}
    asked = {q["category"] for q in build_questions(findings)}

    assert assumed == asked


def test_a_question_carries_what_the_plan_runs_on_meanwhile():
    questions = build_questions(_all_gaps())
    assert all(q["working_assumption"] for q in questions)


def test_a_direction_the_sow_pointed_at_beats_the_generic_default():
    # The SOW saying "probably API keys" does not settle the field, but the
    # plan should still not go off and assume OAuth.
    findings = _all_gaps()
    findings["auth_mechanism"] = FieldFinding(
        field_key="auth_mechanism",
        status=SourceStatus.ASSUMED,
        value="",
        hint="probably API keys",
    )

    question = next(q for q in build_questions(findings) if q["field_key"] == "auth_mechanism")

    assert question["working_assumption"] == "probably API keys"


def test_questions_are_grouped_by_who_has_to_answer_them():
    questions = build_questions(_all_gaps())
    scopes = {q["scope"] for q in questions}
    assert scopes == {str(s) for s in FieldScope}
    merchant = [q for q in questions if q["scope"] == str(FieldScope.MERCHANT)]
    offers = [q for q in questions if q["scope"] == str(FieldScope.OFFER)]
    assert merchant and offers


def test_question_keys_are_unique_and_sequential():
    keys = [q["question_key"] for q in build_questions(_all_gaps())]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


@pytest.fixture
def project(db):
    row = Project(name="Questions", status=ProjectStatus.INGESTING)
    db.add(row)
    db.commit()
    return row


def test_questions_are_persisted_with_their_scope(db, project):
    persist_questions(db, project, build_questions(_all_gaps()))
    db.commit()

    rows = db.query(ClarificationQuestion).filter(
        ClarificationQuestion.project_id == project.id
    ).all()
    assert len(rows) == len(PLANNING_FIELDS)
    assert all(r.status == "open" for r in rows)
    assert {r.scope for r in rows} == {str(s) for s in FieldScope}


def test_re_running_replaces_questions_rather_than_stacking_them(db, project):
    # Otherwise the PM is left holding questions the current plan no longer
    # depends on.
    questions = build_questions(_all_gaps())
    persist_questions(db, project, questions)
    db.commit()
    persist_questions(db, project, questions[:3])
    db.commit()

    assert db.query(ClarificationQuestion).filter(
        ClarificationQuestion.project_id == project.id
    ).count() == 3


def test_deleting_a_project_takes_its_questions(db, project):
    persist_questions(db, project, build_questions(_all_gaps()))
    db.commit()

    db.delete(project)
    db.commit()

    assert db.query(ClarificationQuestion).count() == 0
