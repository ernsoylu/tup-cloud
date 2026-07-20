"""Multipart upload intake (spool → transfer queue) plus transfer/failed
registry endpoints."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, UserChats, assert_chat_access
from app.models import FailedUpload, UploadLog
from app.vfsutil import VfsPathError, normalize_vfs_path

router = APIRouter(prefix="/api", tags=["uploads"])

SPOOL_CHUNK = 1024 * 1024


def _manager(request: Request):
    return request.app.state.transfers


@router.post("/uploads")
async def upload(
    request: Request,
    file: UploadFile,
    chat_id: str = Form(...),
    dest: str = Form("/"),
    caption: str = Form(""),
    user: CurrentUser = None,  # type: ignore[assignment]
    chats: UserChats = None,  # type: ignore[assignment]
) -> dict:
    await assert_chat_access(chat_id, chats)
    try:
        dest_dir = normalize_vfs_path(dest, directory=True)
    except VfsPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    name = Path(file.filename or "unnamed").name
    settings = get_settings()
    settings.spool_dir.mkdir(parents=True, exist_ok=True)
    spool_path = settings.spool_dir / f"{uuid.uuid4().hex}__{name}"
    with spool_path.open("wb") as out:
        while chunk := await file.read(SPOOL_CHUNK):
            out.write(chunk)

    transfer = _manager(request).enqueue_upload(
        spool_path,
        chat_id,
        dest_dir,
        uploaded_by=user.username or str(user.telegram_id),
        user_caption=caption.strip(),
    )
    return transfer.snapshot()


@router.get("/transfers")
async def list_transfers(request: Request, _: CurrentUser, chats: UserChats) -> list[dict]:
    return _manager(request).list(chats)


@router.post("/transfers/clear-finished")
async def clear_finished(request: Request, _: CurrentUser, chats: UserChats) -> dict:
    _manager(request).clear_finished(chats)
    return {"ok": True}


@router.get("/failed")
async def list_failed(_: CurrentUser, chats: UserChats, db: DbSession) -> list[dict]:
    rows = (
        await db.execute(
            select(FailedUpload)
            .where(FailedUpload.status == "pending", FailedUpload.chat_id.in_(chats or ["-"]))
            .order_by(FailedUpload.id)
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "file_name": r.file_name,
            "chat_id": r.chat_id,
            "dest_dir": r.dest_dir,
            "error_message": r.error_message,
            "retry_count": r.retry_count,
            "timestamp": r.timestamp,
        }
        for r in rows
    ]


@router.post("/failed/{failed_id}/retry")
async def retry_failed(
    failed_id: int, request: Request, user: CurrentUser, chats: UserChats, db: DbSession
) -> dict:
    row = await db.get(FailedUpload, failed_id)
    if row is None or row.status != "pending":
        raise HTTPException(status_code=404, detail="No such pending failure")
    await assert_chat_access(row.chat_id, chats)
    spool = Path(row.spool_path)
    if not spool.exists():
        row.status = "abandoned"
        await db.commit()
        raise HTTPException(status_code=410, detail="Spooled file no longer exists")
    row.status = "resolved"
    row.retry_count += 1
    await db.commit()
    transfer = _manager(request).enqueue_upload(
        spool, row.chat_id, row.dest_dir, uploaded_by=user.username or str(user.telegram_id)
    )
    return transfer.snapshot()


@router.post("/failed/{failed_id}/abandon")
async def abandon_failed(failed_id: int, _: CurrentUser, chats: UserChats, db: DbSession) -> dict:
    row = await db.get(FailedUpload, failed_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such failure")
    await assert_chat_access(row.chat_id, chats)
    row.status = "abandoned"
    Path(row.spool_path).unlink(missing_ok=True)
    await db.commit()
    return {"ok": True}


@router.get("/logs")
async def recent_logs(_: CurrentUser, chats: UserChats, db: DbSession, limit: int = 50) -> list[dict]:
    rows = (
        await db.execute(
            select(UploadLog)
            .where(UploadLog.chat_id.in_(chats or ["-"]))
            .order_by(UploadLog.id.desc())
            .limit(min(limit, 200))
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp,
            "file_path": r.file_path,
            "file_size": r.file_size,
            "chat_id": r.chat_id,
            "upload_type": r.upload_type,
            "status": r.status,
            "error_message": r.error_message,
        }
        for r in rows
    ]
