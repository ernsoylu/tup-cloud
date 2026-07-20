"""VFS path normalization, MIME routing, and hashing — ported from tup.utils."""

from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import filetype

MediaKind = Literal["photo", "video", "audio", "document"]

HASH_CHUNK_SIZE = 1024 * 1024
KEEP_FILE = ".keep"

_TOKEN_RE = re.compile(r"\d{5,12}:[A-Za-z0-9_-]{30,64}")


class VfsPathError(ValueError):
    """Raised when a VFS path is malformed or escapes the root."""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def scrub_secrets(text: str) -> str:
    return _TOKEN_RE.sub("[SCRUBBED_TOKEN]", text)


_TAG_RE = re.compile(r"#(\w+)")


def extract_tags(text: str | None) -> str:
    """Space-separated lowercase hashtags found in a caption ('#vfs' excluded)."""
    if not text:
        return ""
    tags = {m.group(1).lower() for m in _TAG_RE.finditer(text)} - {"vfs"}
    return " ".join(sorted(tags))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def detect_mime(path: Path) -> tuple[str, MediaKind]:
    """Magic bytes via filetype first, mimetypes fallback, octet-stream last."""
    mime: str | None = None
    kind = filetype.guess(str(path))
    if kind is not None:
        mime = kind.mime
    if mime is None:
        mime, _ = mimetypes.guess_type(path.name)
    if mime is None:
        return "application/octet-stream", "document"
    return mime, route_for_mime(mime)


def route_for_mime(mime: str) -> MediaKind:
    if mime.startswith("image/") and mime not in ("image/svg+xml", "image/x-icon"):
        return "photo"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "document"


def normalize_vfs_path(path: str, *, directory: bool = False) -> str:
    """Root-relative POSIX path; directories carry a trailing slash, files never."""
    candidate = path.strip()
    if not candidate:
        raise VfsPathError("VFS path must not be empty")
    stack: list[str] = []
    for segment in candidate.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not stack:
                raise VfsPathError(f"VFS path escapes root: {path!r}")
            stack.pop()
        else:
            stack.append(segment)
    if not stack:
        return "/"
    normalized = "/" + "/".join(stack)
    return normalized + "/" if directory else normalized


def split_vfs_path(path: str) -> tuple[str, str]:
    """Split a file path into (virtual_path with trailing slash, file_name)."""
    normalized = normalize_vfs_path(path)
    if normalized == "/":
        raise VfsPathError("Root '/' is a directory, not a file path")
    parent, name = posixpath.split(normalized)
    return (parent if parent.endswith("/") else parent + "/"), name


def extract_media_metadata(path: Path) -> tuple[int | None, int | None, int | None]:
    """(width, height, duration seconds) via hachoir; Nones when unavailable."""
    try:
        from hachoir.metadata import extractMetadata
        from hachoir.parser import createParser

        parser = createParser(str(path))
        if parser is None:
            return None, None, None
        with parser:
            metadata = extractMetadata(parser)
        if metadata is None:
            return None, None, None
        width = int(metadata.get("width")) if metadata.has("width") else None
        height = int(metadata.get("height")) if metadata.has("height") else None
        duration = (
            int(metadata.get("duration").total_seconds()) if metadata.has("duration") else None
        )
        return width, height, duration
    except Exception:
        return None, None, None
