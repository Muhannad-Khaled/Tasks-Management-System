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


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    # Order respects foreign keys.
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
