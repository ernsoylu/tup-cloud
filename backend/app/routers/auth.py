"""Telegram-OTP authentication.

Flow: identifier (@username / numeric id / known phone) → membership policy
check → bot DMs a one-time code → verify → JWT cookies. A Telegram account may
log in iff it is a member of at least one registered drive chat (or the very
first user bootstrapping the system, or whitelisted via ALLOWED_TELEGRAM_IDS).
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.config import get_settings
from app.deps import AdminUser, CurrentUser, DbSession, RedisDep, TgDep, UserChats
from app.membership import is_whitelisted, member_chat_ids
from app.models import User
from app.security import (
    REFRESH_COOKIE,
    clear_auth_cookies,
    consume_bot_login_code,
    consume_refresh_token,
    generate_login_code,
    mint_access_token,
    mint_refresh_token,
    rate_limit,
    revoke_refresh_token,
    set_auth_cookies,
    store_login_challenge,
    verify_login_challenge,
)
from app.telegram import TupError
from app.vfsutil import utc_now_iso

router = APIRouter(prefix="/api/auth", tags=["auth"])

_PHONE_RE = re.compile(r"^\+?[\d\s\-()]{7,20}$")


class RequestCodeBody(BaseModel):
    identifier: str = Field(min_length=2, max_length=64)


class VerifyBody(BaseModel):
    challenge: str | None = None  # absent → the code came from /login in the bot chat
    code: str = Field(min_length=4, max_length=16)


def _user_payload(user: User, chats: list[str]) -> dict:
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "chats": chats,
    }


async def _resolve_identifier(db: DbSession, tg, identifier: str) -> int | None:
    candidate = identifier.strip()
    if _PHONE_RE.fullmatch(candidate) and not candidate.lstrip("+").isdigit():
        candidate = re.sub(r"[\s\-()]", "", candidate)
    if candidate.startswith("+"):
        # Bots cannot look up accounts by phone; only previously-seen users match.
        normalized = re.sub(r"[\s\-()]", "", candidate)
        row = (
            await db.execute(select(User).where(User.phone.in_((normalized, normalized[1:]))))
        ).scalars().first()
        return row.telegram_id if row else None
    return await tg.resolve_user_id(candidate)


@router.post("/request-code")
async def request_code(
    body: RequestCodeBody, request: Request, db: DbSession, redis: RedisDep, tg: TgDep
) -> dict:
    ip = request.client.host if request.client else "unknown"
    if not await rate_limit(redis, f"code:{ip}", limit=5, window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many code requests; try again later.")

    telegram_id = await _resolve_identifier(db, tg, body.identifier)
    if telegram_id is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Could not resolve that Telegram identity. Use your @username or numeric ID, "
                f"and make sure you have started @{tg.bot_username} in Telegram."
            ),
        )

    total_users = (await db.execute(select(func.count(User.id)))).scalar_one()
    allowed = (
        total_users == 0  # bootstrap: the first person in becomes admin
        or is_whitelisted(body.identifier, telegram_id)
        or bool(await member_chat_ids(db, redis, tg, telegram_id))
    )
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="This Telegram account is not a member of any drive chat the bot is in.",
        )
    existing = (
        await db.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalars().first()
    if existing is not None and not existing.approved:
        raise HTTPException(status_code=403, detail="This account has been blocked by an admin.")

    code = generate_login_code()
    challenge = await store_login_challenge(redis, telegram_id, code)
    try:
        await tg.send_dm(
            telegram_id,
            f"🔐 Your tup-cloud login code: {code}\n"
            f"Valid for {get_settings().login_code_ttl_seconds // 60} minutes. "
            "If you did not request this, ignore this message.",
        )
    except TupError as exc:
        raise HTTPException(
            status_code=400, detail=str(exc) + (f" {exc.hint}" if exc.hint else "")
        ) from exc
    return {"challenge": challenge, "bot": tg.bot_username}


@router.get("/bot")
async def bot_info(tg: TgDep) -> dict:
    """Public: which bot to /start and /login with."""
    return {"bot": tg.bot_username}


@router.post("/verify")
async def verify(
    body: VerifyBody,
    request: Request,
    response: Response,
    db: DbSession,
    redis: RedisDep,
    tg: TgDep,
) -> dict:
    ip = request.client.host if request.client else "unknown"
    if not await rate_limit(redis, f"verify:{ip}", limit=10, window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many attempts; try again later.")
    if body.challenge:
        telegram_id = await verify_login_challenge(redis, body.challenge, body.code)
    else:
        telegram_id = await consume_bot_login_code(redis, body.code)
    if telegram_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired code.")

    user = (
        await db.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalars().first()
    if user is None:
        total_users = (await db.execute(select(func.count(User.id)))).scalar_one()
        username = display = ""
        try:
            entity = await tg.client.get_entity(telegram_id)
            username = entity.username or ""
            display = " ".join(
                filter(None, [getattr(entity, "first_name", ""), getattr(entity, "last_name", "")])
            )
        except Exception:
            pass
        user = User(
            telegram_id=telegram_id,
            username=username,
            display_name=display,
            role="admin" if total_users == 0 else "user",
            approved=True,
        )
        db.add(user)
    if not user.approved:
        raise HTTPException(status_code=403, detail="This account has been blocked by an admin.")
    user.last_login = utc_now_iso()
    await db.commit()
    await db.refresh(user)

    access = mint_access_token(user.id, user.telegram_id, user.role)
    refresh = await mint_refresh_token(redis, user.id)
    set_auth_cookies(response, access, refresh)
    chats = await member_chat_ids(db, redis, tg, user.telegram_id)
    return _user_payload(user, chats)


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: DbSession, redis: RedisDep) -> dict:
    token = request.cookies.get(REFRESH_COOKIE)
    user_id = await consume_refresh_token(redis, token) if token else None
    if user_id is None:
        clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Session expired; log in again.")
    user = await db.get(User, user_id)
    if user is None or not user.approved:
        clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Account not available.")
    access = mint_access_token(user.id, user.telegram_id, user.role)
    new_refresh = await mint_refresh_token(redis, user.id)
    set_auth_cookies(response, access, new_refresh)
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response, redis: RedisDep) -> dict:
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        await revoke_refresh_token(redis, token)
    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me")
async def me(user: CurrentUser, chats: UserChats) -> dict:
    return _user_payload(user, chats)


# --- admin user management ----------------------------------------------------


@router.get("/users")
async def list_users(_: AdminUser, db: DbSession) -> list[dict]:
    users = (await db.execute(select(User).order_by(User.id))).scalars().all()
    return [
        {
            "id": u.id,
            "telegram_id": u.telegram_id,
            "username": u.username,
            "display_name": u.display_name,
            "role": u.role,
            "approved": u.approved,
            "last_login": u.last_login,
        }
        for u in users
    ]


@router.post("/users/{user_id}/toggle-block")
async def toggle_block(user_id: int, admin: AdminUser, db: DbSession) -> dict:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such user")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot block yourself")
    user.approved = not user.approved
    await db.commit()
    return {"id": user.id, "approved": user.approved}
