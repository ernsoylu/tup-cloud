"""ORM models — the tup registry schema translated to PostgreSQL, plus users
and observer events for the cloud version."""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.vfsutil import utc_now_iso


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    display_name: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    role: Mapped[str] = mapped_column(String(16), default="user")  # 'admin' | 'user'
    approved: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    last_login: Mapped[str] = mapped_column(String(40), default="")
    default_chat_id: Mapped[str] = mapped_column(String(32), default="")  # landing drive


class ChatAlias(Base):
    __tablename__ = "chat_aliases"

    alias: Mapped[str] = mapped_column(String(64), primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class VfsEntry(Base):
    __tablename__ = "vfs_index"
    __table_args__ = (
        UniqueConstraint("chat_id", "virtual_path", "file_name", name="uq_vfs_path_name"),
        Index("idx_vfs_path", "chat_id", "virtual_path"),
        Index("idx_vfs_hash", "chat_id", "file_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(32))
    virtual_path: Mapped[str] = mapped_column(Text)  # always ends with '/'
    file_name: Mapped[str] = mapped_column(Text)
    file_size: Mapped[int] = mapped_column(BigInteger)
    file_hash: Mapped[str] = mapped_column(String(64))
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    upload_timestamp: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    mime_type: Mapped[str] = mapped_column(Text, default="")
    media_kind: Mapped[str] = mapped_column(String(16), default="")  # document|photo|video|audio
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin: Mapped[str] = mapped_column(String(16), default="upload")  # 'upload' | 'observed'
    uploaded_by: Mapped[str] = mapped_column(String(320), default="")
    user_caption: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="")  # space-separated, no '#'


class FileVersion(Base):
    """Superseded revisions of a file edited through tup-cloud (markdown today,
    any save-through-server pipeline tomorrow — e.g. Collabora Online). Each
    version is the old Telegram message that a save replaced; the current
    revision always lives in vfs_index."""

    __tablename__ = "file_versions"
    __table_args__ = (Index("idx_versions_entry", "entry_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("vfs_index.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[str] = mapped_column(String(32))
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    file_hash: Mapped[str] = mapped_column(String(64))
    file_size: Mapped[int] = mapped_column(BigInteger)
    saved_by: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class UploadLog(Base):
    __tablename__ = "uploads_log"
    __table_args__ = (Index("idx_logs_chat_time", "chat_id", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    file_path: Mapped[str] = mapped_column(Text)
    file_size: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[str] = mapped_column(String(32))
    upload_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))  # 'success' | 'failed'
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class FailedUpload(Base):
    __tablename__ = "failed_registry"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    spool_path: Mapped[str] = mapped_column(Text)  # retained temp file for retry
    file_name: Mapped[str] = mapped_column(Text)
    chat_id: Mapped[str] = mapped_column(String(32))
    dest_dir: Mapped[str] = mapped_column(Text)
    upload_type: Mapped[str] = mapped_column(String(16))
    error_message: Mapped[str] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|resolved|abandoned


class Setting(Base):
    """Small key-value store for runtime configuration (e.g. backup settings)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")  # JSON


class ObserverEvent(Base):
    __tablename__ = "observer_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(32))
    message_id: Mapped[int] = mapped_column(BigInteger)
    file_name: Mapped[str] = mapped_column(Text)
    virtual_path: Mapped[str] = mapped_column(Text, default="/Other/")
    stage: Mapped[str] = mapped_column(String(16))  # detected|analyzing|indexed|skipped|failed
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
