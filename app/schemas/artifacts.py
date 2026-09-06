"""User stories, acceptance criteria and test cases.

These are *derived* artifacts: a requirement restated as work someone can pick
up, agree is finished, and prove. That makes them a different kind of object
from everything else the pipeline produces, and they are handled differently in
two ways.

Provenance is inherited, not asked for. A user story is INFERRED by
construction — it is a restatement, so it can never be EXPLICIT, and asking the
model to cite chunks for it invites citations that support the requirement
rather than the story. It takes its parent requirement's citations instead.

Dependence on assumed values is declared, then judged in code. A test case that
asserts "$100 earns 1,000 points" is only trustworthy if the SOW actually set
the earn rate; if the platform assumed it, a QA engineer running that case
would "validate" a number this system invented. So each case names the planning
fields it relies on, and whether those were assumed is decided from the gap
audit — never taken from the model's own word.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class TestKind(StrEnum):
    FUNCTIONAL = "functional"
    # Run after development to prove the built system matches the SOW, as
    # opposed to proving one story behaves as written.
    TECHNICAL_VALIDATION = "technical_validation"


class DraftAcceptanceCriterion(BaseModel):
    criterion_key: str = Field(description="Stable id like AC-001, unique across the response")
    text: str = Field(description="One condition that must hold for the story to be done")


class DraftTestCase(BaseModel):
    case_key: str = Field(description="Stable id like TC-001, unique across the response")
    title: str
    preconditions: str = Field(default="", description="What must be true before the test runs")
    action: str = Field(description="The single action under test")
    expected_result: str = Field(description="What must be observed for the test to pass")
    kind: TestKind = TestKind.FUNCTIONAL
    depends_on_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Exact planning field keys whose values this case relies on, e.g. "
            "['earn_rate']. Required whenever the expected result contains a "
            "number, rate or duration that came from one."
        ),
    )


class DraftUserStory(BaseModel):
    story_key: str = Field(description="Stable id like US-001")
    requirement_id: str = Field(description="The requirement id this story comes from, e.g. REQ-002")
    actor: str = Field(description="Who wants this, e.g. 'merchant', 'customer', 'support agent'")
    capability: str = Field(description="What they want to do")
    benefit: str = Field(description="Why, i.e. the 'so that' clause")
    acceptance_criteria: list[DraftAcceptanceCriterion] = Field(default_factory=list)
    test_cases: list[DraftTestCase] = Field(default_factory=list)

    def sentence(self) -> str:
        return f"As a {self.actor}, I want {self.capability} so that {self.benefit}"


class TechnicalArtifacts(BaseModel):
    """One structured call's worth of derived artifacts."""

    stories: list[DraftUserStory] = Field(default_factory=list)
