"""Database backup & restore, stored where everything else lives: Telegram.

A backup is a gzipped JSON dump of every table, uploaded to the configured
drive chat as an ordinary VFS file under /Backups/ — indexed, browsable, and
recoverable from the group alone. A scheduler runs backups on the configured
period; retention keeps the newest N.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.events import EventHub
from app.models import (
    ChatAlias,
    FailedUpload,
    FileVersion,
    ObserverEvent,
    Setting,
    UploadLog,
    User,
    VfsEntry,
)
from app.saving import save_bytes
from app.telegram import TelegramService, TupError
from app.vfsutil import utc_now_iso

logger = logging.getLogger("tup.backup")

BACKUP_DIR = "/Backups/"
BACKUP_PREFIX = "tup-cloud-backup-"
FORMAT_NAME = "tup-cloud-backup"
FORMAT_VERSION = 1
CONFIG_KEY = "backup"

# Restore order matters: vfs_index before file_versions (FK), users/aliases first.
_TABLES = [
    ("users", User),
    ("chat_aliases", ChatAlias),
    ("vfs_index", VfsEntry),
    ("file_versions", FileVersion),
    ("uploads_log", UploadLog),
    ("failed_registry", FailedUpload),
    ("observer_events", ObserverEvent),
]

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "chat_id": None,
    "period_hours": 24,
    "keep_last": 10,
    "last_backup_at": "",
}


async def get_config(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(Setting, CONFIG_KEY)
    config = dict(DEFAULT_CONFIG)
    if row is not None and row.value:
        try:
            config.update(json.loads(row.value))
        except ValueError:
            pass
    return config


async def set_config(db: AsyncSession, config: dict[str, Any]) -> None:
    row = await db.get(Setting, CONFIG_KEY)
    if row is None:
        row = Setting(key=CONFIG_KEY)
        db.add(row)
    row.value = json.dumps(config)
    await db.commit()


def _row_dict(obj: Any) -> dict[str, Any]:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


async def dump_database(db: AsyncSession) -> bytes:
    tables: dict[str, list[dict[str, Any]]] = {}
    for name, model in _TABLES:
        rows = (await db.execute(select(model))).scalars().all()
        tables[name] = [_row_dict(r) for r in rows]
    payload = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "created_at": utc_now_iso(),
        "tables": tables,
    }
    return gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


async def run_backup(tg: TelegramService, hub: EventHub) -> dict[str, Any]:
    """Dump the database and upload it to the configured chat. Returns the
    created file info. Raises TupError/ValueError on misconfiguration."""
    async with SessionLocal() as db:
        config = await get_config(db)
        chat_id = config.get("chat_id")
        if not chat_id:
            raise ValueError("No backup drive configured")
        data = await dump_database(db)

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    file_name = f"{BACKUP_PREFIX}{stamp}.json.gz"
    async with SessionLocal() as db:
        entry, _ = await save_bytes(
            db,
            tg,
            hub,
            chat_id=str(chat_id),
            virtual_path=BACKUP_DIR,
            file_name=file_name,
            data=data,
            mime="application/gzip",
            saved_by="backup",
        )
        entry_id, size = entry.id, entry.file_size

        config["last_backup_at"] = utc_now_iso()
        await set_config(db, config)

        # Retention: purge the oldest beyond keep_last (names sort by time).
        keep = max(1, int(config.get("keep_last", 10)))
        backups = (
            await db.execute(
                select(VfsEntry)
                .where(
                    VfsEntry.chat_id == str(chat_id),
                    VfsEntry.virtual_path == BACKUP_DIR,
                    VfsEntry.file_name.startswith(BACKUP_PREFIX),
                )
                .order_by(VfsEntry.file_name.desc())
            )
        ).scalars().all()
        for stale in backups[keep:]:
            try:
                await tg.delete_message(stale.chat_id, stale.telegram_message_id)
            except TupError:
                pass
            await db.delete(stale)
        await db.commit()

    logger.info("Backup %s uploaded to chat %s (%d bytes)", file_name, chat_id, size)
    return {"id": entry_id, "file_name": file_name, "file_size": size}


async def restore_database(
    tg: TelegramService, hub: EventHub, redis, entry_id: int
) -> dict[str, Any]:
    """Replace the entire database with a backup's contents.

    Preserved across the restore: the current backup configuration and the
    index rows of existing backup files (so newer backups stay reachable)."""
    async with SessionLocal() as db:
        entry = await db.get(VfsEntry, entry_id)
        if entry is None or not entry.file_name.startswith(BACKUP_PREFIX):
            raise ValueError("That file is not a tup-cloud backup")
        raw = await tg.download_bytes(entry.chat_id, entry.telegram_message_id)

    payload = json.loads(gzip.decompress(raw))
    if payload.get("format") != FORMAT_NAME or payload.get("version") != FORMAT_VERSION:
        raise ValueError("Unrecognized backup format")
    tables = payload["tables"]

    async with SessionLocal() as db:
        config = await get_config(db)
        preserved_backups = [
            _row_dict(e)
            for e in (
                await db.execute(
                    select(VfsEntry).where(
                        VfsEntry.virtual_path == BACKUP_DIR,
                        VfsEntry.file_name.startswith(BACKUP_PREFIX),
                    )
                )
            ).scalars()
        ]

        # Full replace in one transaction; children before parents on delete.
        for name, model in reversed(_TABLES):
            await db.execute(delete(model))
        counts = {}
        for name, model in _TABLES:
            rows = tables.get(name, [])
            for row in rows:
                db.add(model(**row))
            # Flush per table: children (file_versions) must not reach the DB
            # before their parents (vfs_index).
            await db.flush()
            counts[name] = len(rows)
        # Re-attach current backup files that the dump predates (ids reassigned).
        restored_keys = {
            (r["chat_id"], r["virtual_path"], r["file_name"])
            for r in tables.get("vfs_index", [])
        }
        for row in preserved_backups:
            key = (row["chat_id"], row["virtual_path"], row["file_name"])
            if key not in restored_keys:
                row.pop("id", None)
                db.add(VfsEntry(**row))
        # Serial sequences must move past the restored explicit ids.
        for name, model in _TABLES:
            pk = list(model.__table__.primary_key.columns.keys())[0]
            if pk == "id":
                await db.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {name}), 1))"
                    )
                )
        await set_config(db, config)
        await db.commit()

    from app.membership import invalidate_membership_cache

    await invalidate_membership_cache(redis)
    await hub.publish({"type": "drives-changed"})
    for chat in {r["chat_id"] for r in tables.get("vfs_index", [])}:
        await hub.publish({"type": "index-changed", "chat_id": chat})
    logger.info("Restored backup entry %s: %s", entry_id, counts)
    return {"restored": counts, "created_at": payload.get("created_at")}


async def backup_loop(tg: TelegramService, hub: EventHub) -> None:
    """Scheduler: runs a backup whenever the configured period has elapsed."""
    while True:
        try:
            async with SessionLocal() as db:
                config = await get_config(db)
            if config.get("enabled") and config.get("chat_id"):
                period_hours = max(1, int(config.get("period_hours", 24)))
                last = config.get("last_backup_at") or ""
                due = True
                if last:
                    try:
                        elapsed = datetime.now(UTC) - datetime.fromisoformat(last)
                        due = elapsed.total_seconds() >= period_hours * 3600
                    except ValueError:
                        pass
                if due:
                    await run_backup(tg, hub)
        except Exception:
            logger.exception("Scheduled backup failed")
        await asyncio.sleep(60)
