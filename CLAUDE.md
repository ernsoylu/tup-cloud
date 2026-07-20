# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

tup-cloud is the Dockerized web version of the sibling project `../tup` (Telegram
chats as S3-style drives with a POSIX-like VFS). FastAPI backend + React SPA +
PostgreSQL (system of record) + Redis (pub/sub, refresh tokens, rate limits,
membership cache). Full design and phase history: `PLAN.md`. The tup VFS
semantics (caption protocol, SHA-256 same-folder dedup, path-only `mv`,
empty-only `rmdir`, `.keep` folder markers, MTProto-only transport, 2 GB cap)
are ported from `../tup` and must stay behavior-identical — `../tup/CLAUDE.md`
is the authoritative spec for those rules.

## Commands

```bash
docker compose up -d --build       # full stack on http://localhost:8080
docker compose logs backend -f     # backend logs (telethon noise is normal)

# Backend dev (Python 3.12+; needs env vars, see .env / app/config.py)
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload      # expects postgres+redis reachable

# Frontend dev (Vite proxies /api and /ws to localhost:8000)
cd frontend && npm install
npm run dev
npm run typecheck                  # tsc -b — must pass; build runs it too
npm run build
```

There is no test suite yet. Quick backend validation used so far:
`python -m compileall backend/app` and importing `app.main` with dummy env vars
(`TELEGRAM_BOT_TOKEN=x TELEGRAM_API_ID=1 TELEGRAM_API_HASH=y JWT_SECRET=z`)
then calling `app.openapi()`.

## Configuration

Real secrets live in `./.env` (gitignored, 0600) — bootstrapped from the user's
`~/.tup/.env`. `BOOTSTRAP_DRIVES=alias:chat_id,…` seeds drives on first boot
(imported from the local tup registry). `.env.example` documents everything.

## Architecture (what you must know before touching it)

- **One Telethon client** (`app/telegram.py`, bot-token MTProto login) is the
  only file transport: uploads, downloads, Range streaming, caption edits,
  deletes, login-code DMs. The HTTP Bot API is used *only* for metadata
  (`getChat`, `getChatMember`) via httpx. Session persists in the `tgsession`
  volume; transfers are serialized by `TelegramService.transfer_lock`
  (Telegram throttles parallel bot transfers).
- **Auth is Telegram OTP, not passwords** (`routers/auth.py`, `security.py`).
  Two paths: (1) *bot-initiated* — user DMs `/login` to the bot
  (handled in `observer.py`), membership is checked, the bot replies with an
  8-char single-use code the user types on the login page (`verify` without a
  challenge); (2) *web-initiated* — identifier → membership policy → bot DMs a
  6-digit code bound to a challenge id. Both mint a JWT access token (60 min)
  plus a rotating Redis refresh token (30 d, 30 s grace window for concurrent
  tabs) in HttpOnly cookies. Cookies (not headers) are load-bearing:
  `<video>`/`<img>` and the WebSocket authenticate via cookie. The frontend
  auto-refreshes on 401 for everything except refresh/verify/request-code.
  First user ever becomes admin.
- **Authorization = live chat membership** (`membership.py`): a user may log
  in and see a drive iff Telegram says they're a member of that registered
  chat (private-chat drives match only that user). Cached 5 min in Redis; all
  VFS/file/upload/WS endpoints enforce it via the `UserChats` dependency.
  Admins bypass and see all drives.
- **Events flow one way**: any mutation publishes to Redis channel
  `tup:events` (`events.py`); the `/ws` endpoint fans out to clients, filtered
  per-connection by drive membership. Frontend reacts (`ws.ts`) — e.g.
  `index-changed` triggers a quiet refetch. If you add a mutation, publish.
- **Observer** (`observer.py`): Telethon NewMessage handler. Non-tup media in
  registered chats is indexed under `/Other` (staged: detected → analyzing →
  indexed, each stage published + persisted in `observer_events`). tup-captioned
  messages are indexed at their declared path. Observed entries have
  `origin='observed'`, a `tg:<doc_id>` pseudo-hash (no download → no SHA-256),
  and skip remote caption edits on mv (bots can't edit others' messages).
  Private messages to the bot are also handled here (entity caching for DMs,
  contact→phone capture, /start reply).
- **Uploads are two-hop** (`transfers.py`): browser multipart → spool dir →
  server-side SHA-256 + dedup check → MTProto with progress events. Failures
  keep the spool file and land in `failed_registry` for retry; duplicates are
  "skipped", never failures.
- **Streaming** (`routers/files.py`): cached files served from disk, otherwise
  Range requests are proxied via `iter_download` with 1 MiB chunk alignment
  (MTProto offset constraint — don't change `STREAM_CHUNK` casually). The
  cache autocleaner (`cleaner.py`) deletes files idle past `CACHE_TTL_MINUTES`;
  serving a file touches its mtime.
- **Frontend derives everything client-side** (`store.ts`): one whole-drive
  index fetch; folder tree, listings, recursive (`ls -R`) view, and `#tag`
  filtering are pure functions over that array — mirroring the desktop GUI's
  "one query per refresh" design.
- **DB schema changes**: `init_db` runs `create_all` plus the additive
  `_MIGRATIONS` list in `app/db.py` (`ALTER TABLE … IF NOT EXISTS`). Add new
  columns there; there is no Alembic.

## Standing practice: the parity ledger

`TECH-DEBT.md` tracks every cloud capability that the desktop tup (CLI/GUI in
`../tup`) could adopt. **Whenever you add or change a tup-cloud feature, update
TECH-DEBT.md in the same change** — one table row per feature with backport
notes. The user relies on this ledger to evolve the desktop apps.

## Gotchas

- A bot cannot DM a user who never pressed Start on it, and cannot resolve
  phone numbers — login by phone only matches users already known to the DB.
- `chat_id` strings use Bot-API format (`-100…` for channels/supergroups) and
  must stay strings end-to-end.
- Cross-drive cp/mv is impossible (Telegram file references are chat-scoped);
  the UI blocks it — don't "fix" that.
- Docker on this machine is Rancher Desktop (`open -a "Rancher Desktop"`), not
  Docker Desktop.
