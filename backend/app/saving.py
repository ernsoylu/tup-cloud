"""The generic save-through-server pipeline: bytes → Telegram message →
index row, with version snapshots. Used by the markdown editor, Collabora
(WOPI PutFile / Save As), and blank-document creation — any editor that saves
through the backend gets versioning and the caption protocol for free."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.events import EventHub
from app.models import FileVersion, UploadLog, VfsEntry
from app.telegram import TelegramService, format_caption
from app.vfsutil import utc_now_iso

VERSION_CAP = 20


def cache_path_for(entry: VfsEntry):
    base = get_settings().cache_dir / entry.chat_id / entry.virtual_path.lstrip("/")
    return base / entry.file_name


async def save_bytes(
    db: AsyncSession,
    tg: TelegramService,
    hub: EventHub,
    *,
    chat_id: str,
    virtual_path: str,  # with trailing slash
    file_name: str,
    data: bytes,
    mime: str,
    saved_by: str,
) -> tuple[VfsEntry, bool]:
    """Create or update a file from raw bytes. Returns (entry, changed).
    Replacing an existing file records the old message as a version."""
    file_hash = hashlib.sha256(data).hexdigest()
    full_path = virtual_path + file_name if virtual_path != "/" else "/" + file_name

    existing = (
        await db.execute(
            select(VfsEntry).where(
                VfsEntry.chat_id == chat_id,
                VfsEntry.virtual_path == virtual_path,
                VfsEntry.file_name == file_name,
            )
        )
    ).scalars().first()
    if existing is not None and existing.file_hash == file_hash:
        return existing, False

    settings = get_settings()
    settings.spool_dir.mkdir(parents=True, exist_ok=True)
    # file_name is display data; never let path separators shape the spool path.
    spool = settings.spool_dir / f"{uuid.uuid4().hex}__{file_name.replace('/', '_')}"
    spool.write_bytes(data)
    caption = format_caption(
        full_path, file_hash, (existing.user_caption if existing else "") or None
    )
    try:
        async with tg.transfer_lock:
            message_id = await tg.upload_file(spool, chat_id, caption, "document")
    finally:
        spool.unlink(missing_ok=True)

    if existing is not None:
        if existing.telegram_message_id and existing.origin != "observed":
            db.add(
                FileVersion(
                    entry_id=existing.id,
                    chat_id=existing.chat_id,
                    telegram_message_id=existing.telegram_message_id,
                    file_hash=existing.file_hash,
                    file_size=existing.file_size,
                    saved_by=saved_by,
                )
            )
        existing.file_size = len(data)
        existing.file_hash = file_hash
        existing.telegram_message_id = message_id
        existing.mime_type = mime
        existing.media_kind = "document"
        existing.origin = "upload"
        existing.upload_timestamp = utc_now_iso()  # WOPI LastModifiedTime must advance
        entry = existing
    else:
        entry = VfsEntry(
            chat_id=chat_id,
            virtual_path=virtual_path,
            file_name=file_name,
            file_size=len(data),
            file_hash=file_hash,
            telegram_message_id=message_id,
            mime_type=mime,
            media_kind="document",
            origin="upload",
            uploaded_by=saved_by,
        )
        db.add(entry)
    db.add(
        UploadLog(
            file_path=full_path,
            file_size=len(data),
            chat_id=chat_id,
            upload_type="document",
            status="success",
            telegram_message_id=message_id,
        )
    )
    await db.commit()
    await db.refresh(entry)

    # Cap history: prune the oldest versions beyond the last VERSION_CAP.
    stale = (
        await db.execute(
            select(FileVersion)
            .where(FileVersion.entry_id == entry.id)
            .order_by(FileVersion.id.desc())
            .offset(VERSION_CAP)
        )
    ).scalars().all()
    for version in stale:
        try:
            await tg.delete_message(version.chat_id, version.telegram_message_id)
        except Exception:
            pass
        await db.delete(version)
    if stale:
        await db.commit()

    # Stale cached copies must not shadow the new content.
    cache_path_for(entry).unlink(missing_ok=True)

    await hub.publish({"type": "index-changed", "chat_id": chat_id})
    return entry, True
