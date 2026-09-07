import asyncio
import contextlib
import logging

from fastapi import FastAPI

from app.api import health, projects
from app.core.config import get_settings
from app.taskmanager.watch import DEFAULT_INTERVAL_SECONDS, watch_boards

logger = logging.getLogger(__name__)

# 0 turns the watcher off, for tests and for anyone who would rather run
# scripts/check_boards.py themselves.
#
# Read through Settings rather than os.environ: every other setting in this
# project comes from .env, so one that quietly ignored the file was a setting
# people would believe they had changed. It cost an afternoon once already.
_configured = get_settings().board_watch_seconds
WATCH_INTERVAL = DEFAULT_INTERVAL_SECONDS if _configured is None else _configured


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    """Keep an eye on the boards for as long as the API is up."""
    watcher = None
    if WATCH_INTERVAL > 0:
        watcher = asyncio.create_task(watch_boards(WATCH_INTERVAL))
        logger.info("Watching boards every %ds", WATCH_INTERVAL)
    yield
    if watcher:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher


app = FastAPI(
    lifespan=lifespan,
    title="SOW-to-Project Execution Platform",
    description="Transforms a Scope of Work into a traceable, validated, PM-approved project plan.",
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(projects.router)
app.include_router(projects.people_router)
