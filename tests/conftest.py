"""Shared fixtures.

Tests run against a real PostgreSQL (docker compose up -d) but never against a
real LLM: extraction is stubbed so the pipeline is testable offline, in CI, and
without consuming the Gemini free-tier quota.
"""

import pytest
from sqlalchemy import text

from app.core.db import Base, SessionLocal, engine
from tests.factories import CORPUS, StubLLM

__all__ = ["CORPUS", "StubLLM"]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    # Keep the database clean between tests; order respects foreign keys.
    for table in [
        "task_dependencies",
        "project_tasks",
        "assumptions",
        "sow_chunks",
        "sow_sections",
        "sow_documents",
        "llm_requests",
        "projects",
    ]:
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    session.close()
