# tup-cloud — Development Plan

Cloud version of [`tup`](../tup) (Telegram-as-a-drive): a Dockerized web app that
exposes Telegram chats/groups/channels as cloud storage drives with a POSIX-like
VFS, served by a FastAPI backend and a React single-page frontend.

## Architecture

```text
┌──────────────────────────── docker compose ────────────────────────────────┐
│                                                                            │
│  ┌──────────┐   /       ┌───────────────────────────────────────────────┐  │
│  │ frontend │──────────▶│ nginx: serves built SPA, proxies /api + /ws   │  │
│  │ (React)  │           └──────────────────────┬────────────────────────┘  │
│  └──────────┘                                  │                           │
│                                                ▼                           │
│  ┌─────────────────────────────── backend (FastAPI) ────────────────────┐  │
│  │  auth (JWT cookies, argon2)   vfs ops (ls/tree/mkdir/cp/mv/rm)       │  │
│  │  uploads (multipart → MTProto, progress events)                      │  │
│  │  streaming (HTTP Range proxy over Telethon iter_download)            │  │
│  │  observer (Telethon NewMessage → /Other ingestion pipeline)          │  │
│  │  cache autocleaner (TTL sweep)   WebSocket hub (Redis pub/sub)       │  │
│  └───────┬──────────────────────┬───────────────────────┬───────────────┘  │
│          ▼                      ▼                       ▼                  │
│  ┌──────────────┐      ┌──────────────┐        ┌─────────────────┐         │
│  │  PostgreSQL  │      │    Redis     │        │ volumes:        │         │
│  │ users, vfs,  │      │ pub/sub, rt  │        │ mtproto session │         │
│  │ logs, events │      │ sessions, RL │        │ file cache      │         │
│  └──────────────┘      └──────────────┘        └─────────────────┘         │
└────────────────────────────────────────────────────────────────────────────┘
```

### Datastore split (per user direction: PostgreSQL preferred)
- **PostgreSQL** — system of record: `users`, `chat_aliases` (drives), `vfs_index`,
  `uploads_log`, `failed_registry`, `observer_events`.
- **Redis** — volatile/real-time state: pub/sub channel for WebSocket fan-out
  (transfer progress, observer pipeline stages, index-changed), refresh-token
  store (rotation + revocation), login rate limiting.

### Ported tup semantics (unchanged)
- VFS caption protocol (`📁 path / 🔗 SHA256 / #vfs #folder`), parseable both ways.
- Root-relative POSIX paths; directories stored with trailing `/`; `.keep` rows for
  empty folders; `mv` is path-only (Telegram cannot rename); `rmdir` empty-only.
- Same-folder SHA-256 dedup → "skipped", never a failure.
- MTProto (Telethon, bot-token login) as the only file transport, 2 GB cap;
  server-side `cp` re-sends media without re-upload.
- Media routing by magic bytes (photo/video/audio/document) with mimetypes fallback.

### Cloud-specific design decisions
- **Auth (Telegram OTP)**: the user enters their Telegram @username or numeric
  ID; the backend resolves it via the bot, generates a one-time code (hashed,
  5-min TTL in Redis, attempt-limited), and the bot DMs the code to that
  Telegram account; entering the code completes login. Sessions use short-lived
  JWT access + rotating Redis-backed refresh tokens, both in
  `HttpOnly; SameSite=Lax` cookies (required so `<video>`/`<img>` tags and the
  WebSocket can authenticate without JS headers). Telegram constraints honored:
  a bot can only DM users who pressed **Start** on it, and cannot look up
  accounts by phone — phone login therefore only matches users already known to
  the system. First successful login becomes admin; later identities require
  admin approval unless whitelisted via `ALLOWED_TELEGRAM_IDS`. Request-code
  and verify endpoints are rate-limited via Redis.
- **Single bot, shared drives**: one set of Telegram credentials (env, from
  `~/.tup/.env`); all authenticated users see the same drives. `uploaded_by`
  is recorded per file.
- **Transfers**: browser→server multipart upload (browser progress via XHR),
  then server→Telegram MTProto upload serialized by a global semaphore
  (Telegram throttles parallel bot transfers) with progress published to Redis →
  WebSocket, mirroring the GUI transfer queue.
- **Observer**: a Telethon `NewMessage` handler on all registered chats. Any
  media message *not* carrying a tup caption is ingested under **`/Other`**:
  `detected → analyzing → indexed` pipeline stages, each stage published live
  to the observer feed. Name collisions get ` (2)`-style suffixes; same-folder
  hash dupes are skipped. Caption-carrying messages (e.g. sent by another tup
  instance) are indexed at their declared path instead.
- **Playback/preview**: `GET /api/files/{id}/stream` honors `Range` and proxies
  through Telethon `iter_download` (seekable video/audio without full download);
  cached files are served from disk (nginx-grade range support via FileResponse).
  `GET /api/files/{id}/thumb` serves Telegram's server-side thumbnail (a few KB).
  `GET /api/files/{id}/download` caches to disk then serves.
- **Autoclean**: background sweeper deletes cache files whose atime/mtime is older
  than `CACHE_TTL_MINUTES` (default 60), runs every `CACHE_SWEEP_SECONDS`;
  cache usage is reported in the UI.

## Repository layout

```text
tup-cloud/
├── docker-compose.yml          # postgres + redis + backend + frontend
├── .env.example                # documented; real .env is gitignored
├── PLAN.md                     # this file
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py             # app factory, lifespan (db, redis, telethon, observer, cleaner)
│       ├── config.py           # pydantic-settings (env only, no .env discovery magic)
│       ├── db.py               # async SQLAlchemy engine/session + schema bootstrap
│       ├── models.py           # ORM models
│       ├── security.py         # argon2, JWT mint/verify, cookie helpers
│       ├── deps.py             # FastAPI dependencies (current_user, db session)
│       ├── events.py           # Redis pub/sub hub + WebSocket endpoint
│       ├── telegram.py         # Telethon client lifecycle, captions, retry, peers
│       ├── vfsutil.py          # path normalization, mime, hashing (port of utils.py)
│       ├── routers/
│       │   ├── auth.py         # register/login/refresh/logout/me
│       │   ├── drives.py       # list/add/remove drives, validation
│       │   ├── vfs.py          # index listing, mkdir, mv, cp, rm, rmdir
│       │   ├── uploads.py      # multipart upload → MTProto, transfers state
│       │   ├── files.py        # stream (Range), download, thumb
│       │   └── observer.py     # observer feed + status
│       ├── observer.py         # NewMessage ingestion pipeline → /Other
│       └── cleaner.py          # cache TTL sweeper
├── frontend/
│   ├── Dockerfile              # node build → nginx serve
│   ├── nginx.conf              # SPA fallback + /api + /ws proxy
│   ├── package.json  vite.config.ts  tsconfig.json  index.html
│   └── src/
│       ├── main.tsx  App.tsx  api.ts  types.ts  store.ts (zustand)
│       ├── ws.ts               # WebSocket client with reconnect
│       ├── hooks/useShortcuts.ts
│       └── components/
│           ├── Login.tsx       # login/register card
│           ├── Explorer.tsx    # shell: toolbar, sidebar, listing, panels
│           ├── Sidebar.tsx     # drive selector + folder tree
│           ├── FileList.tsx    # details/grid views, selection, dnd, context menu
│           ├── Breadcrumbs.tsx
│           ├── Transfers.tsx   # bottom transfers panel
│           ├── ObserverFeed.tsx# live /Other ingestion pipeline view
│           └── Preview.tsx     # image/video/audio/pdf modal with streaming
└── (root) .gitignore, CLAUDE.md
```

## Phased development

### Phase 0 — Foundations
Compose file (postgres 16, redis 7, backend, frontend), volumes (pgdata,
mtproto session, cache), healthchecks, `.env` bootstrapped from `~/.tup/.env`
(bot token, api id/hash, default chat) + generated `JWT_SECRET`, `.gitignore`.

### Phase 1 — Backend core: config, DB, auth
Settings from env; async SQLAlchemy models + startup schema creation; argon2id
password hashing; JWT access (15 min) + refresh (14 d, rotated, Redis-backed,
revocable) in HttpOnly cookies; register (first user admin, then gated by
`ALLOW_REGISTRATION`), login (rate-limited), refresh, logout, `/me`.

### Phase 2 — Telegram core + VFS operations
Telethon client (lazy singleton, session in volume); ported caption protocol +
path/mime/hash utilities; drives CRUD with live chat validation; VFS endpoints:
whole-drive index (`GET /api/vfs/{drive}`), mkdir, rm, rmdir, mv, cp (server-side
media re-send), with exact tup edge rules.

### Phase 3 — Uploads + real-time layer
Redis pub/sub hub with per-connection WebSocket fan-out (`/ws`); multipart
upload endpoint (temp spool → hash → dedup check → MTProto with progress
callback → index + log); transfer registry with `queued/running/done/failed/
skipped` states queryable via REST and streamed via WS; failed registry + retry.

### Phase 4 — Observer (the /Other pipeline)
Telethon `NewMessage` handler over registered chats; non-tup media →
`/Other` ingestion with staged progress (`detected → analyzing → indexed`)
published to WS and persisted in `observer_events`; tup-captioned messages from
other instances indexed at their declared path; observer feed API.

### Phase 5 — Streaming, preview, autoclean
Range-aware streaming proxy (Telethon `iter_download`, chunk-aligned offsets);
download-to-cache endpoint; thumbnail endpoint; cache sweeper with
`CACHE_TTL_MINUTES`; cache stats endpoint.

### Phase 6 — Frontend SPA
Vite + React + TS + zustand. Login; explorer (sidebar tree, breadcrumbs,
details/grid views); OS drag-&-drop upload incl. folders; internal drag to move
(Ctrl=copy); context menus; keyboard shortcuts (arrows/Enter/Backspace/Delete/
Ctrl+A/Ctrl+C/Ctrl+X/Ctrl+V/F5, `/` filter, Esc); transfers panel; observer
feed; media preview modal with streamed `<video>`/`<audio>`/`<img>`/pdf;
WS-driven live refresh; toasts for errors.

### Phase 7 — Hardening & docs
Compose build validation, backend smoke tests (auth + vfs path rules), README
with runbook, CLAUDE.md for future agents.

## Environment variables (backend)

| Var | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | Bot API token (required) |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | — | my.telegram.org MTProto creds (required) |
| `DEFAULT_CHAT_ID` | — | pre-registered drive on first boot (optional) |
| `DATABASE_URL` | compose-internal | postgresql+asyncpg DSN |
| `REDIS_URL` | compose-internal | redis DSN |
| `JWT_SECRET` | — | HMAC secret (required) |
| `ALLOW_REGISTRATION` | `true` | open registration after first user |
| `ACCESS_TOKEN_MINUTES` / `REFRESH_TOKEN_DAYS` | 15 / 14 | token lifetimes |
| `CACHE_DIR` | `/data/cache` | streaming/download cache |
| `CACHE_TTL_MINUTES` | `60` | autoclean age threshold |
| `CACHE_SWEEP_SECONDS` | `300` | autoclean interval |
| `OBSERVER_ENABLED` | `true` | toggle the group observer |
| `SESSION_DIR` | `/data/session` | Telethon session volume |
