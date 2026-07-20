"""WOPI host endpoints for Collabora Online (CODE).

CODE authenticates every call with the access_token we minted for the editing
session; the token is bound to one file (entry id) and one user. Saves flow
through the shared save pipeline, so office documents get version snapshots
exactly like markdown files. PutRelativeFile ("Save As" inside Collabora)
creates the new file in the same VFS folder as the original.
"""

from __future__ import annotations

import logging
import mimetypes

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import select

from app.db import SessionLocal
from app.models import User, VfsEntry
from app.saving import cache_path_for, save_bytes
from app.security import decode_wopi_token, mint_wopi_token
from app.telegram import TupError
from app.config import get_settings

logger = logging.getLogger("tup.wopi")

router = APIRouter(prefix="/wopi", tags=["wopi"])


async def _authorized_entry(entry_id: int, access_token: str) -> tuple[VfsEntry, dict]:
    payload = decode_wopi_token(access_token, entry_id)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid WOPI token")
    async with SessionLocal() as db:
        entry = await db.get(VfsEntry, entry_id)
        user = await db.get(User, int(payload["sub"]))
    if entry is None:
        raise HTTPException(status_code=404, detail="File not found")
    if user is None or not user.approved:
        raise HTTPException(status_code=401, detail="Account not available")
    return entry, payload


@router.get("/files/{entry_id}")
async def check_file_info(entry_id: int, access_token: str = Query(...)) -> dict:
    entry, payload = await _authorized_entry(entry_id, access_token)
    return {
        "BaseFileName": entry.file_name,
        "Size": entry.file_size,
        "OwnerId": "tup-cloud",
        "UserId": payload["sub"],
        "UserFriendlyName": payload.get("label") or f"user {payload['sub']}",
        "UserCanWrite": True,
        "UserCanNotWriteRelative": False,
        "SupportsUpdate": True,
        "SupportsRename": False,
        "LastModifiedTime": entry.upload_timestamp,
        "PostMessageOrigin": "*",
    }


@router.get("/files/{entry_id}/contents")
async def get_file_contents(
    entry_id: int, request: Request, access_token: str = Query(...)
) -> Response:
    entry, _ = await _authorized_entry(entry_id, access_token)
    if entry.file_size > get_settings().office_max_bytes:
        raise HTTPException(status_code=413, detail="File too large for online editing")
    cached = cache_path_for(entry)
    if cached.is_file() and cached.stat().st_size == entry.file_size:
        data = cached.read_bytes()
    else:
        tg = request.app.state.tg
        try:
            data = await tg.download_bytes(entry.chat_id, entry.telegram_message_id)
        except TupError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=data, media_type="application/octet-stream")


@router.post("/files/{entry_id}/contents")
async def put_file_contents(
    entry_id: int, request: Request, access_token: str = Query(...)
) -> dict:
    """PutFile: Collabora saves the document."""
    entry, payload = await _authorized_entry(entry_id, access_token)
    data = await request.body()
    tg = request.app.state.tg
    hub = request.app.state.hub
    async with SessionLocal() as db:
        # Re-attach the entry to this session for the save pipeline.
        entry = await db.get(VfsEntry, entry_id)
        try:
            await save_bytes(
                db,
                tg,
                hub,
                chat_id=entry.chat_id,
                virtual_path=entry.virtual_path,
                file_name=entry.file_name,
                data=data,
                mime=entry.mime_type
                or mimetypes.guess_type(entry.file_name)[0]
                or "application/octet-stream",
                saved_by=payload.get("label") or payload["sub"],
            )
        except TupError as exc:
            logger.error("WOPI save failed for entry %s: %s", entry_id, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {}


@router.post("/files/{entry_id}")
async def put_relative_file(
    entry_id: int, request: Request, access_token: str = Query(...)
) -> dict:
    """PutRelativeFile: 'Save As' inside Collabora — new file in the same folder."""
    if request.headers.get("X-WOPI-Override") != "PUT_RELATIVE":
        raise HTTPException(status_code=501, detail="Unsupported WOPI operation")
    entry, payload = await _authorized_entry(entry_id, access_token)
    suggested = request.headers.get("X-WOPI-SuggestedTarget", "")
    relative = request.headers.get("X-WOPI-RelativeTarget", "")
    name = (relative or suggested).encode("latin-1", "ignore").decode("utf-7", "ignore").strip()
    if name.startswith("."):  # extension-only suggestion: keep the base name
        stem = entry.file_name.rsplit(".", 1)[0]
        name = stem + name
    if not name:
        name = "Untitled " + entry.file_name
    data = await request.body()
    tg = request.app.state.tg
    hub = request.app.state.hub
    async with SessionLocal() as db:
        taken = {
            e.file_name
            for e in (
                await db.execute(
                    select(VfsEntry).where(
                        VfsEntry.chat_id == entry.chat_id,
                        VfsEntry.virtual_path == entry.virtual_path,
                    )
                )
            ).scalars()
        }
        if name in taken and not relative:
            stem, dot, ext = name.rpartition(".")
            for n in range(1, 1000):
                candidate = f"{stem}_{n}.{ext}" if dot else f"{name}_{n}"
                if candidate not in taken:
                    name = candidate
                    break
        try:
            new_entry, _ = await save_bytes(
                db,
                tg,
                hub,
                chat_id=entry.chat_id,
                virtual_path=entry.virtual_path,
                file_name=name,
                data=data,
                mime=mimetypes.guess_type(name)[0] or "application/octet-stream",
                saved_by=payload.get("label") or payload["sub"],
            )
        except TupError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    token = mint_wopi_token(new_entry.id, int(payload["sub"]), payload.get("label", ""))
    wopi_src = f"{get_settings().wopi_base}/wopi/files/{new_entry.id}"
    return {"Name": name, "Url": f"{wopi_src}?access_token={token}"}
