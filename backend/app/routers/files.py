"""File byte access: Range-aware streaming (real-time playback), downloads,
thumbnails, cache warming, and cache stats. Cached files are served from disk;
everything else proxies Telegram via MTProto."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

import urllib.parse
import xml.etree.ElementTree as ET

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, HubDep, TgDep, UserChats, assert_chat_access
from app.models import FileVersion, VfsEntry
from app.saving import save_bytes
from app.security import mint_wopi_token
from app.telegram import TupError, format_caption
from app.vfsutil import VfsPathError, extract_tags, split_vfs_path

router = APIRouter(prefix="/api", tags=["files"])

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def cache_path_for(entry: VfsEntry) -> Path:
    base = get_settings().cache_dir / entry.chat_id / entry.virtual_path.lstrip("/")
    return base / entry.file_name


def thumb_path_for(entry: VfsEntry) -> Path:
    key = entry.file_hash or f"msg{entry.telegram_message_id}"
    return get_settings().cache_dir / "_thumbs" / entry.chat_id / f"{key}.jpg"


async def _load_entry(entry_id: int, db: DbSession, chats: list[str]) -> VfsEntry:
    entry = await db.get(VfsEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such file")
    await assert_chat_access(entry.chat_id, chats)
    return entry


def effective_mime(entry: VfsEntry) -> str:
    """Best-effort content type: indexed value first, else the file extension.
    Legacy/imported rows may carry no MIME — without a real type, browsers
    refuse to play media inline."""
    if entry.mime_type and entry.mime_type != "application/octet-stream":
        return entry.mime_type
    guessed, _ = mimetypes.guess_type(entry.file_name)
    return guessed or entry.mime_type or "application/octet-stream"


def _touch(path: Path) -> None:
    """Refresh mtime so the autocleaner treats recently-served files as fresh."""
    try:
        path.touch()
    except OSError:
        pass


@router.get("/files/{entry_id}/stream")
async def stream(
    entry_id: int, request: Request, _: CurrentUser, chats: UserChats, db: DbSession, tg: TgDep
):
    entry = await _load_entry(entry_id, db, chats)
    cached = cache_path_for(entry)
    media_type = effective_mime(entry)
    if cached.is_file() and cached.stat().st_size == entry.file_size:
        _touch(cached)
        return FileResponse(cached, media_type=media_type, filename=entry.file_name)

    size = entry.file_size
    start, end = 0, size - 1
    range_header = request.headers.get("range")
    is_partial = False
    if range_header:
        match = _RANGE_RE.search(range_header)
        if match:
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = min(int(match.group(2)), size - 1)
            if start > end or start >= size:
                raise HTTPException(status_code=416, detail="Range not satisfiable")
            is_partial = True

    try:
        iterator = tg.stream(entry.chat_id, entry.telegram_message_id, start, end)
    except TupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }
    status = 200
    if is_partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        status = 206
    return StreamingResponse(iterator, status_code=status, media_type=media_type, headers=headers)


@router.get("/files/{entry_id}/download")
async def download(
    entry_id: int, _: CurrentUser, chats: UserChats, db: DbSession, tg: TgDep
):
    entry = await _load_entry(entry_id, db, chats)
    cached = cache_path_for(entry)
    if cached.is_file() and cached.stat().st_size == entry.file_size:
        _touch(cached)
        return FileResponse(
            cached,
            media_type=effective_mime(entry),
            filename=entry.file_name,
            content_disposition_type="attachment",
        )
    iterator = tg.stream(entry.chat_id, entry.telegram_message_id, 0, entry.file_size - 1)
    return StreamingResponse(
        iterator,
        media_type=effective_mime(entry),
        headers={
            "Content-Length": str(entry.file_size),
            "Content-Disposition": f'attachment; filename="{entry.file_name}"',
        },
    )


@router.get("/files/{entry_id}/thumb")
async def thumb(entry_id: int, _: CurrentUser, chats: UserChats, db: DbSession, tg: TgDep):
    entry = await _load_entry(entry_id, db, chats)
    path = thumb_path_for(entry)
    if path.is_file():
        _touch(path)
        return FileResponse(path, media_type="image/jpeg")
    try:
        data = await tg.download_thumb(entry.chat_id, entry.telegram_message_id)
    except TupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not data:
        raise HTTPException(status_code=404, detail="No thumbnail for this file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return Response(content=data, media_type="image/jpeg")


@router.post("/files/{entry_id}/cache")
async def warm_cache(
    entry_id: int, request: Request, _: CurrentUser, chats: UserChats, db: DbSession
) -> dict:
    """Warm the server cache so playback/seeking is disk-fast; runs as a transfer."""
    entry = await _load_entry(entry_id, db, chats)
    cached = cache_path_for(entry)
    if cached.is_file() and cached.stat().st_size == entry.file_size:
        return {"cached": True}
    transfer = request.app.state.transfers.enqueue_cache(entry, cached)
    return transfer.snapshot()


class TextFileBody(BaseModel):
    chat_id: str
    path: str  # full VFS file path, e.g. /notes/todo.md
    content: str = Field(max_length=2_000_000)


@router.post("/files/text")
async def save_text_file(
    body: TextFileBody,
    user: CurrentUser,
    chats: UserChats,
    db: DbSession,
    tg: TgDep,
    hub: HubDep,
) -> dict:
    """Create or update a text/markdown file through the shared save pipeline
    (versioning included). Saving identical content is a no-op."""
    await assert_chat_access(body.chat_id, chats)
    try:
        virtual_path, file_name = split_vfs_path(body.path)
    except VfsPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    lower = file_name.lower()
    if lower.endswith((".md", ".markdown")):
        mime = "text/markdown"
    else:
        mime = mimetypes.guess_type(file_name)[0] or "text/plain"

    try:
        entry, changed = await save_bytes(
            db,
            tg,
            hub,
            chat_id=body.chat_id,
            virtual_path=virtual_path,
            file_name=file_name,
            data=body.content.encode("utf-8"),
            mime=mime,
            saved_by=user.username or str(user.telegram_id),
        )
    except TupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "id": entry.id, "unchanged": not changed}


# --- Collabora Online (CODE) --------------------------------------------------

# Impress cannot import plain text, so presentations are seeded from a minimal
# Flat ODP document (one empty slide) that converts cleanly to pptx.
_BLANK_FODP = b"""<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"
 office:version="1.2" office:mimetype="application/vnd.oasis.opendocument.presentation">
 <office:body>
  <office:presentation>
   <draw:page draw:name="page1"/>
  </office:presentation>
 </office:body>
</office:document>"""

_BLANK_SEEDS = {
    # doc_type → (target extension, seed filename, seed bytes, seed mime)
    "document": ("docx", "blank.txt", b"\n", "text/plain"),
    "spreadsheet": ("xlsx", "blank.csv", b"\n", "text/csv"),
    "presentation": (
        "pptx",
        "blank.fodp",
        _BLANK_FODP,
        "application/vnd.oasis.opendocument.presentation-flat-xml",
    ),
    # Draw can't import an empty SVG; a blank PDF page imports cleanly, so
    # drawings are seeded text → pdf → odg (see create_office_file).
    "drawing": ("odg", "blank.txt", b"\n", "text/plain"),
}


async def _entry_bytes(entry: VfsEntry, tg) -> bytes:
    if entry.file_size > get_settings().office_max_bytes:
        raise HTTPException(status_code=413, detail="File too large for conversion")
    cached = cache_path_for(entry)
    if cached.is_file() and cached.stat().st_size == entry.file_size:
        return cached.read_bytes()
    return await tg.download_bytes(entry.chat_id, entry.telegram_message_id)


async def _convert(data: bytes, source_name: str, source_mime: str, target_format: str) -> bytes:
    """LibreOffice-grade conversion via CODE's convert-to service."""
    url = f"{get_settings().collabora_url}/cool/convert-to/{target_format}"
    async with httpx.AsyncClient(timeout=120.0, verify=False) as client:
        response = await client.post(
            url, files={"data": (source_name, data, source_mime)}
        )
    if response.status_code != 200 or not response.content:
        raise HTTPException(
            status_code=502,
            detail=f"Conversion to {target_format} failed ({response.status_code}).",
        )
    return response.content


async def _collabora_frame_path(extension: str) -> str:
    """Path of CODE's editor page from WOPI discovery (host-relative, since the
    browser reaches CODE through our nginx proxy)."""
    url = f"{get_settings().collabora_url}/hosting/discovery"
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            response = await client.get(url)
        root = ET.fromstring(response.text)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Collabora is not reachable; is the container up?"
        ) from exc
    fallback = None
    for action in root.iter("action"):
        urlsrc = action.get("urlsrc", "")
        if not urlsrc:
            continue
        parsed = urllib.parse.urlparse(urlsrc)
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        if fallback is None:
            fallback = path
        if action.get("ext", "").lower() == extension:
            return path
    if fallback is None:
        raise HTTPException(status_code=502, detail="Collabora discovery returned no editors")
    return fallback


@router.post("/files/{entry_id}/wopi-session")
async def wopi_session(
    entry_id: int, user: CurrentUser, chats: UserChats, db: DbSession
) -> dict:
    """Start a Collabora editing session: returns the host-relative iframe URL."""
    entry = await _load_entry(entry_id, db, chats)
    extension = entry.file_name.rsplit(".", 1)[-1].lower()
    frame_path = await _collabora_frame_path(extension)
    token = mint_wopi_token(
        entry.id, user.id, user.username or user.display_name or str(user.telegram_id)
    )
    wopi_src = urllib.parse.quote(f"{get_settings().wopi_base}/wopi/files/{entry.id}", safe="")
    separator = "&" if "?" in frame_path else "?"
    return {"url": f"{frame_path}{separator}WOPISrc={wopi_src}&access_token={token}"}


class OfficeFileBody(BaseModel):
    chat_id: str
    path: str  # full VFS path including the file name (extension added if missing)
    doc_type: str = Field(pattern="^(document|spreadsheet|presentation|drawing)$")


@router.post("/files/office")
async def create_office_file(
    body: OfficeFileBody,
    user: CurrentUser,
    chats: UserChats,
    db: DbSession,
    tg: TgDep,
    hub: HubDep,
) -> dict:
    """Create a blank office document (docx/xlsx/pptx) via CODE conversion and
    store it through the shared save pipeline."""
    await assert_chat_access(body.chat_id, chats)
    try:
        virtual_path, file_name = split_vfs_path(body.path)
    except VfsPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ext, seed_name, seed_data, seed_mime = _BLANK_SEEDS[body.doc_type]
    if not file_name.lower().endswith(f".{ext}"):
        file_name += f".{ext}"
    if body.doc_type == "drawing":
        pdf = await _convert(seed_data, seed_name, seed_mime, "pdf")
        blank = await _convert(pdf, "blank.pdf", "application/pdf", ext)
    else:
        blank = await _convert(seed_data, seed_name, seed_mime, ext)
    try:
        entry, _ = await save_bytes(
            db,
            tg,
            hub,
            chat_id=body.chat_id,
            virtual_path=virtual_path,
            file_name=file_name,
            data=blank,
            mime=mimetypes.guess_type(file_name)[0] or "application/octet-stream",
            saved_by=user.username or str(user.telegram_id),
        )
    except TupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "id": entry.id, "file_name": entry.file_name}


@router.get("/files/{entry_id}/export")
async def export_file(
    entry_id: int,
    _: CurrentUser,
    chats: UserChats,
    db: DbSession,
    tg: TgDep,
    format: str = "pdf",
):
    """Export/convert a document (pdf, docx, odt, xlsx, …) via CODE."""
    if not format.isalnum() or len(format) > 8:
        raise HTTPException(status_code=400, detail="Invalid target format")
    entry = await _load_entry(entry_id, db, chats)
    try:
        data = await _entry_bytes(entry, tg)
    except TupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    converted = await _convert(data, entry.file_name, effective_mime(entry), format)
    stem = entry.file_name.rsplit(".", 1)[0]
    return Response(
        content=converted,
        media_type=mimetypes.guess_type(f"x.{format}")[0] or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{stem}.{format}"'},
    )


@router.get("/files/{entry_id}/versions")
async def list_versions(
    entry_id: int, _: CurrentUser, chats: UserChats, db: DbSession
) -> list[dict]:
    entry = await _load_entry(entry_id, db, chats)
    versions = (
        await db.execute(
            select(FileVersion)
            .where(FileVersion.entry_id == entry.id)
            .order_by(FileVersion.id.desc())
        )
    ).scalars().all()
    return [
        {
            "id": v.id,
            "file_size": v.file_size,
            "file_hash": v.file_hash,
            "saved_by": v.saved_by,
            "created_at": v.created_at,
        }
        for v in versions
    ]


@router.get("/files/versions/{version_id}/content")
async def version_content(
    version_id: int, _: CurrentUser, chats: UserChats, db: DbSession, tg: TgDep
):
    version = await db.get(FileVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="No such version")
    await assert_chat_access(version.chat_id, chats)
    try:
        data = await tg.download_bytes(version.chat_id, version.telegram_message_id)
    except TupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=data, media_type="text/plain; charset=utf-8")


@router.post("/files/{entry_id}/replace")
async def replace_file(
    entry_id: int,
    file: UploadFile,
    # = None only satisfies param ordering after the required UploadFile;
    # FastAPI still enforces the Depends() (auth/membership are NOT optional).
    user: CurrentUser = None,  # type: ignore[assignment]
    chats: UserChats = None,  # type: ignore[assignment]
    db: DbSession = None,  # type: ignore[assignment]
    tg: TgDep = None,  # type: ignore[assignment]
    hub: HubDep = None,  # type: ignore[assignment]
) -> dict:
    """Replace a file's content with an edited copy (same name and folder).
    Goes through the shared save pipeline, so the old revision becomes a
    version snapshot. Used by editors without a save-back protocol (CAD)."""
    entry = await _load_entry(entry_id, db, chats)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    if len(data) > get_settings().office_max_bytes:
        raise HTTPException(status_code=413, detail="File too large to replace through the editor")
    try:
        saved, changed = await save_bytes(
            db,
            tg,
            hub,
            chat_id=entry.chat_id,
            virtual_path=entry.virtual_path,
            file_name=entry.file_name,
            data=data,
            mime=effective_mime(entry),
            saved_by=user.username or str(user.telegram_id),
        )
    except TupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "id": saved.id, "unchanged": not changed, "file_size": saved.file_size}


class CaptionBody(BaseModel):
    caption: str = Field(max_length=800)


@router.patch("/files/{entry_id}/caption")
async def edit_caption(
    entry_id: int,
    body: CaptionBody,
    _: CurrentUser,
    chats: UserChats,
    db: DbSession,
    tg: TgDep,
    hub: HubDep,
) -> dict:
    """Set the user caption / #tags of a file. tup-owned messages get their
    Telegram caption rewritten; observed messages update the index only."""
    entry = await _load_entry(entry_id, db, chats)
    caption = body.caption.strip()
    if entry.origin != "observed" and entry.telegram_message_id:
        full_path = (
            entry.virtual_path + entry.file_name
            if entry.virtual_path != "/"
            else "/" + entry.file_name
        )
        try:
            await tg.edit_caption(
                entry.chat_id,
                entry.telegram_message_id,
                format_caption(full_path, entry.file_hash, caption or None),
            )
        except TupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    entry.user_caption = caption
    entry.tags = extract_tags(caption)
    await db.commit()
    await hub.publish({"type": "index-changed", "chat_id": entry.chat_id})
    return {"ok": True, "user_caption": entry.user_caption, "tags": entry.tags}


@router.get("/cache/stats")
async def cache_stats(_: CurrentUser) -> dict:
    settings = get_settings()
    total = 0
    count = 0
    if settings.cache_dir.exists():
        for path in settings.cache_dir.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
                count += 1
    return {
        "files": count,
        "bytes": total,
        "ttl_minutes": settings.cache_ttl_minutes,
    }
