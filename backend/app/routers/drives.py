"""Drive (registered chat) management. Users see only chats they are members
of; adding/removing drives is admin-only."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import AdminUser, CurrentUser, DbSession, HubDep, RedisDep, TgDep, UserChats
from app.membership import invalidate_membership_cache
from app.models import ChatAlias
from app.telegram import TupError

router = APIRouter(prefix="/api/drives", tags=["drives"])


class AddDriveBody(BaseModel):
    alias: str = Field(min_length=1, max_length=64, pattern=r"^[\w\-]+$")
    chat_id: str = Field(min_length=2, max_length=32)


@router.get("")
async def list_drives(_: CurrentUser, chats: UserChats, db: DbSession) -> list[dict]:
    aliases = (await db.execute(select(ChatAlias).order_by(ChatAlias.alias))).scalars().all()
    return [
        {"alias": a.alias, "chat_id": a.chat_id, "title": a.title or a.alias}
        for a in aliases
        if a.chat_id in chats
    ]


@router.post("")
async def add_drive(
    body: AddDriveBody, _: AdminUser, db: DbSession, tg: TgDep, redis: RedisDep, hub: HubDep
) -> dict:
    try:
        info = await tg.chat_info(body.chat_id)
    except TupError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Chat validation failed: {exc}. Is the bot a member of that chat?",
        ) from exc
    existing = (
        await db.execute(
            select(ChatAlias).where(
                (ChatAlias.alias == body.alias) | (ChatAlias.chat_id == info["chat_id"])
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Alias or chat already registered.")
    alias = ChatAlias(alias=body.alias, chat_id=info["chat_id"], title=info["title"])
    db.add(alias)
    await db.commit()
    await invalidate_membership_cache(redis)
    await hub.publish({"type": "drives-changed"})
    return {"alias": alias.alias, "chat_id": alias.chat_id, "title": alias.title}


@router.delete("/{alias}")
async def remove_drive(
    alias: str, _: AdminUser, db: DbSession, redis: RedisDep, hub: HubDep
) -> dict:
    row = await db.get(ChatAlias, alias)
    if row is None:
        raise HTTPException(status_code=404, detail="No such drive")
    await db.delete(row)
    await db.commit()
    await invalidate_membership_cache(redis)
    await hub.publish({"type": "drives-changed"})
    return {"ok": True}
