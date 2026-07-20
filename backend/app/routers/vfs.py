"""VFS operations over the index: listing, mkdir, rm, rmdir, mv, cp — the exact
tup edge rules (path-only mv, empty-only rmdir, same-folder hash dedup,
.keep folder markers)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, HubDep, TgDep, UserChats, assert_chat_access
from app.models import FileVersion, VfsEntry
from app.telegram import TelegramService, TupError, format_caption
from app.transfers import raise_tup
from app.vfsutil import KEEP_FILE, VfsPathError, normalize_vfs_path, split_vfs_path

router = APIRouter(prefix="/api/vfs", tags=["vfs"])

# Deleted files are parked under this hidden prefix. The caption is rewritten
# to the /.Trash/… path too, so the group's messages alone are enough to
# rebuild the database, including what was in the bin.
TRASH_PREFIX = "/.Trash/"


class PathBody(BaseModel):
    path: str


class SrcDestBody(BaseModel):
    src: str  # file path
    dest: str  # destination directory


def _entry_dict(e: VfsEntry) -> dict:
    return {
        "id": e.id,
        "chat_id": e.chat_id,
        "virtual_path": e.virtual_path,
        "file_name": e.file_name,
        "file_size": e.file_size,
        "file_hash": e.file_hash,
        "telegram_message_id": e.telegram_message_id,
        "upload_timestamp": e.upload_timestamp,
        "mime_type": e.mime_type,
        "media_kind": e.media_kind,
        "width": e.width,
        "height": e.height,
        "duration": e.duration,
        "origin": e.origin,
        "uploaded_by": e.uploaded_by,
        "user_caption": e.user_caption,
        "tags": e.tags,
    }


async def _get_file(db: DbSession, chat_id: str, path: str) -> VfsEntry:
    try:
        virtual_path, file_name = split_vfs_path(path)
    except VfsPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    entry = (
        await db.execute(
            select(VfsEntry).where(
                VfsEntry.chat_id == chat_id,
                VfsEntry.virtual_path == virtual_path,
                VfsEntry.file_name == file_name,
            )
        )
    ).scalars().first()
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No such file: {path}")
    return entry


@router.get("/{chat_id}")
async def list_index(chat_id: str, _: CurrentUser, chats: UserChats, db: DbSession) -> list[dict]:
    """The whole drive index, trash included; the frontend derives trees,
    listings, and the Recycle Bin view in memory, exactly like the desktop GUI
    (one query per refresh)."""
    await assert_chat_access(chat_id, chats)
    entries = (
        await db.execute(
            select(VfsEntry)
            .where(VfsEntry.chat_id == chat_id)
            .order_by(VfsEntry.virtual_path, VfsEntry.file_name)
        )
    ).scalars().all()
    return [_entry_dict(e) for e in entries]


async def _purge_entry(db: DbSession, tg: TelegramService, entry: VfsEntry) -> None:
    """Permanently delete a file: its message, all version messages, all rows."""
    versions = (
        await db.execute(select(FileVersion).where(FileVersion.entry_id == entry.id))
    ).scalars().all()
    for version in versions:
        try:
            await tg.delete_message(version.chat_id, version.telegram_message_id)
        except TupError:
            pass
        await db.delete(version)
    if entry.telegram_message_id:
        try:
            await tg.delete_message(entry.chat_id, entry.telegram_message_id)
        except TupError:
            if entry.origin != "observed":
                raise
    await db.delete(entry)


def _trashed_name(file_name: str, taken: set[str]) -> str:
    if file_name not in taken:
        return file_name
    stem, dot, ext = file_name.rpartition(".")
    if not dot:
        stem, ext = file_name, ""
    for n in range(2, 1000):
        candidate = f"{stem} ({n})" + (f".{ext}" if ext else "")
        if candidate not in taken:
            return candidate
    return file_name


@router.get("/{chat_id}/trash")
async def list_trash(chat_id: str, _: CurrentUser, chats: UserChats, db: DbSession) -> list[dict]:
    await assert_chat_access(chat_id, chats)
    entries = (
        await db.execute(
            select(VfsEntry)
            .where(VfsEntry.chat_id == chat_id, VfsEntry.virtual_path.startswith(TRASH_PREFIX))
            .order_by(VfsEntry.upload_timestamp.desc())
        )
    ).scalars().all()
    result = []
    for e in entries:
        item = _entry_dict(e)
        item["original_path"] = "/" + e.virtual_path[len(TRASH_PREFIX) :]
        result.append(item)
    return result


@router.post("/{chat_id}/trash/restore")
async def restore_from_trash(
    chat_id: str,
    body: PathBody,
    _: CurrentUser,
    chats: UserChats,
    db: DbSession,
    tg: TgDep,
    hub: HubDep,
) -> dict:
    await assert_chat_access(chat_id, chats)
    entry = await _get_file(db, chat_id, body.path)
    if not entry.virtual_path.startswith(TRASH_PREFIX):
        raise HTTPException(status_code=400, detail="That file is not in the Recycle Bin")
    original_dir = "/" + entry.virtual_path[len(TRASH_PREFIX) :]
    clash = (
        await db.execute(
            select(VfsEntry).where(
                VfsEntry.chat_id == chat_id,
                VfsEntry.virtual_path == original_dir,
                VfsEntry.file_name == entry.file_name,
            )
        )
    ).scalars().first()
    if clash is not None:
        raise HTTPException(
            status_code=409, detail="A file with that name exists at the original location"
        )
    full_path = (
        original_dir + entry.file_name if original_dir != "/" else "/" + entry.file_name
    )
    if entry.origin != "observed" and entry.telegram_message_id:
        try:
            await tg.edit_caption(
                chat_id,
                entry.telegram_message_id,
                format_caption(full_path, entry.file_hash, entry.user_caption or None),
            )
        except TupError as exc:
            raise_tup(exc)
    entry.virtual_path = original_dir
    await db.commit()
    await hub.publish({"type": "index-changed", "chat_id": chat_id})
    return {"ok": True, "path": full_path}


@router.post("/{chat_id}/trash/empty")
async def empty_trash(
    chat_id: str, _: CurrentUser, chats: UserChats, db: DbSession, tg: TgDep, hub: HubDep
) -> dict:
    """Permanently delete everything in the bin — files and all their versions."""
    await assert_chat_access(chat_id, chats)
    entries = (
        await db.execute(
            select(VfsEntry).where(
                VfsEntry.chat_id == chat_id, VfsEntry.virtual_path.startswith(TRASH_PREFIX)
            )
        )
    ).scalars().all()
    purged = 0
    for entry in entries:
        try:
            await _purge_entry(db, tg, entry)
            purged += 1
        except TupError:
            continue  # leave the row; retry on the next empty
    await db.commit()
    await hub.publish({"type": "index-changed", "chat_id": chat_id})
    return {"ok": True, "purged": purged}


@router.post("/{chat_id}/mkdir")
async def mkdir(
    chat_id: str, body: PathBody, _: CurrentUser, chats: UserChats, db: DbSession, hub: HubDep
) -> dict:
    await assert_chat_access(chat_id, chats)
    try:
        directory = normalize_vfs_path(body.path, directory=True)
    except VfsPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if directory == "/":
        raise HTTPException(status_code=400, detail="Root already exists")
    existing = (
        await db.execute(
            select(VfsEntry).where(
                VfsEntry.chat_id == chat_id,
                VfsEntry.virtual_path == directory,
                VfsEntry.file_name == KEEP_FILE,
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Folder already exists")
    db.add(
        VfsEntry(
            chat_id=chat_id,
            virtual_path=directory,
            file_name=KEEP_FILE,
            file_size=0,
            file_hash="",
            telegram_message_id=0,
        )
    )
    await db.commit()
    await hub.publish({"type": "index-changed", "chat_id": chat_id})
    return {"ok": True, "path": directory}


@router.post("/{chat_id}/rm")
async def rm(
    chat_id: str,
    body: PathBody,
    _: CurrentUser,
    chats: UserChats,
    db: DbSession,
    tg: TgDep,
    hub: HubDep,
) -> dict:
    """Move a file to the Recycle Bin. Files already in the bin are deleted
    permanently, together with their whole version history."""
    await assert_chat_access(chat_id, chats)
    entry = await _get_file(db, chat_id, body.path)

    if entry.virtual_path.startswith(TRASH_PREFIX):
        await _purge_entry(db, tg, entry)
        await db.commit()
        await hub.publish({"type": "index-changed", "chat_id": chat_id})
        return {"ok": True, "purged": True}

    trash_dir = TRASH_PREFIX + entry.virtual_path.lstrip("/")
    taken = {
        e.file_name
        for e in (
            await db.execute(
                select(VfsEntry).where(
                    VfsEntry.chat_id == chat_id, VfsEntry.virtual_path == trash_dir
                )
            )
        ).scalars()
    }
    new_name = _trashed_name(entry.file_name, taken)
    full_path = trash_dir + new_name
    # Rewrite the caption to the /.Trash/… path so the trash state survives in
    # Telegram itself (rebuildable database).
    if entry.origin != "observed" and entry.telegram_message_id:
        try:
            await tg.edit_caption(
                chat_id,
                entry.telegram_message_id,
                format_caption(full_path, entry.file_hash, entry.user_caption or None),
            )
        except TupError as exc:
            raise_tup(exc)
    entry.virtual_path = trash_dir
    entry.file_name = new_name
    await db.commit()
    await hub.publish({"type": "index-changed", "chat_id": chat_id})
    return {"ok": True, "trashed": True}


@router.post("/{chat_id}/rmdir")
async def rmdir(
    chat_id: str, body: PathBody, _: CurrentUser, chats: UserChats, db: DbSession, hub: HubDep
) -> dict:
    await assert_chat_access(chat_id, chats)
    try:
        directory = normalize_vfs_path(body.path, directory=True)
    except VfsPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if directory == "/":
        raise HTTPException(status_code=400, detail="Cannot remove root")
    inside = (
        await db.execute(
            select(VfsEntry).where(
                VfsEntry.chat_id == chat_id, VfsEntry.virtual_path.startswith(directory)
            )
        )
    ).scalars().all()
    real = [e for e in inside if not (e.virtual_path == directory and e.file_name == KEEP_FILE)]
    if real:
        raise HTTPException(status_code=409, detail="Folder is not empty")
    for e in inside:
        await db.delete(e)
    await db.commit()
    await hub.publish({"type": "index-changed", "chat_id": chat_id})
    return {"ok": True}


@router.post("/{chat_id}/mv")
async def mv(
    chat_id: str,
    body: SrcDestBody,
    _: CurrentUser,
    chats: UserChats,
    db: DbSession,
    tg: TgDep,
    hub: HubDep,
) -> dict:
    """Path-only move (Telegram cannot rename files). Caption is re-pointed for
    tup-owned messages; observed messages move in the index only."""
    await assert_chat_access(chat_id, chats)
    entry = await _get_file(db, chat_id, body.src)
    try:
        dest_dir = normalize_vfs_path(body.dest, directory=True)
    except VfsPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if dest_dir == entry.virtual_path:
        return {"ok": True, "unchanged": True}
    clash = (
        await db.execute(
            select(VfsEntry).where(
                VfsEntry.chat_id == chat_id,
                VfsEntry.virtual_path == dest_dir,
                VfsEntry.file_name == entry.file_name,
            )
        )
    ).scalars().first()
    if clash is not None:
        raise HTTPException(status_code=409, detail="Destination already has a file by that name")

    full_path = dest_dir + entry.file_name if dest_dir != "/" else "/" + entry.file_name
    if entry.origin != "observed" and entry.telegram_message_id:
        try:
            await tg.edit_caption(
                chat_id,
                entry.telegram_message_id,
                format_caption(full_path, entry.file_hash, entry.user_caption or None),
            )
        except TupError as exc:
            raise_tup(exc)
    entry.virtual_path = dest_dir
    await db.commit()
    await hub.publish({"type": "index-changed", "chat_id": chat_id})
    return {"ok": True, "path": full_path}


@router.post("/{chat_id}/cp")
async def cp(
    chat_id: str,
    body: SrcDestBody,
    _: CurrentUser,
    chats: UserChats,
    db: DbSession,
    tg: TgDep,
    hub: HubDep,
) -> dict:
    """Server-side copy: Telegram re-sends the media, nothing is re-uploaded."""
    await assert_chat_access(chat_id, chats)
    entry = await _get_file(db, chat_id, body.src)
    try:
        dest_dir = normalize_vfs_path(body.dest, directory=True)
    except VfsPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry.file_hash:
        dupe = (
            await db.execute(
                select(VfsEntry).where(
                    VfsEntry.chat_id == chat_id,
                    VfsEntry.virtual_path == dest_dir,
                    VfsEntry.file_hash == entry.file_hash,
                )
            )
        ).scalars().first()
        if dupe is not None:
            raise HTTPException(
                status_code=409, detail="An identical file already exists in the destination"
            )
    full_path = dest_dir + entry.file_name if dest_dir != "/" else "/" + entry.file_name
    try:
        message_id = await tg.copy_media(
            chat_id,
            entry.telegram_message_id,
            format_caption(full_path, entry.file_hash, entry.user_caption or None),
        )
    except TupError as exc:
        raise_tup(exc)
    db.add(
        VfsEntry(
            chat_id=chat_id,
            virtual_path=dest_dir,
            file_name=entry.file_name,
            file_size=entry.file_size,
            file_hash=entry.file_hash,
            telegram_message_id=message_id,
            mime_type=entry.mime_type,
            media_kind=entry.media_kind,
            width=entry.width,
            height=entry.height,
            duration=entry.duration,
            origin="upload",
            uploaded_by=entry.uploaded_by,
            user_caption=entry.user_caption,
            tags=entry.tags,
        )
    )
    await db.commit()
    await hub.publish({"type": "index-changed", "chat_id": chat_id})
    return {"ok": True, "path": full_path}
