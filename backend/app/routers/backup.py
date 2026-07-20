"""Backup administration: configure the target drive and period, run backups,
list what exists, and restore. Admin-only — a restore replaces the database."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import backup
from app.deps import AdminUser, DbSession, RedisDep, TgDep
from app.models import VfsEntry
from app.telegram import TupError

router = APIRouter(prefix="/api/backup", tags=["backup"])


class BackupConfigBody(BaseModel):
    enabled: bool
    chat_id: str | None = None
    period_hours: int = Field(24, ge=1, le=24 * 30)
    keep_last: int = Field(10, ge=1, le=100)


@router.get("/config")
async def get_config(_: AdminUser, db: DbSession) -> dict:
    return await backup.get_config(db)


@router.put("/config")
async def set_config(body: BackupConfigBody, _: AdminUser, db: DbSession) -> dict:
    if body.enabled and not body.chat_id:
        raise HTTPException(status_code=400, detail="Pick a drive to store backups in first")
    config = await backup.get_config(db)
    config.update(
        {
            "enabled": body.enabled,
            "chat_id": body.chat_id,
            "period_hours": body.period_hours,
            "keep_last": body.keep_last,
        }
    )
    await backup.set_config(db, config)
    return config


@router.post("/run")
async def run_now(request: Request, _: AdminUser, tg: TgDep) -> dict:
    try:
        return await backup.run_backup(tg, request.app.state.hub)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/list")
async def list_backups(_: AdminUser, db: DbSession) -> list[dict]:
    config = await backup.get_config(db)
    if not config.get("chat_id"):
        return []
    rows = (
        await db.execute(
            select(VfsEntry)
            .where(
                VfsEntry.chat_id == str(config["chat_id"]),
                VfsEntry.virtual_path == backup.BACKUP_DIR,
                VfsEntry.file_name.startswith(backup.BACKUP_PREFIX),
            )
            .order_by(VfsEntry.file_name.desc())
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "file_name": r.file_name,
            "file_size": r.file_size,
            "created_at": r.upload_timestamp,
        }
        for r in rows
    ]


class RestoreBody(BaseModel):
    entry_id: int


@router.post("/restore")
async def restore(
    body: RestoreBody, request: Request, _: AdminUser, tg: TgDep, redis: RedisDep
) -> dict:
    try:
        return await backup.restore_database(tg, request.app.state.hub, redis, body.entry_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
