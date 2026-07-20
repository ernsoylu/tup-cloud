"""tup-cloud backend: app factory, lifespan wiring, and the WebSocket hub."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import select

from app import observer
from app.cleaner import run_cleaner
from app.config import get_settings
from app.db import SessionLocal, engine, init_db
from app.events import EventHub, forward_events
from app.membership import member_chat_ids
from app.models import ChatAlias, User
from app.routers import auth, drives, files, observer as observer_router, uploads, vfs, wopi
from app.security import ACCESS_COOKIE, decode_access_token
from app.telegram import TelegramService, TupError
from app.transfers import TransferManager
from app.vfsutil import scrub_secrets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("tup.main")


class _ScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Collapse args first so tokens embedded in interpolated URLs are caught.
        record.msg = scrub_secrets(record.getMessage())
        record.args = ()
        return True


# Handler-level filters see every record that reaches the root handler,
# including those from child loggers like httpx/telethon.
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_ScrubFilter())
logging.getLogger("httpx").setLevel(logging.WARNING)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    for directory in (settings.session_dir, settings.cache_dir, settings.spool_dir):
        directory.mkdir(parents=True, exist_ok=True)

    await init_db()
    redis = Redis.from_url(settings.redis_url)
    tg = TelegramService(settings)
    await tg.start()
    hub = EventHub(redis)

    app.state.redis = redis
    app.state.tg = tg
    app.state.hub = hub
    app.state.transfers = TransferManager(tg, hub)

    await _bootstrap_default_drive(tg)
    if settings.observer_enabled:
        observer.register(tg, hub, redis)
        logger.info("Observer enabled")

    cleaner_task = asyncio.create_task(run_cleaner(hub))
    try:
        yield
    finally:
        cleaner_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleaner_task
        await tg.stop()
        await redis.aclose()
        await engine.dispose()


async def _bootstrap_default_drive(tg: TelegramService) -> None:
    """Register BOOTSTRAP_DRIVES (alias:chat_id,…) and DEFAULT_CHAT_ID on first boot."""
    settings = get_settings()
    wanted: list[tuple[str, str]] = []
    for item in settings.bootstrap_drives.split(","):
        if ":" in item:
            alias, _, chat_id = item.strip().partition(":")
            if alias and chat_id:
                wanted.append((alias, chat_id))
    if settings.default_chat_id:
        wanted.append(("default", str(settings.default_chat_id)))

    for alias, chat_id in wanted:
        async with SessionLocal() as db:
            existing = (
                await db.execute(
                    select(ChatAlias).where(
                        (ChatAlias.chat_id == chat_id) | (ChatAlias.alias == alias)
                    )
                )
            ).scalars().first()
            if existing is not None:
                continue
            try:
                info = await tg.chat_info(chat_id)
            except TupError as exc:
                logger.warning("Could not register drive %s (%s): %s", alias, chat_id, exc)
                continue
            db.add(ChatAlias(alias=alias, chat_id=info["chat_id"], title=info["title"]))
            await db.commit()
            logger.info("Registered drive %s → %s (%s)", alias, info["chat_id"], info["title"])


app = FastAPI(title="tup-cloud", lifespan=lifespan)

# Same-origin in production (nginx proxy); localhost:5173 for Vite dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(drives.router)
app.include_router(vfs.router)
app.include_router(uploads.router)
app.include_router(files.router)
app.include_router(observer_router.router)
app.include_router(wopi.router)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    token = websocket.cookies.get(ACCESS_COOKIE)
    payload = decode_access_token(token) if token else None
    if payload is None:
        await websocket.close(code=4401)
        return
    async with SessionLocal() as db:
        user = await db.get(User, int(payload["sub"]))
        if user is None or not user.approved:
            await websocket.close(code=4401)
            return
        if user.role == "admin":
            allowed = {
                a.chat_id for a in (await db.execute(select(ChatAlias))).scalars().all()
            }
        else:
            allowed = set(
                await member_chat_ids(
                    db, websocket.app.state.redis, websocket.app.state.tg, user.telegram_id
                )
            )

    await websocket.accept()
    subscription = await websocket.app.state.hub.subscribe()
    forwarder = asyncio.create_task(
        forward_events(subscription, websocket.send_json, allowed, user.role == "admin")
    )
    try:
        while True:
            await websocket.receive_text()  # keepalive pings; content ignored
    except WebSocketDisconnect:
        pass
    finally:
        forwarder.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await forwarder
        await subscription.close()
