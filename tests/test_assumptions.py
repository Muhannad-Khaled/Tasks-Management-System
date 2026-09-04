"""Assumption engine.

The engine exists because relying on the model to volunteer gaps measured 0/7
recall on a deliberately vague SOW. These tests pin the guarantees that make
the checklist approach worth having: no field goes unanswered, and a claim the
model cannot support is demoted rather than believed.
"""

import pytest

from app.graph.assumptions import (
    build_assumptions,
    render_field_list,
    validate_findings,
)
from app.schemas.enums import SourceStatus
from app.schemas.fields import FIELDS_BY_KEY, PLANNING_FIELDS
from app.schemas.gaps import FieldFinding, GapReport

VALID_KEYS = {"SOW-001-S01-C01", "SOW-001-S02-C01"}


def _report(*findings: FieldFinding) -> GapReport:
    return GapReport(findings=list(findings))


def test_a_field_the_audit_skipped_is_treated_as_a_gap():
    # Silence must never read as "the SOW covers it".
    findings, warnings = validate_findings(_report(), PLANNING_FIELDS, VALID_KEYS)
    assert len(findings) == len(PLANNING_FIELDS)
    assert all(f.status == SourceStatus.ASSUMED for f in findings.values())
    assert len(warnings) == len(PLANNING_FIELDS)


def test_explicit_claim_without_a_citation_is_downgraded():
    report = _report(
        FieldFinding(
            field_key="offer_count",
            status=SourceStatus.EXPLICIT,
            value="20",
            evidence_chunk_keys=[],
        )
    )
    findings, warnings = validate_findings(report, PLANNING_FIELDS, VALID_KEYS)
    assert findings["offer_count"].status == SourceStatus.ASSUMED
    assert findings["offer_count"].value == ""
    assert any("no usable citation" in w for w in warnings)


def test_explicit_claim_citing_a_nonexistent_chunk_is_downgraded():
    report = _report(
        FieldFinding(
            field_key="earn_rate",
            status=SourceStatus.EXPLICIT,
            value="10 points per 1 USD",
            evidence_chunk_keys=["SOW-001-S99-C99"],
        )
    )
    findings, _ = validate_findings(report, PLANNING_FIELDS, VALID_KEYS)
    assert findings["earn_rate"].status == SourceStatus.ASSUMED


@pytest.mark.parametrize(
    "value",
    ["TBD", "to be confirmed", "standard scheme", "probably API keys", "industry-standard", ""],
)
def test_non_committal_values_do_not_count_as_stated(value):
    # "Standard scheme" is the SOW declining to specify, not a specification.
    report = _report(
        FieldFinding(
            field_key="earn_rate",
            status=SourceStatus.EXPLICIT,
            value=value,
            evidence_chunk_keys=["SOW-001-S01-C01"],
        )
    )
    findings, _ = validate_findings(report, PLANNING_FIELDS, VALID_KEYS)
    assert findings["earn_rate"].status == SourceStatus.ASSUMED


def test_a_properly_cited_concrete_value_survives():
    report = _report(
        FieldFinding(
            field_key="offer_count",
            status=SourceStatus.EXPLICIT,
            value="20",
            evidence_chunk_keys=["SOW-001-S01-C01"],
        )
    )
    findings, warnings = validate_findings(report, PLANNING_FIELDS, VALID_KEYS)
    assert findings["offer_count"].status == SourceStatus.EXPLICIT
    assert findings["offer_count"].value == "20"
    assert not any("offer_count" in w for w in warnings)


def test_citations_are_normalized_before_being_checked():
    report = _report(
        FieldFinding(
            field_key="offer_count",
            status=SourceStatus.EXPLICIT,
            value="20",
            evidence_chunk_keys=["SOW-001-S01-C01 p3"],
        )
    )
    findings, _ = validate_findings(report, PLANNING_FIELDS, VALID_KEYS)
    assert findings["offer_count"].status == SourceStatus.EXPLICIT


def test_unknown_field_keys_are_reported_not_stored():
    report = _report(
        FieldFinding(field_key="invented_field", status=SourceStatus.EXPLICIT, value="x")
    )
    findings, warnings = validate_findings(report, PLANNING_FIELDS, VALID_KEYS)
    assert "invented_field" not in findings
    assert any("unknown field" in w for w in warnings)


def test_assumptions_carry_a_default_reason_and_confidence():
    findings, _ = validate_findings(_report(), PLANNING_FIELDS, VALID_KEYS)
    assumptions = build_assumptions(findings)
    assert len(assumptions) == len(PLANNING_FIELDS)
    for assumption in assumptions:
        assert assumption["value"], "a default is what makes the plan schedulable"
        assert assumption["reason"]
        assert 0.0 < assumption["confidence"] <= 1.0
    keys = [a["assumption_key"] for a in assumptions]
    assert keys == sorted(keys), "assumption ids should be stable and ordered"
    assert len(set(keys)) == len(keys)


def test_stated_fields_produce_no_assumption():
    report = _report(
        FieldFinding(
            field_key="offer_count",
            status=SourceStatus.EXPLICIT,
            value="20",
            evidence_chunk_keys=["SOW-001-S01-C01"],
        )
    )
    findings, _ = validate_findings(report, PLANNING_FIELDS, VALID_KEYS)
    categories = {a["category"] for a in build_assumptions(findings)}
    assert FIELDS_BY_KEY["offer_count"].category not in categories


def test_the_model_is_asked_about_every_field():
    rendered = render_field_list(PLANNING_FIELDS)
    for spec in PLANNING_FIELDS:
        assert spec.key in rendered
        assert spec.question in rendered
