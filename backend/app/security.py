"""JWT access tokens, Redis-backed rotating refresh tokens, cookie helpers,
and one-time login codes (hashed at rest)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Response
from redis.asyncio import Redis

from app.config import get_settings

ACCESS_COOKIE = "tup_access"
REFRESH_COOKIE = "tup_refresh"
_REFRESH_PREFIX = "auth:refresh:"
_CODE_PREFIX = "auth:code:"
_RATE_PREFIX = "auth:rate:"


def _secret() -> str:
    return get_settings().jwt_secret.get_secret_value()


def mint_access_token(user_id: int, telegram_id: int, role: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tg": telegram_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


async def mint_refresh_token(redis: Redis, user_id: int) -> str:
    token = secrets.token_urlsafe(48)
    ttl = timedelta(days=get_settings().refresh_token_days)
    await redis.set(_REFRESH_PREFIX + token, str(user_id), ex=ttl)
    return token


async def consume_refresh_token(redis: Redis, token: str) -> int | None:
    """Rotating refresh: the token is retired, but with a 30 s grace TTL so a
    second tab racing the same refresh doesn't get logged out."""
    key = _REFRESH_PREFIX + token
    user_id = await redis.get(key)
    if user_id is None:
        return None
    await redis.expire(key, 30)
    return int(user_id)


async def revoke_refresh_token(redis: Redis, token: str) -> None:
    await redis.delete(_REFRESH_PREFIX + token)


def set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    settings = get_settings()
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=settings.access_token_minutes * 60,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        samesite="lax",
        path="/api/auth",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")


# --- one-time login codes -----------------------------------------------------


def _hash_code(code: str) -> str:
    return hmac.new(_secret().encode(), code.encode(), hashlib.sha256).hexdigest()


def generate_login_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def store_login_challenge(redis: Redis, telegram_id: int, code: str) -> str:
    """Create an opaque challenge id bound to (telegram_id, hashed code)."""
    challenge = secrets.token_urlsafe(24)
    ttl = get_settings().login_code_ttl_seconds
    key = _CODE_PREFIX + challenge
    await redis.hset(key, mapping={"tg": telegram_id, "hash": _hash_code(code), "left": 5})
    await redis.expire(key, ttl)
    return challenge


async def verify_login_challenge(redis: Redis, challenge: str, code: str) -> int | None:
    """Returns the telegram_id on success; decrements attempts, burns on success."""
    key = _CODE_PREFIX + challenge
    data = await redis.hgetall(key)
    if not data:
        return None
    left = int(data.get(b"left", b"0"))
    if left <= 0:
        await redis.delete(key)
        return None
    if not hmac.compare_digest(data[b"hash"].decode(), _hash_code(code)):
        await redis.hincrby(key, "left", -1)
        return None
    await redis.delete(key)
    return int(data[b"tg"])


# --- WOPI access tokens (Collabora Online sessions) ---------------------------


def mint_wopi_token(entry_id: int, user_id: int, user_label: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "purpose": "wopi",
        "entry": entry_id,
        "sub": str(user_id),
        "label": user_label,
        "iat": now,
        "exp": now + timedelta(hours=settings.wopi_token_hours),
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_wopi_token(token: str, entry_id: int) -> dict[str, Any] | None:
    payload = decode_access_token(token)
    if payload is None or payload.get("purpose") != "wopi" or payload.get("entry") != entry_id:
        return None
    return payload


# --- bot-initiated login codes (/login DM to the bot) -------------------------

_BOT_CODE_PREFIX = "auth:botcode:"
# No 0/O/1/I — users retype these by hand from the Telegram chat.
_BOT_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def generate_bot_login_code() -> str:
    return "".join(secrets.choice(_BOT_CODE_ALPHABET) for _ in range(8))


async def store_bot_login_code(redis: Redis, telegram_id: int, code: str) -> None:
    """Bot-issued codes carry the identity themselves, so they are stored keyed
    by their own hash (single-use, TTL-bound) rather than by a challenge id."""
    await redis.set(
        _BOT_CODE_PREFIX + _hash_code(code),
        str(telegram_id),
        ex=get_settings().login_code_ttl_seconds,
    )


async def consume_bot_login_code(redis: Redis, code: str) -> int | None:
    normalized = code.strip().upper().replace("-", "").replace(" ", "")
    if not normalized or len(normalized) > 16:
        return None
    value = await redis.getdel(_BOT_CODE_PREFIX + _hash_code(normalized))
    return int(value) if value is not None else None


async def rate_limit(redis: Redis, bucket: str, limit: int, window_seconds: int) -> bool:
    """Fixed-window counter; True when the call is allowed."""
    key = _RATE_PREFIX + bucket
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    return count <= limit
