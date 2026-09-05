"""Schema for the explicit gap-detection pass.

The model is asked, field by field, whether the SOW states a value. It answers
with a finding per field rather than a free-form list, so a field can never be
skipped silently — the absence of an answer is itself detectable.
"""

from pydantic import BaseModel, Field

from app.schemas.enums import SourceStatus


class FieldFinding(BaseModel):
    field_key: str = Field(description="The exact field key that was asked about")
    status: SourceStatus = Field(
        description=(
            "explicit if the SOW states this outright; inferred if it follows "
            "necessarily from what the SOW says; assumed if the SOW does not "
            "provide it at all"
        )
    )
    value: str = Field(description="The value found, or '' when the SOW does not provide one")
    evidence_chunk_keys: list[str] = Field(
        default_factory=list,
        description="Chunk keys supporting the value. Must be empty when status is assumed.",
    )
    note: str = Field(
        default="",
        description=(
            "For assumed: why the SOW does not settle it. For explicit/inferred: "
            "any caveat, such as a conditional or disputed alternative value."
        ),
    )
    hint: str = Field(
        default="",
        description=(
            "A direction the SOW points at without committing to it, e.g. "
            "'probably API keys'. Set this instead of value when the SOW gestures "
            "at an answer without settling it."
        ),
    )


class GapReport(BaseModel):
    findings: list[FieldFinding] = Field(
        description="Exactly one finding per requested field key, in the order asked."
    )
