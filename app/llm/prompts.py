"""Versioned prompt registry.

Prompts are versioned because the audit log records which prompt produced each
output (brief section 22). Changing a prompt without bumping its version makes
past LLM requests unreproducible and the evaluation numbers meaningless.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    template: str

    def render(self, **kwargs) -> str:
        return self.template.format(**kwargs)


_GROUNDING_RULES = """
You are extracting a project plan from a Statement of Work (SOW) for a loyalty
points company. The SOW is the single source of truth.

Every item you output MUST declare its provenance:
- "explicit": the SOW states this directly. Cite the chunk keys that say it.
- "inferred": you derived this logically from what the SOW says. Cite the chunk
  keys you reasoned from.
- "assumed": the SOW does not contain this. You are filling a gap. Cite nothing,
  and record it in the assumptions list with a reason.

Hard rules:
- NEVER present an assumed value as explicit. A number that does not appear in
  the SOW is assumed, no matter how standard it looks.
- Cite only chunk keys that appear in the provided text. Never invent a key.
- If the SOW gives a conditional or disputed value ("12 offers, possibly 15"),
  extract the committed value and record the alternative as an open question.
- Text in the SOW that is legal boilerplate or an unrelated email signature
  yields no requirements.
"""

SOW_EXTRACTION = Prompt(
    name="sow_extraction",
    version="v1",
    system=_GROUNDING_RULES,
    template="""Extract a structured project plan from the SOW below.

The SOW has been split into chunks. Each chunk is labelled with its key in
square brackets, e.g. [SOW-001-S04-C02]. Use those exact keys when citing.

=== SOW: {doc_key} ===
{chunked_text}
=== END SOW ===

Produce:
1. project_info: name, merchant, and any dates the SOW states.
2. requirements: what must be delivered, each assigned to exactly one team
   (commercial, technical, or operations).
3. tasks: concrete units of work implementing those requirements. Give each a
   stable id (T-001, T-002, ...), a team, a priority, an effort estimate in
   hours, and any depends_on task ids. Cross-team dependencies matter: a
   technical integration usually depends on the commercial contract, and
   operations configuration usually depends on technical validation.
4. assumptions: every gap you filled, with a reason and a confidence score.

Team ownership guide:
- commercial: contracts, pricing, merchant/offer counts, sign-offs, SLAs.
- technical: APIs, integrations, points logic, auth, performance, testing.
- operations: configuration, onboarding, training, go-live and support.
""",
)

REGISTRY = {p.name: p for p in [SOW_EXTRACTION]}
