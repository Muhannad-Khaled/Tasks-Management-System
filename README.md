# SOW-to-Project Execution Platform

AI-powered platform that transforms a loyalty-points company's Scope of Work (SOW)
into a traceable, validated, PM-approved project plan pushed to Trello/Plane.

Differentiators: SOW traceability, claim-level grounding, deterministic validation,
cross-team dependency reasoning, and human-in-the-loop approval.

## Dev setup

```
python -m uv sync            # install deps into .venv
cp .env.example .env         # fill in GEMINI_API_KEY, Trello creds
docker compose up -d         # Postgres :5432, ChromaDB :8001
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m uvicorn app.main:app --reload
```

See `plan-breef.md` for the full product brief.
