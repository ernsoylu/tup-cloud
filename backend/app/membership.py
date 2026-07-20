"""Login authorization and per-user drive visibility.

Policy: a Telegram account may use tup-cloud iff it is a member of at least one
registered drive chat (group, channel, or — for private-chat drives — the chat
is that user itself). Users see only the drives they are members of; admins see
all. Results are cached briefly in Redis to keep getChatMember chatter down.
"""

from __future__ import annotations

import json

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ChatAlias
from app.telegram import TelegramService

_MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}
_CACHE_PREFIX = "membership:"
_CACHE_TTL = 300


async def member_chat_ids(
    db: AsyncSession, redis: Redis, tg: TelegramService, telegram_id: int
) -> list[str]:
    """Chat ids (drives) the Telegram user belongs to, Redis-cached."""
    cache_key = f"{_CACHE_PREFIX}{telegram_id}"
    cached = await redis.get(cache_key)
    if cached is not None:
        return json.loads(cached)

    aliases = (await db.execute(select(ChatAlias))).scalars().all()
    member_of: list[str] = []
    for alias in aliases:
        if _is_private_chat_of(alias.chat_id, telegram_id):
            member_of.append(alias.chat_id)
            continue
        status = await tg.get_chat_member_status(alias.chat_id, telegram_id)
        if status in _MEMBER_STATUSES:
            member_of.append(alias.chat_id)

    await redis.set(cache_key, json.dumps(member_of), ex=_CACHE_TTL)
    return member_of


def _is_private_chat_of(chat_id: str, telegram_id: int) -> bool:
    try:
        return int(chat_id) == telegram_id
    except ValueError:
        return False


def is_whitelisted(identifier: str, telegram_id: int) -> bool:
    allowed = {
        item.strip().lstrip("@").lower()
        for item in get_settings().allowed_telegram_ids.split(",")
        if item.strip()
    }
    return bool(allowed) and (
        str(telegram_id) in allowed or identifier.strip().lstrip("@").lower() in allowed
    )


async def invalidate_membership_cache(redis: Redis, telegram_id: int | None = None) -> None:
    if telegram_id is not None:
        await redis.delete(f"{_CACHE_PREFIX}{telegram_id}")
        return
    async for key in redis.scan_iter(f"{_CACHE_PREFIX}*"):
        await redis.delete(key)
