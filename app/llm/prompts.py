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
    version="v9",
    system=_GROUNDING_RULES,
    template="""Extract a structured project plan from the SOW below.

The SOW has been split into chunks. Each chunk is labelled with its key in
square brackets, e.g. [SOW-001-S04-C02]. Use those exact keys when citing.

=== SOW: {doc_key} ===
{chunked_text}
=== END SOW ===

Produce:
1. project_info: name, merchant, and any dates the SOW states.
2. team_roster: the delivery roles the SOW names, with the team each belongs
   to and what it is responsible for. Cite the chunk keys that name them.
   If the SOW does not describe the team at all, return an empty roster —
   do NOT populate it from the guide below, which exists only to name the
   roles tasks may be assigned to.
3. requirements: what must be delivered, each assigned to exactly one team
   (commercial, technical, or operations). Give each a stable id
   (REQ-001, REQ-002, ...).
4. milestones: every dated checkpoint the SOW commits to between kickoff
   and go-live — usually a table of milestones with dates and owners. Extract
   these BEFORE sizing any task, because they are the windows the estimates
   have to fit. Take the dates exactly as written and cite the chunk that
   states them. If the SOW gives no intermediate dates, return an empty list
   rather than inventing checkpoints.
5. tasks: concrete units of work implementing those requirements. Give each a
   stable id (T-001, T-002, ...), a team, the requirement_id it
   implements, any depends_on task ids, and the milestone it contributes to
   (named exactly as you listed it above, or empty if none).
   Every task needs estimated_hours: the person-hours the work itself takes,
   not counting time spent waiting for another team. A SOW almost never states
   this, so judge it from the work described and give a real number — a
   contract countersignature is not the same size as building an API.

   Anchor those numbers to the dates the SOW does give. Where it states
   milestones — "technical integration complete by 10 November", "staff
   training complete by 27 November" — the working days from one milestone to
   the next are the time both parties agreed that stretch of work would take.
   Size the tasks under each milestone so the ones that run in sequence
   roughly fill their window instead of a fraction of it. Tasks that run in
   parallel share a window rather than adding to it, so count the longest
   chain, not the total.

   Never shrink an estimate to make it fit. If the work genuinely needs more
   time than the SOW allows, give the larger number: that gap is the most
   useful thing this plan can report, and trimming it would only make the
   schedule agree with the contract by pretending.
   Cross-team dependencies matter: a technical integration usually depends on
   the commercial contract, and operations configuration usually depends on
   technical validation.

   Every entry in depends_on carries its own source_status, because an arrow
   moves every date after it and the PM has to know whether the SOW ordered
   the work or you did:
     - EXPLICIT: the document states the ordering. Cite the chunk that says
       so in source_chunk_keys. If you cannot cite it, it is not explicit.
     - INFERRED: the ordering follows from what the document describes — one
       task produces what the other consumes. Cite the chunks it follows from.
     - ASSUMED: neither. You are ordering these because that is how such work
       normally runs. Say so plainly and cite nothing.
   Give a one-sentence rationale for every arrow. An arrow whose only defence
   is "this usually comes first" is ASSUMED, however reasonable it sounds.
   Set assignee_role to the role on that task's own team best suited to the
   work, chosen from this roster:

{role_guide}

   Use a role from the task's own team. Never write a person's name — the SOW
   names roles, and inventing a person is worse than naming none.
6. details: everything the SOW enumerates, each filed under one category
   from the list below. This is where the specifics go — the individual APIs,
   offers, merchants, configuration steps and training topics — as opposed to
   the work of delivering them, which is a task.

{category_guide}

   List an item only if the SOW mentions it. An empty category means the SOW
   said nothing about it, which is itself worth knowing.
7. assumptions: every gap you filled, with a reason and a confidence score.

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
    version="v2",
    system=(
        "You split a generated project task into the atomic factual claims it "
        "makes, so each can be checked against the SOW separately.\n\n"
        "A task and a SOW speak in different moods, and this is the thing to "
        "get right. A task says what someone will DO: 'Design the OAuth 2.0 "
        "flow'. A SOW says what must BE TRUE: 'authentication uses OAuth 2.0 "
        "client-credentials'. Extract the state the work is meant to bring "
        "about, never the act of bringing it about.\n\n"
        "This matters because a SOW almost never contains the words design, "
        "develop, implement, optimise, configure or conduct. A claim built "
        "around one of those verbs cannot be supported by any SOW, no matter "
        "how well the task matches the document — so it measures nothing and "
        "quietly condemns correct work.\n\n"
        "  task:  'Design client-credentials OAuth 2.0 flow for the POS integration'\n"
        "  claim: 'Authentication with the merchant POS uses OAuth 2.0 "
        "client-credentials'\n"
        "  wrong: 'An OAuth 2.0 flow will be designed'\n\n"
        "  task:  'Conduct performance and load testing'\n"
        "  claim: 'The integration sustains 50 transactions per second at peak'\n"
        "  wrong: 'Performance testing is conducted'\n\n"
        "A claim is one checkable assertion. Split anything compound: "
        "'Deliver 2 days of training across 5 branches by 27 November' is three "
        "claims — the duration, the branch count, and the date.\n\n"
        "Every task asserts something about the finished system or the "
        "engagement; find it rather than returning nothing. But do not invent "
        "claims the task does not make, and do not restate its purpose.\n\n"
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
    version="v2",
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
        "- Judge the state the claim describes, not the wording around it. A "
        "SOW requiring a property establishes that property; it does not also "
        "have to describe the work of building it. 'The SOW does not mention "
        "designing/testing/configuring this' is never a reason to withhold "
        "support.\n"
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

TECHNICAL_ARTIFACTS = Prompt(
    name="technical_artifacts",
    version="v2",
    system="""You write two kinds of user story, and confusing them is the main way
this task goes wrong.

CLIENT STORIES, one per requirement. These say what somebody outside the
project gets. The actor must be a person who *receives* value from the
finished system: a merchant, a cardholder, a cashier, a store manager. Never
someone delivering the project.
  wrong: 'As a support agent, I want to deliver 2 days of training'
  right: 'As a store cashier, I want to be trained on enrolment and redemption
         so that I can serve customers without help'
If the actor is a role on the delivery team, the sentence is a task in
disguise: it describes work being done rather than value someone receives, and
nobody outside the project can judge it done.

ENGINEER STORIES, one per technical task listed below. These are the opposite,
and the rule above does not apply to them: they describe how the assigned
engineer will build that one task. Write the capability in technical terms —
the interface, the component, the mechanism — at a level the named owner would
recognise as their own work. Do not write the actor; it is taken from the role
already assigned to the task.
  weak:   'implement the loyalty feature'
  strong: 'expose the accrual endpoint behind the switch interface and
          reconcile it against the core banking ledger nightly'

THE HARD RULE, and the one worth failing this task over. Technical notes and
every measured threshold come only from the project details listed below.
Those are what the signed document actually said. Naming a library, framework,
protocol, vendor or number that is not in that list makes the output worse
than useless: an engineer will build to it, and nobody will ever find out
where it came from. If the details do not cover something, leave the field
empty. An empty field is a correct answer here.

Whenever you fill technical_notes or a measure, copy the exact names of the
details you used into source_detail_names. A name that is not on the list will
be rejected and the content dropped.

Acceptance criteria state one observable condition each. Attach a measure when
the details give one, and only then.

A test case is a check somebody will actually run: a starting state, one
action, and the result that must be observed.

Rules:
- Every client story must name the requirement it comes from, using the
  requirement ids given. Do not invent an id.
- Every engineer story must name a task id exactly as given.
- Use the working values listed below when a test needs a concrete number, so
  the case is runnable.
- Whenever a test case relies on one of those values, list the field key in
  depends_on_fields. Some of them are values the SOW never stated, and a case
  built on one must be marked before anyone runs it and reports the system
  correct. Getting this list right matters more than the wording of the case.
- Do not invent requirements or tasks. Cover the ones given and stop.
""",
    template="""Write the derived artifacts for this project.

=== REQUIREMENTS (one client story each) ===
{requirements}
=== END REQUIREMENTS ===

=== TECHNICAL TASKS (one engineer story each) ===
{technical_tasks}
=== END TECHNICAL TASKS ===

=== PROJECT DETAILS ===
The only permitted source for technical notes and measured thresholds.
Everything here came from the signed document. Nothing outside it did.

{project_details}
=== END PROJECT DETAILS ===

Working values the plan currently runs on. Use these for concrete numbers in
test cases, and name the field key in depends_on_fields wherever a test relies
on one:

{working_values}

Cover every requirement and every technical task. Give each story 2-4
acceptance criteria and at least one test case.
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
        TECHNICAL_ARTIFACTS,
    ]
}
