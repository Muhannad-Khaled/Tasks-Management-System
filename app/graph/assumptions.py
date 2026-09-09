"""The assumption engine (brief section 12).

Runs an explicit audit of the planning-critical fields rather than trusting the
extraction pass to volunteer what was missing. Every field comes back as
explicit, inferred, or assumed, and every assumed field becomes a recorded
assumption carrying a default, a reason, and a confidence.

The point is that a gap is loud. A PM reading the plan sees that the SOW never
specified the offer count, instead of reading a confident task list whose
foundations were quietly invented.
"""

from __future__ import annotations

import logging
import re

from app.graph.persistence import normalize_citation
from app.schemas.enums import SourceStatus
from app.schemas.fields import PLANNING_FIELDS, FieldSpec
from app.schemas.gaps import FieldFinding, GapReport

logger = logging.getLogger(__name__)

# A field the model reported as explicit but could not cite is not explicit.
# Downgrading it keeps unsupported claims out of the "stated in the SOW" bucket.
_REQUIRES_EVIDENCE = {SourceStatus.EXPLICIT, SourceStatus.INFERRED}

# Wording that gestures at a value without settling it. The prompt says to treat
# these as gaps; this is the deterministic backstop for when it does not.
#
# Strong markers defer the answer outright, so they demote the field whatever
# else the value says.
_DEFERS_THE_ANSWER = (
    "tbd",
    "to be confirmed",
    "to be agreed",
    "to be determined",
    "to be provided",
    "to be defined",
    "not stated",
    "not specified",
    "unspecified",
    "unknown",
    "n/a",
)

# Weak markers only mean vagueness when nothing concrete accompanies them.
# "Standard revenue-share model" settles nothing, but "2.5% of each redeemed
# offer (standard LoyaltyCo model)" does — demoting that would turn a stated
# fact into an invented assumption, the exact error this system exists to avoid.
_VAGUE_UNLESS_QUANTIFIED = ("standard", "typical", "usual", "probably", "likely", "approximately")

_HAS_QUANTITY = re.compile(r"\d")

CONFIDENCE_BY_CATEGORY = {
    "SCHEDULE": 0.6,
    "TEAM_SIZE": 0.5,
    "PERFORMANCE": 0.5,
    "SLA_TARGETS": 0.5,
}
DEFAULT_CONFIDENCE = 0.7


def render_field_list(fields: list[FieldSpec]) -> str:
    return "\n".join(f"- {f.key}: {f.question}" for f in fields)


def _is_non_committal(value: str) -> bool:
    """Whether a value gestures at an answer without actually giving one."""
    lowered = value.strip().lower()
    if not lowered:
        return True
    if any(marker in lowered for marker in _DEFERS_THE_ANSWER):
        return True
    if any(marker in lowered for marker in _VAGUE_UNLESS_QUANTIFIED):
        return not _HAS_QUANTITY.search(lowered)
    return False


def validate_findings(
    report: GapReport, fields: list[FieldSpec], valid_chunk_keys: set[str]
) -> tuple[dict[str, FieldFinding], list[str]]:
    """Normalize findings and force unsupported ones down to ASSUMED.

    Returns the findings by field key plus warnings describing every downgrade,
    so the audit trail records what the model claimed and why it was rejected.
    """
    warnings: list[str] = []
    by_key: dict[str, FieldFinding] = {}

    for finding in report.findings:
        spec = next((f for f in fields if f.key == finding.field_key), None)
        if spec is None:
            warnings.append(f"gap audit returned unknown field {finding.field_key!r}")
            continue

        finding.evidence_chunk_keys = [
            key
            for key in (normalize_citation(k) for k in finding.evidence_chunk_keys)
            if key in valid_chunk_keys
        ]

        if finding.status in _REQUIRES_EVIDENCE:
            if not finding.evidence_chunk_keys:
                warnings.append(
                    f"{finding.field_key}: claimed {finding.status} with no usable "
                    "citation, downgraded to assumed"
                )
                finding.status = SourceStatus.ASSUMED
                finding.value = ""
            elif _is_non_committal(finding.value):
                warnings.append(
                    f"{finding.field_key}: value {finding.value!r} does not settle the "
                    "field, downgraded to assumed"
                )
                finding.status = SourceStatus.ASSUMED
                # Keep the wording as a hint rather than discarding it. The SOW
                # saying "probably API keys" does not settle the field, but
                # assuming OAuth instead would contradict the document — worse
                # than assuming nothing.
                finding.hint = finding.hint or finding.value
                finding.value = ""
        by_key[finding.field_key] = finding

    # A field the audit skipped is a gap, not a pass.
    for spec in fields:
        if spec.key not in by_key:
            warnings.append(f"{spec.key}: not reported by the gap audit, treated as a gap")
            by_key[spec.key] = FieldFinding(
                field_key=spec.key,
                status=SourceStatus.ASSUMED,
                value="",
                note="The audit did not report on this field.",
            )
    return by_key, warnings


def build_assumptions(
    findings: dict[str, FieldFinding], fields: list[FieldSpec] | None = None
) -> list[dict]:
    """Turn every assumed field into a recorded assumption with a default."""
    fields = fields or PLANNING_FIELDS
    assumptions: list[dict] = []
    for spec in fields:
        finding = findings.get(spec.key)
        if finding is None or finding.status != SourceStatus.ASSUMED:
            continue
        reason = finding.note.strip() or f"The SOW does not specify the {spec.label}."
        # A direction the SOW pointed at beats a generic default. Assuming
        # OAuth for a SOW that says "probably API keys" would have the plan
        # contradict the document it came from.
        hint = finding.hint.strip()
        confidence = CONFIDENCE_BY_CATEGORY.get(spec.category, DEFAULT_CONFIDENCE)
        if hint:
            reason = f"{reason} Using the direction the SOW indicates rather than a default."
            confidence = min(confidence + 0.1, 1.0)
        assumptions.append(
            {
                "assumption_key": f"A-{len(assumptions) + 1:03d}",
                "category": spec.category,
                "value": hint or spec.default,
                "reason": reason,
                "confidence": confidence,
            }
        )
    return assumptions


def grounded_fields(findings: dict[str, FieldFinding]) -> dict[str, FieldFinding]:
    """Fields the SOW actually settles, for use downstream in planning."""
    return {
        key: finding
        for key, finding in findings.items()
        if finding.status in _REQUIRES_EVIDENCE and finding.value
    }


def build_questions(
    findings: dict[str, FieldFinding], fields: list[FieldSpec] | None = None
) -> list[dict]:
    """Turn every unanswered field into a question worth asking.

    An assumption and a question are the same fact seen from two sides: the
    plan runs on the assumption, and the question is what would replace it.
    Deriving one from the other means the question set can never drift from
    what the plan actually assumed, and costs no model call.

    A field the SOW settled produces no question, which is the point — nobody
    should be asked to confirm something the document already states.
    """
    fields = fields or PLANNING_FIELDS
    questions: list[dict] = []
    for spec in fields:
        finding = findings.get(spec.key)
        if finding is None or finding.status != SourceStatus.ASSUMED:
            continue
        hint = finding.hint.strip()
        questions.append(
            {
                "question_key": f"Q-{len(questions) + 1:03d}",
                "scope": str(spec.scope),
                "category": spec.category,
                "field_key": spec.key,
                "team": str(spec.team) if spec.team else "",
                "text": spec.question,
                "working_assumption": hint or spec.default,
            }
        )
    return questions


def audit_coverage(
    open_field_keys: set[str],
    assumption_count: int | None = None,
    fields: list[FieldSpec] | None = None,
) -> dict:
    """What the field audit examined, and where its sight stops.

    The audit reports nine gaps and says nothing else, and nine gaps reads as
    the whole of what the SOW left out. It is not. It is the whole of what the
    SOW left out *among the fields listed here* — a live run on a document
    requiring a data retention policy without stating a period produced no
    question at all, because no field asks about retention.

    For a platform whose entire claim is that it says what a document does not,
    an unexamined subject and an examined one that came back clean cannot look
    identical. So the scope of the check is reported alongside its result.

    `answered` is derived rather than stored: a listed field with no open
    question is one the audit found an answer for. When `assumption_count` is
    given it is cross-checked against the open fields, since both come from the
    same findings and any disagreement means the derivation cannot be trusted.
    """
    fields = fields or PLANNING_FIELDS
    known = {spec.key for spec in fields}
    # Questions from an older run may name a field the list has since dropped.
    # Counting those as open would report more gaps than there are fields.
    open_keys = open_field_keys & known
    answered = [spec for spec in fields if spec.key not in open_keys]

    reliable = assumption_count is None or assumption_count == len(open_keys)
    return {
        "checked": len(fields),
        "answered": len(answered),
        "open": len(open_keys),
        "reliable": reliable,
        "answered_fields": [
            {"key": spec.key, "label": spec.label, "question": spec.question}
            for spec in answered
        ],
        "boundary": (
            f"The audit asks {len(fields)} questions of every SOW. Anything outside "
            "them was not examined, so silence here is not a finding — a subject "
            "this list does not cover produces no gap however little the document "
            "says about it."
        ),
    }
