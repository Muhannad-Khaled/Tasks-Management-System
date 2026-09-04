from fastapi import APIRouter
from sqlalchemy import text

from app.core.db import engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "up"
    except Exception:  # noqa: BLE001 - a health probe must report, never raise
        db_status = "down"
    return {"status": "ok", "database": db_status}
