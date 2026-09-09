"""Prompt templates against the arguments the code actually supplies.

The LLM stub returns a fixed object without ever rendering a template, so a
placeholder nobody fills is invisible to every other test and fails only on a
live run — after the quota has been spent.
"""

import string

import pytest

from app.llm.prompts import (
    CLAIM_EXTRACTION,
    CLAIM_VERIFICATION,
    GAP_DETECTION,
    REGISTRY,
    SOW_EXTRACTION,
    TASK_REGENERATION,
    TECHNICAL_ARTIFACTS,
)
from app.schemas.artifacts import TechnicalArtifacts
from app.schemas.gaps import GapReport
from app.schemas.grounding import BatchClaimSet, GroundingJudgement
from app.schemas.sow import ExtractedTask, StructuredSOW

# What each caller passes. Update this when a call site changes; the test then
# tells you which template no longer matches.
SUPPLIED = {
    SOW_EXTRACTION: {"doc_key", "chunked_text", "role_guide", "category_guide"},
    GAP_DETECTION: {"doc_key", "chunked_text", "field_list"},
    CLAIM_EXTRACTION: {"tasks"},
    CLAIM_VERIFICATION: {"evidence", "claims"},
    TASK_REGENERATION: {"title", "description", "team", "reason", "failed_claims", "evidence"},
    TECHNICAL_ARTIFACTS: {
        "requirements",
        "technical_tasks",
        "project_details",
        "working_values",
    },
}


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


@pytest.mark.parametrize("prompt", REGISTRY.values(), ids=lambda p: p.name)
def test_every_placeholder_is_supplied_by_its_caller(prompt):
    assert placeholders(prompt.template) == SUPPLIED[prompt], prompt.name


@pytest.mark.parametrize("prompt", REGISTRY.values(), ids=lambda p: p.name)
def test_every_prompt_renders(prompt):
    rendered = prompt.render(**{name: "x" for name in SUPPLIED[prompt]})
    assert "{" not in rendered.replace("{{", "").replace("}}", "")


def test_the_registry_covers_every_prompt_this_test_knows_about():
    assert set(REGISTRY.values()) == set(SUPPLIED)


def test_the_extraction_prompt_names_the_roles_tasks_may_be_assigned_to():
    from app.schemas.roles import TEAM_ROLES, render_role_guide

    guide = render_role_guide()
    for role in TEAM_ROLES:
        assert role.title in guide


def test_the_extraction_prompt_forbids_inventing_a_person():
    assert "person" in SOW_EXTRACTION.template.lower()


# ---------------------------------------------- structured-output schemas

# Gemini's response_schema accepts a subset of JSON Schema. A keyword outside
# it is not ignored — it fails the whole call. The LLM stub never runs the
# conversion, so nothing else in the suite can catch this: `gt=0` on an effort
# estimate compiled to exclusiveMinimum and broke extraction with 310 tests
# green.
# Only keywords observed to fail a real call. $defs and $ref appear in every
# nested model and the SDK resolves them, so listing them here would fail
# the suite on schemas that work.
UNSUPPORTED_BY_GEMINI = ("exclusiveMinimum", "exclusiveMaximum")

STRUCTURED_SCHEMAS = [
    StructuredSOW,
    GapReport,
    TechnicalArtifacts,
    BatchClaimSet,
    GroundingJudgement,
    ExtractedTask,
]


@pytest.mark.parametrize("model", STRUCTURED_SCHEMAS, ids=lambda m: m.__name__)
def test_no_schema_uses_a_keyword_gemini_rejects(model):
    import json

    blob = json.dumps(model.model_json_schema())
    found = [kw for kw in UNSUPPORTED_BY_GEMINI if kw in blob]
    assert not found, f"{model.__name__} uses {found}, which Gemini will reject"


def test_an_effort_estimate_is_required_of_every_task():
    # Left optional, the model omitted it for nine tasks in ten and the
    # scheduler quietly substituted one day for each.
    assert ExtractedTask.model_fields["estimated_hours"].is_required()


def test_the_extraction_prompt_anchors_estimates_to_the_sows_own_dates():
    # Without an anchor the model sizes work in a vacuum: technical integration
    # came out at 32% of the window the SOW had agreed for it.
    template = SOW_EXTRACTION.template.lower()
    assert "milestone" in template
    assert "working days" in template


def test_the_extraction_prompt_forbids_shrinking_an_estimate_to_fit():
    # The gap between what work needs and what the SOW allows is the most
    # useful thing the plan reports. Capping estimates to fit the contract
    # would delete exactly that, and make every milestone look green.
    template = SOW_EXTRACTION.template.lower()
    assert "never shrink" in template
