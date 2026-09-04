"""Test helpers: corpus paths and the LLM test double."""

from pathlib import Path

from app.schemas.enums import SourceStatus
from app.schemas.fields import PLANNING_FIELDS
from app.schemas.gaps import FieldFinding, GapReport
from app.schemas.sow import (
    ExtractedAssumption,
    ExtractedRequirement,
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

    def __init__(self, citations: list[str] | None = None):
        self.citations = citations if citations is not None else []
        self.calls: list[dict] = []

    def generate_structured(self, prompt, schema, **kwargs):
        self.calls.append({"prompt": prompt.name, **kwargs})
        if schema is GapReport:
            return self._gap_report()
        return self._extraction()

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
            requirements=[
                ExtractedRequirement(
                    requirement_id="REQ-001",
                    team="commercial",
                    title="Countersign the commercial contract",
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                )
            ],
            tasks=[
                ExtractedTask(
                    task_id="T-001",
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
                    team="technical",
                    title="Develop POS API integration",
                    description="REST integration for points accrual.",
                    priority="high",
                    estimated_hours=60,
                    depends_on=["T-001"],
                    source_status="explicit",
                    source_chunk_keys=self.citations,
                ),
                ExtractedTask(
                    task_id="T-003",
                    team="operations",
                    title="Configure merchant and offers",
                    priority="medium",
                    estimated_hours=16,
                    depends_on=["T-002"],
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
