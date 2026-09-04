"""Seed a demo project without calling Gemini.

Runs the real pipeline (parse -> validate -> persist) with a stubbed extraction,
so the UI and API can be exercised without an API key or free-tier quota.

    .venv/Scripts/python scripts/seed_demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.db import Base, SessionLocal, engine
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.models import Project
from app.schemas.enums import ProjectStatus
from tests.factories import CORPUS, StubLLM


def main() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        sow = CORPUS / "sow_a_cairomart.pdf"
        doc = parse_document(sow, "SOW-DEMO")
        citations = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()][:2]

        project = Project(name="Demo — CairoMart (stubbed)", status=ProjectStatus.INGESTING)
        db.add(project)
        db.commit()

        state = run_sow_pipeline(
            db, project.id, str(sow), "SOW-DEMO", client=StubLLM(citations=citations)
        )
        print(f"project_id     : {project.id}")
        print(f"parsing status : {state['parsing_status']}")
        print(f"tasks created  : {state['task_count']}")
        for warning in state.get("warnings", []):
            print(f"warning        : {warning}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
