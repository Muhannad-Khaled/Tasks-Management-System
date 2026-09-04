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
| M1 — SOW → extraction → tasks → Trello → UI | pipeline done; needs `GEMINI_API_KEY` for real extraction |
| M2–M7 — teams, planning, grounding, HITL, RAG, evaluation | not started |

## Setup

```bash
python -m uv sync
cp .env.example .env
docker compose up -d
.venv/Scripts/python -m alembic upgrade head
```

Add your Google AI Studio key to `.env` as `GEMINI_API_KEY`, plus Trello
credentials if you want to push a board.

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
| `app/graph/` | LangGraph workflow and persistence |
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
