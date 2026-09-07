"""Shared fixtures.

Tests run against their own PostgreSQL database, never the development one:
the cleanup between tests truncates every table, which would otherwise delete
real extraction results. The override must happen before app.core.db is
imported, since the engine is created at import time.

Tests never call a real LLM either — extraction is stubbed in factories.py, so
the suite runs offline and consumes no Gemini quota.
"""

import os

import pytest
from sqlalchemy import create_engine, text

_DEV_URL = os.environ.get("DATABASE_URL") or "postgresql+psycopg://sow:sow@localhost:5432/sow_platform"
TEST_DB = "sow_platform_test"
_TEST_URL = _DEV_URL.rsplit("/", 1)[0] + f"/{TEST_DB}"


def _ensure_test_database() -> None:
    admin = create_engine(_DEV_URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DB}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    admin.dispose()


_ensure_test_database()
os.environ["DATABASE_URL"] = _TEST_URL

from app.core.db import Base, SessionLocal, engine
from tests.factories import CORPUS, StubLLM

__all__ = ["CORPUS", "StubLLM"]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    assert engine.url.database == TEST_DB, (
        f"tests must not run against {engine.url.database!r}; "
        "the cleanup step drops every table"
    )
    # Rebuilt from the models each session, so a new column never leaves the
    # test schema stale — create_all alone does not alter existing tables.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _no_embedding(monkeypatch):
    """Keep ChromaDB out of the suite.

    Embedding every chunk on every pipeline test took the suite from 20s to
    over four minutes, and retrieval is an aid to grounding rather than a
    behaviour these tests assert.

    The consequence is that ChromaDB has no automated coverage at all: indexing
    and retrieval are only exercised by running the real pipeline. That is a
    known gap, not a decision that retrieval does not need testing.
    """
    monkeypatch.setattr("app.graph.workflow.index_document", lambda *a, **k: 0)
    monkeypatch.setattr("app.graph.workflow.search", None)


# Children before parents. A table missing here does not fail loudly: its rows
# survive, the DELETE of whatever they reference raises, and the cleanup dies
# mid-way. Add new tables to this list when they gain a foreign key.
_CLEANUP_ORDER = [
    "board_drift",
    "acceptance_criteria",
    "test_cases",
    "user_stories",
    "claims",
    "validation_logs",
    "milestone_tasks",
    "task_dependencies",
    "project_tasks",
    "project_requirements",
    "project_roles",
    "project_details",
    "project_milestones",
    "assumptions",
    "clarification_questions",
    "sow_chunks",
    "sow_sections",
    "sow_documents",
    "llm_requests",
    "projects",
    # After project_roles, which points at it.
    "person_roles",
    "people",
]


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
        session.rollback()
        for table in _CLEANUP_ORDER:
            session.execute(text(f"DELETE FROM {table}"))
        session.commit()
    finally:
        # Without this, a failing DELETE leaves the connection open in an
        # aborted transaction holding locks, and the *next* run's drop_all
        # blocks on them forever. One broken test then looks like a hung suite.
        session.close()


@pytest.fixture(autouse=True)
def _no_live_model(request, monkeypatch):
    """Nothing in the suite may reach the real model.

    One endpoint built its client inline, so rejecting a task called Google
    from inside a unit test: it passed when the API answered, failed when it
    was busy, and spent the project's daily quota on every full run. The test
    was measuring Google's weather, not this codebase.

    Raising here makes that mistake loud the first time it is made again,
    rather than the fiftieth time the suite is mysteriously red.
    """

    if request.node.get_closest_marker("builds_a_real_client"):
        # test_llm_client.py constructs one on purpose to exercise the retry
        # and fallback logic, over a mocked transport. It never leaves the
        # process; blocking it would only stop that logic being tested at all.
        return

    def refuse(self, *args, **kwargs):
        raise AssertionError(
            "A test tried to construct a real GeminiClient. Pass a StubLLM, or "
            "override the get_llm_client dependency."
        )

    monkeypatch.setattr("app.llm.client.GeminiClient.__init__", refuse)
