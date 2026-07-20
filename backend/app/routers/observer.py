"""Observer feed API: recent ingestion events for drives the user can see."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, UserChats
from app.models import ObserverEvent

router = APIRouter(prefix="/api/observer", tags=["observer"])


@router.get("/events")
async def recent_events(
    _: CurrentUser, chats: UserChats, db: DbSession, limit: int = 50
) -> list[dict]:
    rows = (
        await db.execute(
            select(ObserverEvent)
            .where(ObserverEvent.chat_id.in_(chats or ["-"]))
            .order_by(ObserverEvent.id.desc())
            .limit(min(limit, 200))
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "chat_id": r.chat_id,
            "message_id": r.message_id,
            "file_name": r.file_name,
            "virtual_path": r.virtual_path,
            "stage": r.stage,
            "detail": r.detail,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/status")
async def status(_: CurrentUser) -> dict:
    return {"enabled": get_settings().observer_enabled}
