"""Live group observer.

Watches every registered drive chat through the bot's update stream. Media
messages that already carry a tup caption (sent by tup CLI/GUI or another
tup-cloud) are indexed at their declared path; any other file lands in the
drive's /Other folder through a staged pipeline whose progress is published to
the observer feed in real time:

    detected → analyzing → indexed   (or skipped / failed)

Private messages to the bot are also handled here: they cache the sender's
entity (making login-code DMs possible) and record shared contact phones so
phone-number login can match known users.
"""

from __future__ import annotations

import logging
import time

from redis.asyncio import Redis
from sqlalchemy import func, select
from telethon import TelegramClient, events

from app.config import get_settings
from app.db import SessionLocal
from app.events import EventHub
from app.membership import member_chat_ids
from app.models import ChatAlias, ObserverEvent, User, VfsEntry
from app.security import generate_bot_login_code, rate_limit, store_bot_login_code
from app.telegram import TelegramService, parse_caption
from app.vfsutil import extract_tags, route_for_mime, split_vfs_path, utc_now_iso

logger = logging.getLogger("tup.observer")

OTHER_DIR = "/Other/"

_chats_cache: tuple[float, set[str]] = (0.0, set())


async def _registered_chats() -> set[str]:
    global _chats_cache
    stamp, cached = _chats_cache
    if time.monotonic() - stamp < 60:
        return cached
    async with SessionLocal() as db:
        rows = (await db.execute(select(ChatAlias.chat_id))).scalars().all()
    _chats_cache = (time.monotonic(), set(rows))
    return _chats_cache[1]


def register(tg: TelegramService, hub: EventHub, redis: Redis) -> None:
    client: TelegramClient = tg.client

    @client.on(events.NewMessage(incoming=True))
    async def _on_message(event: events.NewMessage.Event) -> None:
        try:
            if event.is_private:
                await _handle_private(tg, redis, event)
                return
            chat_id = str(event.chat_id)
            if chat_id not in await _registered_chats():
                return
            message = event.message
            if message.media is None or message.file is None:
                return
            if message.sender_id == tg.bot_id:
                return  # our own uploads index themselves
            await _ingest(hub, chat_id, message)
        except Exception:
            logger.exception("Observer failed on update")


async def _handle_private(
    tg: TelegramService, redis: Redis, event: events.NewMessage.Event
) -> None:
    """Private chat with the bot: /login issues OTPs, /start explains, and every
    message caches the sender's entity and any shared contact phone."""
    sender = await event.get_sender()
    if sender is None or getattr(sender, "bot", False):
        return
    phone = ""
    if event.message.contact is not None and event.message.contact.user_id == sender.id:
        phone = event.message.contact.phone_number or ""
    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.telegram_id == sender.id))
        ).scalars().first()
        if user is not None:
            user.username = sender.username or user.username
            if phone:
                user.phone = phone.lstrip("+")
            await db.commit()

    text = (event.message.message or "").strip().lower()
    if text.startswith("/login"):
        await _handle_login_command(tg, redis, event, sender.id, blocked=(
            user is not None and not user.approved
        ))
    elif text.startswith("/start"):
        await event.reply(
            "👋 This bot powers a tup-cloud drive.\n\n"
            "Send /login to get a one-time login code for the web app. "
            "You can log in if you are a member of one of the drive chats."
        )


async def _handle_login_command(
    tg: TelegramService,
    redis: Redis,
    event: events.NewMessage.Event,
    telegram_id: int,
    *,
    blocked: bool,
) -> None:
    if blocked:
        await event.reply("🚫 This account has been blocked by an admin.")
        return
    if not await rate_limit(redis, f"botlogin:{telegram_id}", limit=3, window_seconds=300):
        await event.reply("⏳ Too many codes requested — try again in a few minutes.")
        return

    async with SessionLocal() as db:
        total_users = (await db.execute(select(func.count(User.id)))).scalar_one()
        is_member = bool(await member_chat_ids(db, redis, tg, telegram_id))
    # total_users == 0: bootstrap — the very first person in becomes admin.
    if total_users > 0 and not is_member:
        await event.reply(
            "🚫 You are not a member of any drive chat this bot serves, "
            "so login is not available for your account."
        )
        return

    code = generate_bot_login_code()
    await store_bot_login_code(redis, telegram_id, code)
    ttl_minutes = get_settings().login_code_ttl_seconds // 60
    await event.reply(
        f"🔐 Your tup-cloud login code:\n\n`{code}`\n\n"
        f"Enter it on the web login page within {ttl_minutes} minutes. "
        "It works once. If you did not request this, ignore it."
    )


async def _ingest(hub: EventHub, chat_id: str, message) -> None:
    file_name = message.file.name or f"msg{message.id}{message.file.ext or ''}"
    meta = parse_caption(message.message)

    async with SessionLocal() as db:
        record = ObserverEvent(
            chat_id=chat_id, message_id=message.id, file_name=file_name, stage="detected"
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
    await _publish(hub, chat_id, record)

    try:
        if meta is not None:
            await _index_declared(hub, chat_id, message, record, meta)
        else:
            await _index_other(hub, chat_id, message, record)
    except Exception as exc:
        logger.exception("Ingestion failed for message %s in %s", message.id, chat_id)
        await _update(hub, chat_id, record, "failed", str(exc)[:500])


async def _index_declared(hub, chat_id: str, message, record: ObserverEvent, meta) -> None:
    """A tup-captioned message from another tup instance: honor its path."""
    virtual_path, file_name = split_vfs_path(meta.full_path)
    record.file_name = file_name
    record.virtual_path = virtual_path
    await _update(hub, chat_id, record, "analyzing", "tup caption found — honoring declared path")
    async with SessionLocal() as db:
        existing = (
            await db.execute(
                select(VfsEntry).where(
                    VfsEntry.chat_id == chat_id,
                    VfsEntry.virtual_path == virtual_path,
                    VfsEntry.file_name == file_name,
                )
            )
        ).scalars().first()
        if existing is not None:
            await _update(hub, chat_id, record, "skipped", "already indexed")
            return
        db.add(
            _entry_from_message(
                chat_id, message, virtual_path, file_name, meta.sha256,
                user_caption=meta.user_caption or "",
            )
        )
        await db.commit()
    await _update(hub, chat_id, record, "indexed", meta.full_path)
    await hub.publish({"type": "index-changed", "chat_id": chat_id})


async def _index_other(hub, chat_id: str, message, record: ObserverEvent) -> None:
    """A plain file someone sent to the group → /Other with proper processing."""
    pseudo_hash = _pseudo_hash(message)
    mime = message.file.mime_type or "application/octet-stream"
    size = message.file.size or 0
    detail = f"{mime}, {size} bytes"
    await _update(hub, chat_id, record, "analyzing", detail)

    async with SessionLocal() as db:
        dupe = (
            await db.execute(
                select(VfsEntry).where(
                    VfsEntry.chat_id == chat_id,
                    VfsEntry.virtual_path == OTHER_DIR,
                    VfsEntry.file_hash == pseudo_hash,
                )
            )
        ).scalars().first()
        if dupe is not None:
            await _update(hub, chat_id, record, "skipped", f"duplicate of {dupe.file_name}")
            return

        taken = {
            e.file_name
            for e in (
                await db.execute(
                    select(VfsEntry).where(
                        VfsEntry.chat_id == chat_id, VfsEntry.virtual_path == OTHER_DIR
                    )
                )
            ).scalars()
        }
        file_name = _dedupe_name(record.file_name, taken)
        record.file_name = file_name
        db.add(
            _entry_from_message(
                chat_id, message, OTHER_DIR, file_name, pseudo_hash,
                user_caption=(message.message or "").strip(),
            )
        )
        await db.commit()

    await _update(hub, chat_id, record, "indexed", OTHER_DIR + record.file_name)
    await hub.publish({"type": "index-changed", "chat_id": chat_id})


def _entry_from_message(
    chat_id: str,
    message,
    virtual_path: str,
    file_name: str,
    file_hash: str,
    user_caption: str = "",
) -> VfsEntry:
    mime = message.file.mime_type or "application/octet-stream"
    return VfsEntry(
        chat_id=chat_id,
        virtual_path=virtual_path,
        file_name=file_name,
        file_size=message.file.size or 0,
        file_hash=file_hash,
        telegram_message_id=int(message.id),
        mime_type=mime,
        media_kind=route_for_mime(mime),
        width=getattr(message.file, "width", None),
        height=getattr(message.file, "height", None),
        duration=int(message.file.duration) if getattr(message.file, "duration", None) else None,
        origin="observed",
        uploaded_by=str(message.sender_id or ""),
        user_caption=user_caption,
        tags=extract_tags(user_caption),
    )


def _pseudo_hash(message) -> str:
    """Observed files are not downloaded, so no SHA-256; Telegram's document id
    is stable per unique media and serves as the dedup key."""
    media_id = getattr(getattr(message, "document", None), "id", None) or getattr(
        getattr(message, "photo", None), "id", None
    )
    return f"tg:{media_id or message.id}"


def _dedupe_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    for n in range(2, 1000):
        candidate = f"{stem} ({n})" + (f".{ext}" if ext else "")
        if candidate not in taken:
            return candidate
    return f"{stem}-{utc_now_iso()}" + (f".{ext}" if ext else "")


async def _update(hub, chat_id: str, record: ObserverEvent, stage: str, detail: str = "") -> None:
    async with SessionLocal() as db:
        row = await db.get(ObserverEvent, record.id)
        if row is not None:
            row.stage = stage
            row.detail = detail
            row.file_name = record.file_name
            row.virtual_path = record.virtual_path
            row.updated_at = utc_now_iso()
            await db.commit()
    record.stage = stage
    record.detail = detail
    await _publish(hub, chat_id, record)


async def _publish(hub: EventHub, chat_id: str, record: ObserverEvent) -> None:
    await hub.publish(
        {
            "type": "observer",
            "chat_id": chat_id,
            "event": {
                "id": record.id,
                "chat_id": chat_id,
                "message_id": record.message_id,
                "file_name": record.file_name,
                "virtual_path": record.virtual_path,
                "stage": record.stage,
                "detail": record.detail,
                "created_at": record.created_at,
            },
        }
    )
