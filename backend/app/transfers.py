"""Server-side transfer registry: spooled uploads → MTProto, with live progress
published to the event hub. States mirror the desktop GUI queue:
queued → running → done | failed | skipped."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from app.events import EventHub
from app.models import FailedUpload, UploadLog, VfsEntry
from app.telegram import DuplicateFileError, TelegramService, TupError, format_caption
from app.vfsutil import detect_mime, extract_media_metadata, extract_tags, sha256_file, utc_now_iso

logger = logging.getLogger("tup.transfers")

PROGRESS_STEP = 256 * 1024  # publish at most one event per 256 KiB per transfer


@dataclass
class Transfer:
    id: str
    kind: str  # 'upload' | 'cache'
    chat_id: str
    file_name: str
    dest_dir: str
    size: int
    sent: int = 0
    status: str = "queued"  # queued | running | done | failed | skipped
    error: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    def snapshot(self) -> dict:
        return asdict(self)


class TransferManager:
    def __init__(self, tg: TelegramService, hub: EventHub) -> None:
        self._tg = tg
        self._hub = hub
        self._transfers: dict[str, Transfer] = {}
        self._tasks: set[asyncio.Task] = set()

    def list(self, chat_ids: list[str]) -> list[dict]:
        return [
            t.snapshot()
            for t in sorted(self._transfers.values(), key=lambda t: t.created_at, reverse=True)
            if t.chat_id in chat_ids
        ]

    def clear_finished(self, chat_ids: list[str]) -> None:
        for tid in [
            t.id
            for t in self._transfers.values()
            if t.chat_id in chat_ids and t.status in ("done", "failed", "skipped")
        ]:
            del self._transfers[tid]

    async def _emit(self, transfer: Transfer) -> None:
        await self._hub.publish(
            {"type": "transfer", "chat_id": transfer.chat_id, "transfer": transfer.snapshot()}
        )

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # --- uploads --------------------------------------------------------------

    def enqueue_upload(
        self,
        spool_path: Path,
        chat_id: str,
        dest_dir: str,
        uploaded_by: str,
        user_caption: str = "",
    ) -> Transfer:
        transfer = Transfer(
            id=uuid.uuid4().hex,
            kind="upload",
            chat_id=chat_id,
            file_name=spool_path.name.split("__", 1)[-1],
            dest_dir=dest_dir,
            size=spool_path.stat().st_size,
        )
        self._transfers[transfer.id] = transfer
        self._spawn(self._run_upload(transfer, spool_path, uploaded_by, user_caption))
        return transfer

    async def _run_upload(
        self, transfer: Transfer, spool_path: Path, uploaded_by: str, user_caption: str = ""
    ) -> None:
        await self._emit(transfer)
        virtual_dir = transfer.dest_dir if transfer.dest_dir.endswith("/") else transfer.dest_dir + "/"
        full_path = (virtual_dir + transfer.file_name) if virtual_dir != "/" else "/" + transfer.file_name
        try:
            file_hash = await asyncio.to_thread(sha256_file, spool_path)
            mime, kind = await asyncio.to_thread(detect_mime, spool_path)
            caption = format_caption(full_path, file_hash, user_caption or None)

            async with SessionLocal() as db:
                from sqlalchemy import select

                dupe = await db.execute(
                    select(VfsEntry).where(
                        VfsEntry.chat_id == transfer.chat_id,
                        VfsEntry.virtual_path == virtual_dir,
                        VfsEntry.file_hash == file_hash,
                    )
                )
                if dupe.scalars().first() is not None:
                    raise DuplicateFileError(
                        f"{transfer.file_name} is identical (SHA-256) to an existing file in {virtual_dir}."
                    )

            transfer.status = "running"
            await self._emit(transfer)

            last_reported = 0

            def on_progress(sent: int, _total: int) -> None:
                nonlocal last_reported
                transfer.sent = sent
                if sent - last_reported >= PROGRESS_STEP or sent == transfer.size:
                    last_reported = sent
                    asyncio.get_running_loop().create_task(self._emit(transfer))

            async with self._tg.transfer_lock:
                message_id = await self._tg.upload_file(
                    spool_path, transfer.chat_id, caption, kind, progress=on_progress
                )

            width = height = duration = None
            if kind in ("photo", "video", "audio"):
                width, height, duration = await asyncio.to_thread(
                    extract_media_metadata, spool_path
                )

            async with SessionLocal() as db:
                await _upsert_entry(
                    db,
                    chat_id=transfer.chat_id,
                    virtual_path=virtual_dir,
                    file_name=transfer.file_name,
                    file_size=transfer.size,
                    file_hash=file_hash,
                    telegram_message_id=message_id,
                    mime_type=mime,
                    media_kind=kind,
                    width=width,
                    height=height,
                    duration=duration,
                    origin="upload",
                    uploaded_by=uploaded_by,
                    user_caption=user_caption,
                    tags=extract_tags(user_caption),
                )
                db.add(
                    UploadLog(
                        file_path=full_path,
                        file_size=transfer.size,
                        chat_id=transfer.chat_id,
                        upload_type=kind,
                        status="success",
                        telegram_message_id=message_id,
                    )
                )
                await db.commit()

            transfer.sent = transfer.size
            transfer.status = "done"
            spool_path.unlink(missing_ok=True)
            await self._hub.publish({"type": "index-changed", "chat_id": transfer.chat_id})
        except DuplicateFileError as exc:
            transfer.status = "skipped"
            transfer.error = str(exc)
            spool_path.unlink(missing_ok=True)
        except Exception as exc:
            transfer.status = "failed"
            transfer.error = str(exc)
            logger.exception("Upload failed: %s", transfer.file_name)
            async with SessionLocal() as db:
                db.add(
                    FailedUpload(
                        spool_path=str(spool_path),
                        file_name=transfer.file_name,
                        chat_id=transfer.chat_id,
                        dest_dir=virtual_dir,
                        upload_type="upload",
                        error_message=str(exc)[:2000],
                    )
                )
                db.add(
                    UploadLog(
                        file_path=full_path,
                        file_size=transfer.size,
                        chat_id=transfer.chat_id,
                        upload_type="upload",
                        status="failed",
                        error_message=str(exc)[:2000],
                    )
                )
                await db.commit()
        finally:
            await self._emit(transfer)

    # --- cache warms (downloads to server cache for fast playback) ------------

    def enqueue_cache(self, entry: VfsEntry, dest: Path) -> Transfer:
        transfer = Transfer(
            id=uuid.uuid4().hex,
            kind="cache",
            chat_id=entry.chat_id,
            file_name=entry.file_name,
            dest_dir=entry.virtual_path,
            size=entry.file_size,
        )
        self._transfers[transfer.id] = transfer
        self._spawn(self._run_cache(transfer, entry.telegram_message_id, dest))
        return transfer

    async def _run_cache(self, transfer: Transfer, message_id: int, dest: Path) -> None:
        transfer.status = "running"
        await self._emit(transfer)
        try:
            last_reported = 0

            def on_progress(received: int, _total: int) -> None:
                nonlocal last_reported
                transfer.sent = received
                if received - last_reported >= PROGRESS_STEP:
                    last_reported = received
                    asyncio.get_running_loop().create_task(self._emit(transfer))

            async with self._tg.transfer_lock:
                await self._tg.download_to(
                    transfer.chat_id, message_id, dest, progress=on_progress
                )
            transfer.sent = transfer.size
            transfer.status = "done"
        except Exception as exc:
            transfer.status = "failed"
            transfer.error = str(exc)
            logger.exception("Cache download failed: %s", transfer.file_name)
        finally:
            await self._emit(transfer)


async def _upsert_entry(db, **values) -> None:
    from sqlalchemy.dialects.postgresql import insert

    statement = insert(VfsEntry).values(**values)
    statement = statement.on_conflict_do_update(
        constraint="uq_vfs_path_name",
        set_={
            k: statement.excluded[k]
            for k in values
            if k not in ("chat_id", "virtual_path", "file_name")
        }
        | {"upload_timestamp": utc_now_iso()},
    )
    await db.execute(statement)


def raise_tup(exc: TupError):  # convenience for routers
    from fastapi import HTTPException

    detail = str(exc) + (f" {exc.hint}" if exc.hint else "")
    raise HTTPException(status_code=400, detail=detail)
