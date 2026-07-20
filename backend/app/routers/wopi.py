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
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import select

from app.db import SessionLocal
from app.membership import member_chat_ids
from app.models import User, VfsEntry
from app.saving import cache_path_for, save_bytes
from app.security import decode_wopi_token, mint_wopi_token
from app.telegram import TupError
from app.config import get_settings

logger = logging.getLogger("tup.wopi")

router = APIRouter(prefix="/wopi", tags=["wopi"])


def _wopi_time(iso: str) -> str:
    """WOPI requires strict ISO 8601 UTC with a 'Z' suffix; our index stores
    '+00:00'-style timestamps. CODE's parser rejects the latter, which breaks
    its modified-time tracking and with it Save As / Export."""
    try:
        stamp = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        stamp = datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _extract_token(request: Request, access_token: str | None) -> str:
    """CODE sends the token as ?access_token=... on most calls, but as an
    'Authorization: Bearer' header on some server-side ones. Accept both."""
    if access_token:
        return access_token
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:]
    return ""


async def _authorized_entry(
    entry_id: int, request: Request, access_token: str | None
) -> tuple[VfsEntry, dict]:
    payload = decode_wopi_token(_extract_token(request, access_token), entry_id)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid WOPI token")
    async with SessionLocal() as db:
        entry = await db.get(VfsEntry, entry_id)
        user = await db.get(User, int(payload["sub"]))
        if entry is None:
            raise HTTPException(status_code=404, detail="File not found")
        if user is None or not user.approved:
            raise HTTPException(status_code=401, detail="Account not available")
        # Re-check drive membership on every WOPI call: the token has a long
        # TTL and is browser-exposed, so a user removed from the drive must
        # lose access to its files. member_chat_ids is Redis-cached (~5 min),
        # so repeat calls during an editing session stay cheap. Admins bypass.
        if user.role != "admin":
            member_chats = await member_chat_ids(
                db,
                request.app.state.redis,
                request.app.state.tg,
                user.telegram_id,
            )
            if entry.chat_id not in member_chats:
                raise HTTPException(status_code=403, detail="No access to this drive")
    return entry, payload


@router.get("/files/{entry_id}")
async def check_file_info(
    entry_id: int, request: Request, access_token: str | None = Query(None)
) -> dict:
    entry, payload = await _authorized_entry(entry_id, request, access_token)
    return {
        "BaseFileName": entry.file_name,
        "Size": entry.file_size,
        "OwnerId": "tup-cloud",
        "UserId": payload["sub"],
        "UserFriendlyName": payload.get("label") or f"user {payload['sub']}",
        "UserCanWrite": True,
        "UserCanNotWriteRelative": False,
        "SupportsUpdate": True,
        "SupportsRename": True,
        "UserCanRename": True,
        "LastModifiedTime": _wopi_time(entry.upload_timestamp),
        "PostMessageOrigin": "*",
    }


@router.get("/files/{entry_id}/contents")
async def get_file_contents(
    entry_id: int, request: Request, access_token: str | None = Query(None)
) -> Response:
    entry, _ = await _authorized_entry(entry_id, request, access_token)
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
    entry_id: int, request: Request, access_token: str | None = Query(None)
) -> dict:
    """PutFile: Collabora saves the document."""
    entry, payload = await _authorized_entry(entry_id, request, access_token)
    data = await request.body()
    tg = request.app.state.tg
    hub = request.app.state.hub
    async with SessionLocal() as db:
        # Re-attach the entry to this session for the save pipeline.
        entry = await db.get(VfsEntry, entry_id)
        try:
            saved, _ = await save_bytes(
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
        modified = saved.upload_timestamp
    # CODE reads LastModifiedTime from the PutFile response; an empty body
    # breaks its save tracking and blocks Save As / Export.
    return {"LastModifiedTime": _wopi_time(modified)}


def _decode_wopi_name(raw: str) -> str:
    name = raw.encode("latin-1", "ignore").decode("utf-7", "ignore").strip()
    # Targets may arrive path-qualified; folders are fixed, keep the base name.
    return name.replace("\\", "/").split("/")[-1].strip()


@router.post("/files/{entry_id}")
async def file_operation(
    entry_id: int, request: Request, access_token: str | None = Query(None)
) -> dict:
    """WOPI file-level operations: RENAME_FILE (title-bar rename) and
    PUT_RELATIVE ('Save As')."""
    override = request.headers.get("X-WOPI-Override")
    if override == "RENAME_FILE":
        return await _rename_file(entry_id, request, access_token)
    if override == "PUT_RELATIVE":
        return await _put_relative_file(entry_id, request, access_token)
    raise HTTPException(status_code=501, detail="Unsupported WOPI operation")


async def _rename_file(entry_id: int, request: Request, access_token: str | None) -> dict:
    """Rename in place: same entry, same Telegram message — only the index row
    and the caption path change (versions and history stay attached)."""
    entry, payload = await _authorized_entry(entry_id, request, access_token)
    requested = _decode_wopi_name(request.headers.get("X-WOPI-RequestedName", ""))
    if not requested:
        raise HTTPException(status_code=400, detail="Missing requested name")
    # WOPI sends the name without extension; the host keeps the extension.
    extension = entry.file_name.rsplit(".", 1)[1] if "." in entry.file_name else ""
    new_name = requested + (f".{extension}" if extension else "")

    tg = request.app.state.tg
    hub = request.app.state.hub
    async with SessionLocal() as db:
        entry = await db.get(VfsEntry, entry_id)
        if new_name == entry.file_name:
            return {"Name": requested}
        clash = (
            await db.execute(
                select(VfsEntry).where(
                    VfsEntry.chat_id == entry.chat_id,
                    VfsEntry.virtual_path == entry.virtual_path,
                    VfsEntry.file_name == new_name,
                )
            )
        ).scalars().first()
        if clash is not None:
            raise HTTPException(status_code=409, detail="A file with that name already exists")

        full_path = (
            entry.virtual_path + new_name if entry.virtual_path != "/" else "/" + new_name
        )
        if entry.origin != "observed" and entry.telegram_message_id:
            try:
                from app.telegram import format_caption

                await tg.edit_caption(
                    entry.chat_id,
                    entry.telegram_message_id,
                    format_caption(full_path, entry.file_hash, entry.user_caption or None),
                )
            except TupError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        cache_path_for(entry).unlink(missing_ok=True)  # cached under the old name
        entry.file_name = new_name
        await db.commit()
    await hub.publish({"type": "index-changed", "chat_id": entry.chat_id})
    logger.info("Renamed entry %s to %s", entry_id, new_name)
    token = mint_wopi_token(entry.id, int(payload["sub"]), payload.get("label", ""))
    wopi_src = f"{get_settings().wopi_base}/wopi/files/{entry.id}"
    return {"Name": requested, "Url": f"{wopi_src}?access_token={token}"}


async def _put_relative_file(
    entry_id: int, request: Request, access_token: str | None
) -> dict:
    """PutRelativeFile: 'Save As' inside Collabora — new file in the same folder."""
    entry, payload = await _authorized_entry(entry_id, request, access_token)
    suggested = request.headers.get("X-WOPI-SuggestedTarget", "")
    relative = request.headers.get("X-WOPI-RelativeTarget", "")
    name = _decode_wopi_name(relative or suggested)
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
