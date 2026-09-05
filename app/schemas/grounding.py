"""Claim-level grounding schemas (brief sections 16-18).

"The whole answer is grounded" is not a useful statement. A task can be mostly
supported by the SOW and still contain one invented detail — a duration, a
count, a deadline — and that one detail is what derails a project. So each
generated item is broken into atomic claims and each claim is judged on its own
evidence.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class ClaimVerdict(StrEnum):
    SUPPORTED = "supported"  # the evidence states or directly implies the claim
    PARTIAL = "partial"  # related evidence, but it does not establish the claim
    UNSUPPORTED = "unsupported"  # nothing in the SOW establishes this
    CONTRADICTED = "contradicted"  # the SOW says otherwise


class Claim(BaseModel):
    claim_id: str = Field(description="Stable id such as C-001")
    text: str = Field(description="One atomic, checkable assertion")
    is_quantitative: bool = Field(
        default=False,
        description="True when the claim asserts a number, date, or duration",
    )


class ClaimSet(BaseModel):
    claims: list[Claim] = Field(
        description="The atomic claims this item makes. Split compound statements."
    )


class TaskClaims(BaseModel):
    task_ref: str = Field(description="The task reference given in the input, e.g. T1")
    claims: list[Claim] = Field(default_factory=list)


class BatchClaimSet(BaseModel):
    """Claims for every task in one response.

    Batched because the free tier allows 20 requests per day: a call per task
    would spend a whole day's budget grounding a single project.
    """

    tasks: list[TaskClaims] = Field(description="One entry per task given, in the order given.")


class ClaimJudgement(BaseModel):
    claim_id: str
    verdict: ClaimVerdict
    supporting_chunk_keys: list[str] = Field(
        default_factory=list,
        description="Chunks that establish the claim. Empty unless supported or partial.",
    )
    reasoning: str = Field(description="One sentence on why the evidence does or does not settle it")


class GroundingJudgement(BaseModel):
    judgements: list[ClaimJudgement] = Field(
        description="Exactly one judgement per claim, in the order given."
    )
