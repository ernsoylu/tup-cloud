"""FastAPI dependencies: db session, redis, telegram service, current user,
and per-drive access enforcement."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.events import EventHub
from app.membership import member_chat_ids
from app.models import User
from app.security import ACCESS_COOKIE, decode_access_token
from app.telegram import TelegramService

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_tg(request: Request) -> TelegramService:
    return request.app.state.tg


def get_hub(request: Request) -> EventHub:
    return request.app.state.hub


RedisDep = Annotated[Redis, Depends(get_redis)]
TgDep = Annotated[TelegramService, Depends(get_tg)]
HubDep = Annotated[EventHub, Depends(get_hub)]


async def get_current_user(request: Request, db: DbSession) -> User:
    token = request.cookies.get(ACCESS_COOKIE)
    payload = decode_access_token(token) if token else None
    if payload is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await db.get(User, int(payload["sub"]))
    if user is None or not user.approved:
        raise HTTPException(status_code=401, detail="Account not available")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


async def user_chat_ids(
    user: CurrentUser, db: DbSession, redis: RedisDep, tg: TgDep
) -> list[str]:
    """Drives visible to this user (admin: all registered drives)."""
    if user.role == "admin":
        from sqlalchemy import select

        from app.models import ChatAlias

        return [a.chat_id for a in (await db.execute(select(ChatAlias))).scalars().all()]
    return await member_chat_ids(db, redis, tg, user.telegram_id)


UserChats = Annotated[list[str], Depends(user_chat_ids)]


async def assert_chat_access(chat_id: str, chats: list[str]) -> None:
    if chat_id not in chats:
        raise HTTPException(status_code=403, detail="No access to this drive")
