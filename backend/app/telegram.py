"""Telethon (MTProto) service: the single transport for uploads, downloads,
streaming, caption edits, deletes, login-code DMs — plus the VFS caption
protocol ported from tup.uploader."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    DocumentAttributeVideo,
    InputPeerChannel,
    InputPeerChat,
    InputPeerUser,
)

from app.config import Settings
from app.vfsutil import MediaKind, extract_media_metadata

logger = logging.getLogger("tup.telegram")

MTPROTO_LIMIT_BYTES = 2 * 1024 * 1024 * 1024  # bot accounts cap at 2 GB
STREAM_CHUNK = 1024 * 1024  # 1 MiB: satisfies MTProto offset/request alignment

_CAPTION_PATH_RE = re.compile(r"📁 `(?P<path>/[^`]*)`")
_CAPTION_HASH_RE = re.compile(r"🔗 SHA256: (?P<hash>[0-9a-f]{64})")


class TupError(RuntimeError):
    """User-facing error; rendered as a clean HTTP error detail, never a traceback."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class DuplicateFileError(TupError):
    """Same SHA-256 already present in the target folder — a skip, not a failure."""


@dataclass(frozen=True)
class CaptionMeta:
    full_path: str
    sha256: str
    user_caption: str | None


def format_caption(full_path: str, sha256: str, user_caption: str | None = None) -> str:
    virtual_dir = full_path.rsplit("/", 1)[0] or "/"
    folder = virtual_dir.rstrip("/").rsplit("/", 1)[-1] or "root"
    folder_tag = re.sub(r"\W+", "_", folder)
    parts = [f"📁 `{full_path}`", f"🔗 SHA256: {sha256}"]
    if user_caption:
        parts.append(f"\n{user_caption}")
    parts.append(f"\n#vfs #{folder_tag}")
    return "\n".join(parts)


def parse_caption(caption: str | None) -> CaptionMeta | None:
    if not caption:
        return None
    path_match = _CAPTION_PATH_RE.search(caption)
    hash_match = _CAPTION_HASH_RE.search(caption)
    if not path_match or not hash_match:
        return None
    tail = caption[hash_match.end() :]
    tag_idx = tail.find("#vfs")
    body = (tail[:tag_idx] if tag_idx != -1 else tail).strip()
    return CaptionMeta(path_match.group("path"), hash_match.group("hash"), body or None)


async def with_retry[T](
    operation: Callable[[], Awaitable[T]], *, max_retries: int, what: str
) -> T:
    """Retry on flood waits and transient network faults with backoff."""
    attempt = 0
    while True:
        try:
            return await operation()
        except FloodWaitError as exc:
            attempt += 1
            if attempt > max_retries:
                raise TupError(f"{what}: rate-limited after {max_retries} retries.") from exc
            logger.warning("Flood wait (%s): sleeping %ds", what, exc.seconds)
            await asyncio.sleep(float(exc.seconds))
        except OSError as exc:
            attempt += 1
            if attempt > max_retries:
                raise TupError(f"{what}: network failure after {max_retries} retries: {exc}") from exc
            await asyncio.sleep(float(2**attempt))


def video_attributes(path: Path) -> list[DocumentAttributeVideo] | None:
    width, height, duration = extract_media_metadata(path)
    if width is None or height is None:
        return None
    return [DocumentAttributeVideo(duration=duration or 0, w=width, h=height, supports_streaming=True)]


class TelegramService:
    """Owns the long-lived Telethon client and a Bot API HTTP client (metadata)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._settings.session_dir.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(
            str(self._settings.session_dir / "tup-cloud"),
            settings.telegram_api_id,
            settings.telegram_api_hash.get_secret_value(),
        )
        self._bot_api = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}",
            timeout=30.0,
        )
        self.bot_username: str = ""
        self.bot_id: int = 0
        # Telegram throttles parallel bot transfers; serialize like the desktop GUI.
        self.transfer_lock = asyncio.Semaphore(1)

    async def start(self) -> None:
        await self.client.start(bot_token=self._settings.telegram_bot_token.get_secret_value())
        me = await self.client.get_me()
        self.bot_username = me.username or ""
        self.bot_id = me.id
        session_file = self._settings.session_dir / "tup-cloud.session"
        if session_file.exists():
            session_file.chmod(0o600)
        logger.info("Telegram connected as @%s", self.bot_username)

    async def stop(self) -> None:
        await self._bot_api.aclose()
        await self.client.disconnect()

    # --- Bot API (metadata) ---------------------------------------------------

    async def bot_api(self, method: str, **params: Any) -> Any:
        response = await self._bot_api.post(f"/{method}", json=params)
        payload = response.json()
        if not payload.get("ok"):
            raise TupError(f"Bot API {method} failed: {payload.get('description', 'unknown error')}")
        return payload["result"]

    async def get_chat_member_status(self, chat_id: str, user_id: int) -> str | None:
        """Membership status in a chat, or None when not a member / not queryable."""
        try:
            result = await self.bot_api("getChatMember", chat_id=chat_id, user_id=user_id)
            return str(result.get("status"))
        except TupError:
            return None

    # --- peers ----------------------------------------------------------------

    async def resolve_peer(self, chat_id: str) -> Any:
        try:
            numeric_id = int(chat_id)
        except ValueError:
            try:
                return await self.client.get_input_entity(chat_id)
            except Exception as exc:
                raise TupError(f"Cannot resolve chat {chat_id!r}: {exc}") from exc
        try:
            return await self.client.get_input_entity(numeric_id)
        except ValueError:
            # Fresh bot sessions have an empty entity cache; bots may address
            # chats they are a member of with access_hash 0.
            if str(numeric_id).startswith("-100"):
                return InputPeerChannel(int(str(numeric_id)[4:]), 0)
            if numeric_id < 0:
                return InputPeerChat(-numeric_id)
            return InputPeerUser(numeric_id, 0)

    async def resolve_user_id(self, identifier: str) -> int | None:
        """@username or numeric id → telegram user id (None when unresolvable)."""
        candidate = identifier.strip().lstrip("@")
        if re.fullmatch(r"\d{5,15}", candidate):
            return int(candidate)
        try:
            entity = await self.client.get_entity(candidate)
            return int(entity.id)
        except Exception:
            return None

    async def chat_info(self, chat_id: str) -> dict[str, Any]:
        """Validate a chat and return its title/type via the Bot API."""
        result = await self.bot_api("getChat", chat_id=chat_id)
        title = result.get("title") or " ".join(
            filter(None, [result.get("first_name"), result.get("last_name")])
        )
        return {"chat_id": str(result["id"]), "title": title, "type": result.get("type", "")}

    # --- messaging ------------------------------------------------------------

    async def send_dm(self, telegram_id: int, text: str) -> None:
        """DM a user. Only works if the user has started the bot (Telegram rule)."""
        try:
            entity = await self.client.get_input_entity(telegram_id)
        except ValueError as exc:
            raise TupError(
                f"Cannot message Telegram user {telegram_id}.",
                hint=f"Open @{self.bot_username} in Telegram, press Start, then retry.",
            ) from exc
        await self.client.send_message(entity, text)

    # --- transport ------------------------------------------------------------

    async def upload_file(
        self,
        local_path: Path,
        chat_id: str,
        caption: str,
        kind: MediaKind,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> int:
        size = local_path.stat().st_size
        if size > MTPROTO_LIMIT_BYTES:
            raise TupError(f"{local_path.name} exceeds Telegram's 2 GB bot upload cap.")
        attributes = video_attributes(local_path) if kind == "video" else None

        async def _send() -> int:
            peer = await self.resolve_peer(chat_id)
            message = await self.client.send_file(
                peer,
                str(local_path),
                caption=caption,
                parse_mode=None,  # keep the caption protocol block raw
                force_document=(kind == "document"),
                supports_streaming=(kind == "video"),
                attributes=attributes,
                progress_callback=progress,
            )
            return int(message.id)

        return await with_retry(
            _send, max_retries=self._settings.max_retries, what=f"upload {local_path.name}"
        )

    async def get_media_message(self, chat_id: str, message_id: int) -> Any:
        peer = await self.resolve_peer(chat_id)
        message = await self.client.get_messages(peer, ids=message_id)
        if message is None or message.media is None:
            raise TupError(f"Message {message_id} has no media on Telegram.")
        return message

    async def download_to(
        self,
        chat_id: str,
        message_id: int,
        dest: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Download to `dest` via a .part rename so partial files never look done."""

        async def _download() -> Path:
            message = await self.get_media_message(chat_id, message_id)
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.name + ".part")
            result = await self.client.download_media(
                message, file=str(part), progress_callback=progress
            )
            if result is None:
                raise TupError(f"Download of message {message_id} produced no file.")
            Path(result).replace(dest)
            return dest

        return await with_retry(
            _download, max_retries=self._settings.max_retries, what=f"download {dest.name}"
        )

    async def stream(
        self, chat_id: str, message_id: int, start: int, end: int
    ) -> AsyncIterator[bytes]:
        """Yield bytes [start, end] of a message's media, chunk-aligned for MTProto."""
        message = await self.get_media_message(chat_id, message_id)
        aligned = (start // STREAM_CHUNK) * STREAM_CHUNK
        skip = start - aligned
        remaining = end - start + 1
        async for chunk in self.client.iter_download(
            message.media, offset=aligned, request_size=STREAM_CHUNK
        ):
            if skip:
                if len(chunk) <= skip:  # short chunk (tiny file): keep skipping
                    skip -= len(chunk)
                    continue
                chunk = chunk[skip:]
                skip = 0
            if len(chunk) >= remaining:
                yield bytes(chunk[:remaining])
                return
            remaining -= len(chunk)
            yield bytes(chunk)

    async def download_bytes(self, chat_id: str, message_id: int) -> bytes:
        """Full media download into memory — only for small files (text/markdown)."""
        message = await self.get_media_message(chat_id, message_id)
        result = await self.client.download_media(message, file=bytes)
        if not isinstance(result, bytes):
            raise TupError(f"Message {message_id} produced no content.")
        return result

    async def download_thumb(self, chat_id: str, message_id: int) -> bytes | None:
        message = await self.get_media_message(chat_id, message_id)
        result = await self.client.download_media(message, file=bytes, thumb=-1)
        return result if isinstance(result, bytes) else None

    async def copy_media(self, chat_id: str, message_id: int, caption: str) -> int:
        """Server-side duplication: re-send existing media, no re-upload."""

        async def _copy() -> int:
            peer = await self.resolve_peer(chat_id)
            source = await self.client.get_messages(peer, ids=message_id)
            if source is None or source.media is None:
                raise TupError(f"Source message {message_id} has no media on Telegram.")
            message = await self.client.send_file(peer, source.media, caption=caption, parse_mode=None)
            return int(message.id)

        return await with_retry(_copy, max_retries=self._settings.max_retries, what="copy")

    async def edit_caption(self, chat_id: str, message_id: int, caption: str) -> None:
        async def _edit() -> None:
            peer = await self.resolve_peer(chat_id)
            try:
                await self.client.edit_message(peer, message_id, caption, parse_mode=None)
            except Exception as exc:
                if "not modified" in str(exc).lower():
                    return
                raise

        await with_retry(_edit, max_retries=self._settings.max_retries, what="edit caption")

    async def delete_message(self, chat_id: str, message_id: int) -> None:
        async def _delete() -> None:
            peer = await self.resolve_peer(chat_id)
            await self.client.delete_messages(peer, [message_id])

        await with_retry(_delete, max_retries=self._settings.max_retries, what="delete message")
