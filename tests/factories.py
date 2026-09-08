"""Test helpers: corpus paths and the LLM test double."""

import re
from datetime import date
from pathlib import Path

from app.schemas.artifacts import (
    DraftAcceptanceCriterion,
    DraftEngineerStory,
    DraftTestCase,
    DraftUserStory,
    TechnicalArtifacts,
    TestKind,
)
from app.schemas.details import DetailCategory
from app.schemas.enums import SourceStatus
from app.schemas.fields import PLANNING_FIELDS
from app.schemas.gaps import FieldFinding, GapReport
from app.schemas.grounding import (
    BatchClaimSet,
    Claim,
    ClaimJudgement,
    ClaimVerdict,
    GroundingJudgement,
    TaskClaims,
)
from app.schemas.sow import (
    ExtractedAssumption,
    ExtractedDependency,
    ExtractedDetail,
    ExtractedMilestone,
    ExtractedRequirement,
    ExtractedRole,
    ExtractedTask,
    ProjectInfo,
    StructuredSOW,
)

CORPUS = Path(__file__).parent.parent / "data" / "sample_sows"


class StubLLM:
    """Stands in for GeminiClient, returning a fixed extraction.

    `citations` lets a test inject chunk keys that do not exist in the document,
    which is how the invented-citation guard is exercised.
    """

    def __init__(self, citations: list[str] | None = None, roster: bool = False):
        self.citations = citations if citations is not None else []
        # Most SOWs do not name their team, so the default stub returns no
        # roster and the default one is assumed. `roster=True` exercises the
        # other path.
        self.roster = roster
        self.calls: list[dict] = []

    def generate_structured(self, prompt, schema, **kwargs):
        self.calls.append({"prompt": prompt.name, **kwargs})
        if schema is GapReport:
            return self._gap_report()
        if schema is BatchClaimSet:
            return self._batch_claims(kwargs.get("tasks", ""))
        if schema is GroundingJudgement:
            return self._judgement(kwargs.get("claims", ""))
        if schema is TechnicalArtifacts:
            return self._artifacts(kwargs.get("technical_tasks", ""))
        if schema is ExtractedTask:
            return self._regenerated(kwargs.get("title", "Task"))
        return self._extraction()

    def _regenerated(self, title: str) -> ExtractedTask:
        """A rewritten task, for the regeneration prompt.

        Deliberately different wording from the original: a regeneration that
        returned the same text would make every assertion about what changed
        pass without the code having changed anything.
        """
        return ExtractedTask(
            task_id="T-001",
            team="technical",
            title=f"{title} (revised)",
            description="Narrowed to what the SOW actually establishes.",
            estimated_hours=24,
            source_status="explicit",
            source_chunk_keys=self.citations,
        )

    def _artifacts(self, technical_tasks: str = "") -> TechnicalArtifacts:
        """One story per requirement, covering the cases that matter.

        US-002 carries a test case resting on the earn rate, which the stub's
        gap report reports as assumed — that is the pairing the
        rests_on_assumption flag exists to catch.
        """
        return TechnicalArtifacts(
            stories=[
                DraftUserStory(
                    story_key="US-001",
                    requirement_id="REQ-001",
                    actor="merchant",
                    capability="have a countersigned commercial contract in place",
                    benefit="integration work can begin",
                    acceptance_criteria=[
                        DraftAcceptanceCriterion(
                            criterion_key="AC-001", text="Both parties have signed."
                        ),
                        DraftAcceptanceCriterion(
                            criterion_key="AC-002", text="The signed copy is filed."
                        ),
                    ],
                    test_cases=[
                        DraftTestCase(
                            case_key="TC-001",
                            title="Contract is countersigned",
                            action="Open the contract record",
                            expected_result="Status reads countersigned",
                        )
                    ],
                ),
                DraftUserStory(
                    story_key="US-002",
                    requirement_id="REQ-002",
                    actor="customer",
                    capability="earn points on a purchase",
                    benefit="I am rewarded for shopping",
                    acceptance_criteria=[
                        DraftAcceptanceCriterion(
                            criterion_key="AC-003",
                            text="Points appear on the account within 5 seconds.",
                        )
                    ],
                    test_cases=[
                        DraftTestCase(
                            case_key="TC-002",
                            title="Points accrue at the stated rate",
                            preconditions="An enrolled customer at a live till",
                            action="Complete a 100 USD purchase",
                            expected_result="The account is credited with 1000 points",
                            depends_on_fields=["earn_rate"],
                        ),
                        DraftTestCase(
                            case_key="TC-003",
                            title="Accrual endpoint responds",
                            action="Call the accrual endpoint",
                            expected_result="A success response is returned",
                            kind=TestKind.TECHNICAL_VALIDATION,
                            depends_on_fields=["not_a_real_field"],
                        ),
                    ],
                ),
                DraftUserStory(
                    story_key="US-003",
                    requirement_id="REQ-003",
                    actor="merchant store manager",
                    capability="have my account and offers configured",
                    benefit="my branches can go live",
                    acceptance_criteria=[
                        DraftAcceptanceCriterion(
                            criterion_key="AC-004", text="Every offer is configured."
                        )
                    ],
                    test_cases=[
                        DraftTestCase(
                            case_key="TC-004",
                            title="Offers are configured",
                            action="Open the merchant configuration",
                            expected_result="All offers are listed as active",
                        )
                    ],
                ),
            ],
            engineer_stories=self._engineer_stories(technical_tasks),
        )

    def _engineer_stories(self, technical_tasks: str) -> list[DraftEngineerStory]:
        """One per technical task, taking the ids the prompt actually rendered.

        Parsing them back out is the point: an engineer story keyed to a task
        id the stub invented would pass every assertion here while the real
        call, which sees database ids, dropped every story it produced.

        The first story cites a detail the stub's extraction really contains;
        every later one cites a detail that does not exist, so both halves of
        the sourcing rule are exercised on any project with two or more
        technical tasks.
        """
        ids = re.findall(r"^\[([0-9a-f-]{36})\]", technical_tasks, re.MULTILINE)
        stories = []
        for index, task_id in enumerate(ids):
            grounded = index == 0
            stories.append(
                DraftEngineerStory(
                    story_key=f"ES-{index + 1:03d}",
                    task_id=task_id,
                    capability="expose the accrual endpoint and reconcile it nightly",
                    benefit="points land on the right account without manual repair",
                    technical_notes=(
                        "POST /points/accrue, called synchronously from the till."
                        if grounded
                        else "Built on Redis Streams with a Kafka fallback."
                    ),
                    source_detail_names=(
                        ["Points accrual endpoint"] if grounded else ["A detail nobody extracted"]
                    ),
                    acceptance_criteria=[
                        DraftAcceptanceCriterion(
                            criterion_key=f"AC-1{index:02d}",
                            text="The endpoint returns within the agreed latency.",
                            measure="under 400ms" if grounded else "under 50ms",
                            source_detail_names=(
                                ["Points accrual endpoint"]
                                if grounded
                                else ["A detail nobody extracted"]
                            ),
                        )
                    ],
                    test_cases=[
                        DraftTestCase(
                            case_key=f"TC-1{index:02d}",
                            title="Accrual endpoint holds under load",
                            action="Drive the endpoint at the agreed peak rate",
                            expected_result="Every call succeeds within the latency budget",
                            kind=TestKind.TECHNICAL_VALIDATION,
                        )
                    ],
                )
            )
        return stories

    def _batch_claims(self, rendered_tasks: str) -> BatchClaimSet:
        """Two claims per task: one qualitative, one quantitative."""
        refs = re.findall(r"^\[(T\d+)\]", rendered_tasks, re.MULTILINE)
        return BatchClaimSet(
            tasks=[
                TaskClaims(
                    task_ref=ref,
                    claims=[
                        Claim(claim_id="C-001", text=f"{ref} states a requirement"),
                        Claim(
                            claim_id="C-002",
                            text=f"{ref} takes a specific duration",
                            is_quantitative=True,
                        ),
                    ],
                )
                for ref in refs
            ]
        )

    def _judgement(self, rendered_claims: str) -> GroundingJudgement:
        """Support the qualitative claim, fail the quantitative one.

        This mirrors the real failure mode the grounding layer exists to catch:
        a task correct about the work and unsupported about the numbers.
        """
        judgements = []
        for line in rendered_claims.splitlines():
            claim_id = line.strip().lstrip("- ").split(":")[0].strip()
            if not claim_id:
                continue
            if claim_id.endswith("C-001"):
                judgements.append(
                    ClaimJudgement(
                        claim_id=claim_id,
                        verdict=ClaimVerdict.SUPPORTED,
                        supporting_chunk_keys=self.citations[:1],
                        reasoning="The SOW states this requirement.",
                    )
                )
            else:
                judgements.append(
                    ClaimJudgement(
                        claim_id=claim_id,
                        verdict=ClaimVerdict.UNSUPPORTED,
                        reasoning="The SOW gives no duration for this work.",
                    )
                )
        return GroundingJudgement(judgements=judgements)

    def _gap_report(self) -> GapReport:
        """Report the first field as stated and the rest as gaps."""
        findings = []
        for index, spec in enumerate(PLANNING_FIELDS):
            if index == 0 and self.citations:
                findings.append(
                    FieldFinding(
                        field_key=spec.key,
                        status=SourceStatus.EXPLICIT,
                        value="5",
                        evidence_chunk_keys=self.citations,
                    )
                )
            else:
                findings.append(
                    FieldFinding(
                        field_key=spec.key,
                        status=SourceStatus.ASSUMED,
                        value="",
                        note=f"The SOW does not specify the {spec.label}.",
                    )
                )
        return GapReport(findings=findings)

    def _extraction(self) -> StructuredSOW:
        return StructuredSOW(
            project_info=ProjectInfo(
                project_name="Loyalty Program - CairoMart",
                merchant_name="CairoMart Retail Group",
            ),
            team_roster=(
                [
                    ExtractedRole(
                        title="Integration Architect",
                        team="technical",
                        responsibility="Integration design",
                        source_status="explicit",
                        source_chunk_keys=self.citations,
                    )
                ]
                if self.roster
                else []
            ),
            details=[
                ExtractedDetail(
                    category=DetailCategory.OFFER,
                    name="Double points weekend",
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                ),
                ExtractedDetail(
                    category=DetailCategory.API,
                    name="Points accrual endpoint",
                    description="POST /points/accrue",
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                ),
                ExtractedDetail(
                    category=DetailCategory.SYSTEM_CONFIGURATION,
                    name="Merchant configuration",
                    source_status="inferred",
                    source_chunk_keys=self.citations,
                ),
            ],
            milestones=[
                ExtractedMilestone(
                    name="Contract signed",
                    target_date=date(2026, 10, 2),
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                ),
                ExtractedMilestone(
                    name="Go-live",
                    target_date=date(2026, 12, 1),
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                ),
            ],
            requirements=[
                ExtractedRequirement(
                    requirement_id="REQ-001",
                    team="commercial",
                    title="Countersign the commercial contract",
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                ),
                ExtractedRequirement(
                    requirement_id="REQ-002",
                    team="technical",
                    title="Integrate with the merchant POS",
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                ),
                ExtractedRequirement(
                    requirement_id="REQ-003",
                    team="operations",
                    title="Configure merchants and offers",
                    source_status="inferred",
                    source_chunk_keys=self.citations,
                ),
            ],
            tasks=[
                ExtractedTask(
                    task_id="T-001",
                    milestone="Contract signed",
                    requirement_id="REQ-001",
                    assignee_role="Commercial Manager",
                    team="commercial",
                    title="Finalize merchant contract",
                    description="Countersign before integration starts.",
                    priority="high",
                    estimated_hours=8,
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                ),
                ExtractedTask(
                    task_id="T-002",
                    milestone="Go-live",
                    requirement_id="REQ-002",
                    assignee_role="Backend Engineer",
                    team="technical",
                    title="Develop POS API integration",
                    description="REST integration for points accrual.",
                    priority="high",
                    estimated_hours=60,
                    # Ordered by the SOW itself, with the chunk that says so.
                    depends_on=[
                        ExtractedDependency(
                            depends_on_id="T-001",
                            source_status="explicit",
                            source_chunk_keys=self.citations,
                            rationale="Integration work starts after the contract is signed.",
                        )
                    ],
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                ),
                # A second technical task under REQ-002, which is the shape
                # that produced the bug: two units of work sharing one
                # requirement were handed one another's acceptance criteria,
                # so a design-document card carried the API's tests.
                ExtractedTask(
                    task_id="T-004",
                    milestone="Go-live",
                    requirement_id="REQ-002",
                    assignee_role="Technical Lead",
                    team="technical",
                    title="Author the integration design document",
                    description="Interfaces, error handling and rollback, for review.",
                    priority="medium",
                    estimated_hours=20,
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                ),
                ExtractedTask(
                    task_id="T-003",
                    milestone="Go-live",
                    requirement_id="REQ-003",
                    assignee_role="Operations Specialist",
                    team="operations",
                    title="Configure merchant and offers",
                    priority="medium",
                    estimated_hours=16,
                    # Nothing in the SOW orders these two; the system did.
                    depends_on=[
                        ExtractedDependency(
                            depends_on_id="T-002",
                            source_status="assumed",
                            rationale="Configuration normally follows the build.",
                        )
                    ],
                    source_status="inferred",
                    source_chunk_keys=self.citations,
                ),
            ],
            assumptions=[
                ExtractedAssumption(
                    assumption_id="A-001",
                    category="TEAM_SIZE",
                    value="5 technical team members",
                    reason="SOW does not specify team size",
                    confidence=0.8,
                )
            ],
        )
