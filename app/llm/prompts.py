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

GAP_DETECTION = Prompt(
    name="gap_detection",
    version="v1",
    system=(
        "You audit a Statement of Work for missing planning information. You are "
        "not writing a plan; you are reporting, field by field, what the document "
        "does and does not settle.\n\n"
        "For each field you are asked about, answer with exactly one finding:\n"
        "- explicit: the SOW states it outright. Give the value and cite the chunk "
        "keys that say it.\n"
        "- inferred: it follows necessarily from what the SOW says (for example a "
        "location count derived from a table of branches). Give the value and cite "
        "what you reasoned from.\n"
        "- assumed: the SOW does not provide it. Leave the value empty, cite "
        "nothing, and say in the note what is missing.\n\n"
        "Rules:\n"
        "- Vague wording is not a value. 'Standard scheme', 'industry-standard "
        "levels', 'to be confirmed', 'TBD' and 'probably X' all mean the SOW does "
        "NOT settle the field: report assumed. When the SOW gestures at an answer "
        "without committing ('probably API keys'), put that wording in `hint` so "
        "the plan follows the document's own direction instead of a generic "
        "default, and leave `value` empty.\n"
        "- A conditional or disputed figure ('12 offers, possibly 15') is reported "
        "as the committed value, with the alternative recorded in the note.\n"
        "- Do not be generous. Reporting a field as explicit when the SOW only "
        "gestures at it is the worst possible error.\n"
        "- Answer for every field key given, even when the answer is assumed."
    ),
    template="""Audit this SOW for the fields listed below.

=== SOW: {doc_key} ===
{chunked_text}
=== END SOW ===

Report one finding for each of these fields:

{field_list}
""",
)

CLAIM_EXTRACTION = Prompt(
    name="claim_extraction",
    version="v1",
    system=(
        "You split a generated project item into the atomic factual claims it "
        "makes, so each can be checked against the SOW separately.\n\n"
        "A claim is one checkable assertion. Split anything compound: "
        "'Deliver 2 days of training across 5 branches by 27 November' is three "
        "claims — the duration, the branch count, and the date.\n\n"
        "Only extract assertions about the project that could be true or false "
        "against the SOW. Do not extract restatements of the task's own purpose "
        "('this task implements the integration'), and do not invent claims the "
        "item does not make.\n\n"
        "Mark a claim as quantitative when it asserts a number, date, duration, "
        "or rate — those are where unsupported detail does the most damage."
    ),
    template="""Split each generated task below into atomic claims.

Return one entry per task, using the task reference exactly as given.
Claim ids must be unique across the whole response.

{tasks}
""",
)

CLAIM_VERIFICATION = Prompt(
    name="claim_verification",
    version="v1",
    system=(
        "You judge whether SOW evidence establishes each claim. You are the "
        "check on a generation step, so err towards scepticism.\n\n"
        "Verdicts:\n"
        "- supported: the evidence states the claim, or it follows directly and "
        "necessarily from what the evidence states.\n"
        "- partial: the evidence is about the same subject but does not "
        "establish the claim. A claim of '2 days of training' against evidence "
        "that only says training is required is partial, not supported.\n"
        "- unsupported: nothing in the evidence establishes the claim.\n"
        "- contradicted: the evidence says something incompatible with it.\n\n"
        "Rules:\n"
        "- Judge only against the evidence given. Plausibility, industry norms "
        "and your own knowledge are not evidence.\n"
        "- A number is supported only if the evidence gives that number or one "
        "it arithmetically determines. A different number is contradicted.\n"
        "- Cite the chunk keys that do the supporting, and only those.\n"
        "- Answer for every claim given."
    ),
    template="""Judge each claim against the SOW evidence below.

=== EVIDENCE ===
{evidence}
=== END EVIDENCE ===

Claims:
{claims}
""",
)

TASK_REGENERATION = Prompt(
    name="task_regeneration",
    version="v1",
    system=(
        _GROUNDING_RULES
        + "\n\nYou are regenerating a single task that a project manager "
        "rejected, or that failed grounding. Fix the specific problem you are "
        "told about.\n\n"
        "- If claims were unsupported, either drop them or restate the task so "
        "it only asserts what the SOW establishes. Do not defend the original "
        "wording.\n"
        "- If the PM gave a reason, treat it as binding.\n"
        "- Keep the task's team and its place in the plan unless the reason "
        "says otherwise.\n"
        "- Cite only chunk keys present in the evidence below."
    ),
    template="""Regenerate this task.

Original title: {title}
Original description: {description}
Team: {team}

Why it is being regenerated:
{reason}

{failed_claims}

=== SOW EVIDENCE ===
{evidence}
=== END EVIDENCE ===

Return a single replacement task.
""",
)

REGISTRY = {
    p.name: p
    for p in [
        SOW_EXTRACTION,
        GAP_DETECTION,
        CLAIM_EXTRACTION,
        CLAIM_VERIFICATION,
        TASK_REGENERATION,
    ]
}
