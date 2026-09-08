"""User stories, acceptance criteria and test cases.

These are *derived* artifacts: a requirement restated as work someone can pick
up, agree is finished, and prove. That makes them a different kind of object
from everything else the pipeline produces, and they are handled differently in
two ways.

There are two audiences and they need different sentences. A client story says
what the merchant or cardholder gets and hangs off a requirement. An engineer
story says how one technical task will be built and hangs off that task —
because a requirement can carry three tasks, and inheriting one set of criteria
across all of them put the accrual engine's acceptance criteria on the card for
writing a design document.

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
    measure: str = Field(
        default="",
        description=(
            "The threshold this criterion is judged against, when the project "
            "details state one, e.g. 'p95 latency <= 400ms' or '450 TPS'. "
            "Leave empty rather than choosing a number yourself."
        ),
    )
    source_detail_names: list[str] = Field(
        default_factory=list,
        description=(
            "Exact names of the project details the measure was taken from. "
            "Required whenever measure is filled. Copy the names given; do not "
            "paraphrase them."
        ),
    )


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


class DraftEngineerStory(BaseModel):
    """How one technical task will actually be built.

    The actor is deliberately absent. It is the delivery role already assigned
    to the task, so asking for it would invite a second opinion on a question
    the plan has already settled — and let a role be named that is not on the
    project.
    """

    story_key: str = Field(description="Stable id like ES-001")
    task_id: str = Field(description="The technical task id this describes, exactly as given")
    capability: str = Field(
        description="What the engineer will build or change, in technical terms"
    )
    benefit: str = Field(description="What it achieves for the delivery, i.e. the 'so that' clause")
    technical_notes: str = Field(
        default="",
        description=(
            "Interfaces, protocols, environments and constraints this work must "
            "respect. Composed only from the project details listed in the "
            "prompt. Naming a library, framework or tool the details do not "
            "mention is prohibited; leave this empty instead."
        ),
    )
    source_detail_names: list[str] = Field(
        default_factory=list,
        description="Exact names of the project details the notes were built from",
    )
    acceptance_criteria: list[DraftAcceptanceCriterion] = Field(default_factory=list)
    test_cases: list[DraftTestCase] = Field(default_factory=list)


class TechnicalArtifacts(BaseModel):
    """One structured call's worth of derived artifacts."""

    stories: list[DraftUserStory] = Field(default_factory=list)
    engineer_stories: list[DraftEngineerStory] = Field(default_factory=list)
