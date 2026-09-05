"""Claim-level grounding and the validation pipeline.

The scoring rules encode judgement calls, not arithmetic, so they are pinned
here: what counts as supported, what a contradiction does to an otherwise good
task, and what happens when the verifier says nothing at all.
"""

from datetime import date

import pytest

from app.grounding.engine import (
    GroundingStatus,
    TaskGrounding,
    gather_evidence,
    ground_project,
    project_grounding_score,
    render_evidence,
    verify_claims,
)
from app.models import ProjectTask
from app.schemas.grounding import Claim, ClaimJudgement, ClaimVerdict, GroundingJudgement
from app.validation.pipeline import (
    validate_business_rules,
    validate_dependencies,
    validate_grounding,
    validate_schema,
    validate_timeline,
)


def _grounding(*verdicts: ClaimVerdict) -> TaskGrounding:
    claims = [Claim(claim_id=f"C-{i:03d}", text=f"claim {i}") for i in range(1, len(verdicts) + 1)]
    judgements = [
        ClaimJudgement(claim_id=c.claim_id, verdict=v, reasoning="")
        for c, v in zip(claims, verdicts)
    ]
    return TaskGrounding(task_id="T1", title="Task", claims=claims, judgements=judgements)


S, U = ClaimVerdict.SUPPORTED, ClaimVerdict.UNSUPPORTED


@pytest.mark.parametrize(
    "verdicts,expected",
    [((S, S, S, S), 1.0), ((S, S, S, U), 0.75), ((S, U), 0.5), ((U, U), 0.0)],
)
def test_score_is_supported_over_total(verdicts, expected):
    assert _grounding(*verdicts).score == expected


def test_a_task_asserting_nothing_checkable_is_not_penalised():
    assert TaskGrounding(task_id="T1", title="Task").score == 1.0


@pytest.mark.parametrize(
    "verdicts,status",
    [
        ((S, S, S, S, S, S, S, S, S, S), GroundingStatus.ACCEPT),  # 100%
        ((S, S, S, S, S, S, S, S, S, U), GroundingStatus.ACCEPT),  # 90%, on the boundary
        ((S, S, S, S, S, S, S, S, U, U), GroundingStatus.REVIEW),  # 80%
        ((S, S, S, U), GroundingStatus.REVIEW),  # 75%, just above review
        ((S, S, U, U), GroundingStatus.REJECT),  # 50%
        ((S, U, U, U), GroundingStatus.REJECT),  # 25%
    ],
)
def test_policy_thresholds(verdicts, status):
    # Brief section 18: 90%+ accept, 70-89% review, below 70% reject.
    assert _grounding(*verdicts).status == status


def test_one_contradiction_rejects_an_otherwise_strong_task():
    # A claim the SOW actively contradicts is worse than a claim it is silent
    # about, so it must not be averaged away by nine good ones.
    grounding = _grounding(*([S] * 9), ClaimVerdict.CONTRADICTED)
    assert grounding.score == 0.9
    assert grounding.status == GroundingStatus.REJECT


def test_project_score_weights_claims_not_tasks():
    # One task with a single supported claim must not offset another with nine
    # failures just because they are one task each.
    small = _grounding(S)
    large = _grounding(*([U] * 9))
    assert project_grounding_score([small, large]) == pytest.approx(0.1)


def test_supported_verdict_citing_nothing_real_is_downgraded(db):
    # The verifier calling a claim supported while citing evidence that does not
    # exist is precisely the failure the grounding layer is meant to catch.
    class _Client:
        def generate_structured(self, prompt, schema, **kwargs):
            return GroundingJudgement(
                judgements=[
                    ClaimJudgement(
                        claim_id="C-001",
                        verdict=ClaimVerdict.SUPPORTED,
                        supporting_chunk_keys=["SOW-001-S99-C99"],
                        reasoning="claims support",
                    )
                ]
            )

    claims = [Claim(claim_id="C-001", text="20 offers will launch")]
    judgements = verify_claims(_Client(), db, "p1", claims, [], {"SOW-001-S01-C01"})
    assert judgements[0].verdict == ClaimVerdict.UNSUPPORTED
    assert "cited no evidence" in judgements[0].reasoning


def test_a_claim_the_verifier_ignored_is_not_treated_as_supported(db):
    class _Client:
        def generate_structured(self, prompt, schema, **kwargs):
            return GroundingJudgement(judgements=[])

    claims = [Claim(claim_id="C-001", text="something")]
    judgements = verify_claims(_Client(), db, "p1", claims, [], set())
    assert judgements[0].verdict == ClaimVerdict.UNSUPPORTED


def test_evidence_rendering_labels_each_chunk_with_its_location():
    rendered = render_evidence([("K1", "4. Commercial", "20 offers", 7)])
    assert "[K1]" in rendered and "4. Commercial" in rendered and "page 7" in rendered


def test_evidence_rendering_says_so_when_there_is_none():
    assert "no evidence" in render_evidence([])


def test_gather_evidence_skips_retrieval_when_none_is_configured(db):
    assert gather_evidence(db, "p1", [], [Claim(claim_id="C-001", text="x")], None) == []


def test_grounding_a_project_costs_two_calls_regardless_of_task_count(db):
    # The free tier allows 20 requests per day, so a call per task would spend
    # a whole day's budget on one project. This is a budget, not a preference.
    from tests.factories import StubLLM

    class _Task:
        def __init__(self, n):
            self.id, self.title, self.description, self.source_chunk_keys = (
                f"task-{n}", f"Task {n}", "", ""
            )

    stub = StubLLM()
    groundings = ground_project(stub, db, "p1", [_Task(i) for i in range(12)], set(), None)

    assert len(stub.calls) == 2, f"expected 2 calls, got {[c['prompt'] for c in stub.calls]}"
    assert len(groundings) == 12


def test_claim_ids_are_namespaced_so_verdicts_cannot_cross_tasks(db):
    # The model reuses ids like C-001 per task; without namespacing, one task's
    # verdict would silently overwrite another's.
    from tests.factories import StubLLM

    class _Task:
        def __init__(self, n):
            self.id, self.title, self.description, self.source_chunk_keys = (
                f"task-{n}", f"Task {n}", "", ""
            )

    groundings = ground_project(StubLLM(), db, "p1", [_Task(1), _Task(2)], set(), None)
    all_ids = [c.claim_id for g in groundings for c in g.claims]
    assert len(all_ids) == len(set(all_ids)), "claim ids collided across tasks"
    assert all(g.judgements for g in groundings), "every task must receive verdicts"


# --- validation pipeline -------------------------------------------------


def _task(**kwargs) -> ProjectTask:
    defaults = {
        "id": "T1",
        "project_id": "P1",
        "title": "Do the thing",
        "description": "",
        "team": "technical",
        "source_chunk_keys": "",
    }
    return ProjectTask(**{**defaults, **kwargs})


def test_schema_stage_rejects_a_blank_title_or_unknown_team():
    assert not validate_schema([_task(title="  ")]).passed
    assert not validate_schema([_task(team="marketing")]).passed
    assert validate_schema([_task()]).passed


def test_grounding_stage_fails_only_on_rejected_tasks():
    assert validate_grounding([_grounding(S, S, S, S)]).passed
    assert validate_grounding([_grounding(S, S, S, U)]).passed, "review is not a failure"
    assert not validate_grounding([_grounding(U, U, U, S)]).passed


def test_business_rules_catch_a_task_filed_under_the_wrong_team():
    wrong = _task(title="Deliver on-ground staff training", team="technical")
    assert not validate_business_rules([wrong]).passed

    right = _task(title="Deliver on-ground staff training", team="operations")
    assert validate_business_rules([right]).passed


@pytest.mark.parametrize(
    "title,team",
    [
        # Regression: a bare "configur" signal filed this as operations work.
        ("Configure Points Calculation & Expiry Engine", "technical"),
        ("Configure the offer accrual logic", "technical"),
        ("Configure the merchant account in production", "operations"),
        ("Deliver on-ground staff training", "operations"),
        ("Countersign the commercial contract", "commercial"),
    ],
)
def test_business_rules_do_not_flag_correctly_assigned_work(title, team):
    result = validate_business_rules([_task(title=title, team=team)])
    assert result.passed, f"{title!r} wrongly flagged as not belonging to {team}"


def test_business_rules_stay_quiet_when_wording_spans_teams():
    # "Test the API after contract sign-off" points at two teams; flagging it
    # either way would be guessing.
    ambiguous = _task(title="Test the API after contract sign-off", team="technical")
    assert validate_business_rules([ambiguous]).passed


def test_dependency_stage_reports_a_cycle():
    a, b = _task(id="A"), _task(id="B")
    a.depends_on.append(b)
    b.depends_on.append(a)
    result = validate_dependencies([a, b])
    assert not result.passed
    assert "circular" in result.detail


def test_timeline_stage_flags_work_past_the_sow_deadline():
    late = _task(due_date=date(2026, 12, 15))
    result = validate_timeline([late], date(2026, 12, 1))
    assert not result.passed
    assert late.id in result.offending_task_ids


def test_timeline_stage_passes_without_a_deadline_to_check():
    assert validate_timeline([_task(due_date=date(2026, 12, 15))], None).passed
