# SOW-to-Project Execution Platform

AI-powered platform that turns a loyalty-points company's Scope of Work (SOW)
into a traceable, validated, PM-approved project plan pushed to Trello/Plane.

The differentiator is not "PDF to tasks". It is that every generated task can be
traced to the exact SOW text it came from, values the SOW never stated are
labelled as assumptions rather than presented as fact, and nothing reaches a
task manager without a human approving it.

## Status

| Milestone | State |
|---|---|
| M0 — foundation, DB schema, synthetic SOW corpus | done |
| M1 — SOW → extraction → tasks → Trello → UI | done, verified end to end against a real Trello board |
| M2 — assumption engine | done |
| M3 — dependency validation, critical path, timeline | done |
| M4 — claim-level grounding, validation pipeline | done |
| M5–M7 — HITL, RAG copilot, evaluation | not started |

Extraction quality against the gold standards (`gemini-3.7-flash`):

| Document | Tasks | Coverage | Citations | Evidence | Assumption recall | Hallucinated |
|---|---|---|---|---|---|---|
| A (clean) | 9 | 100% | 100% | 100% | n/a — no gaps | none |
| B (gappy) | 7 | 100% | 100% | 100% | 100% | none |
| C (adversarial) | 8 | 100% | 100% | 100% | 50% | none |

C's two unflagged gaps are document-specific open questions (an offer list to be
supplied later, a POS upgrade plan) rather than the standard planning fields the
checklist covers. Catching those needs an open-questions pass, which is not built.

## Setup

```bash
python -m uv sync
cp .env.example .env
docker compose up -d
.venv/Scripts/python -m alembic upgrade head
```

Add your Google AI Studio key to `.env` as `GEMINI_API_KEY`, plus Trello
credentials if you want to push a board.

Note the Gemini free tier allows **20 requests per day per model**. A full
pipeline run costs four: extraction, gap audit, claim extraction, verification.
`scripts/seed_demo.py` runs the pipeline with a stubbed model and spends none.

Run the API and the UI in two terminals:

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload
```

```bash
.venv/Scripts/python -m streamlit run ui/app.py
```

The UI is at http://localhost:8501 and the API docs at http://localhost:8000/docs.

To explore the UI without an API key (uses a stubbed extraction, no quota spent):

```bash
.venv/Scripts/python scripts/seed_demo.py
```

## Tests

```bash
.venv/Scripts/python -m pytest
```

Tests need PostgreSQL running (`docker compose up -d`) but never call Gemini —
extraction is stubbed in `tests/factories.py`.

## Layout

| Path | Purpose |
|---|---|
| `app/ingestion/` | PDF/DOCX/TXT parsing into sections and chunks with citable keys; parsing-validation gate |
| `app/llm/` | Gemini client with structured output, response caching, audit logging; versioned prompts |
| `app/graph/` | LangGraph workflow, assumption engine, persistence |
| `app/planning/` | Dependency validation, CPM critical path, scheduling |
| `app/grounding/` | Claim extraction, evidence retrieval, verification, scoring |
| `app/validation/` | The six-stage validation pipeline |
| `app/rag/` | ChromaDB index over SOW chunks |
| `evals/` | Scores a real extraction against the gold standards |
| `app/taskmanager/` | Platform-neutral task interface plus the Trello adapter |
| `app/api/` | FastAPI routes |
| `ui/` | Streamlit UI |
| `data/sample_sows/` | Synthetic SOW corpus and gold standards ([details](data/sample_sows/README.md)) |

See [plan-breef.md](plan-breef.md) for the full product brief.

## Design notes

**Chunk keys are the backbone.** Every chunk gets a stable key like
`SOW-001-S04-C02`. Tasks store the keys they cite, the UI resolves them back to
the original text, and citations pointing at keys the document does not contain
are dropped at persistence time rather than stored.

**The parsing gate is real.** If a document fails validation the graph routes to
human review and the LLM is never called, so the model cannot reason confidently
over a broken parse.

**Provenance is a first-class field.** Everything generated is `explicit`,
`inferred`, or `assumed`, and assumptions carry a reason and confidence.

**Gaps are found by checklist, not volunteered.** Asking the model to mention
what a SOW left out measured 0 of 7 gaps on a deliberately vague document — it
did not invent the missing numbers, it simply said nothing about them. So the
planning-critical fields are enumerated in `app/schemas/fields.py` and audited
one by one. A field the model claims is stated but cannot cite is demoted to an
assumption, and so is a value like "standard scheme" or "TBD" that gestures at
an answer without giving one. On the clean SOW this produces zero assumptions;
on the vague one, thirteen.

**Scheduling is deterministic.** The model proposes dependencies; code decides
which survive. Self-references, edges to tasks that do not exist, and cycles are
removed and reported rather than allowed to reach the scheduler, and the critical
path comes from a plain CPM pass — no model involved. It is computed across the
whole project, because the delays that matter run between teams: technical
validation holding up operations configuration, not one team's internal order.

**Grounding is per claim, not per task.** A task can describe the right work and
still assert a number the SOW never gave, so each task is split into atomic
claims and each claim is judged on its own evidence. On a real run the model
generated "perform stress testing" for a SOW that specifies performance targets
but never asks for stress testing; the claim was caught while the rest of the
task was accepted. Score is supported/total, and a single contradicted claim
rejects a task outright instead of being averaged away.

**The whole project is grounded in two LLM calls.** The free tier allows 20
requests per day, so a call per task spent a day's budget on one project. Claims
for every task are extracted in one call and judged in another; a test pins that
budget so the shape cannot regress.
