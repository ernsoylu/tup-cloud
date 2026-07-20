# tup-cloud

Cloud version of [tup](../tup): turn Telegram chats, groups, and channels into
cloud storage drives — served as a web app with a React single-page frontend,
FastAPI backend, PostgreSQL, and Redis, all in Docker.

## Quick start

```bash
cp .env.example .env      # fill in your Telegram credentials (same as tup's ~/.tup/.env)
./tup-cloud.sh start      # builds and starts everything, waits for health
open http://localhost:8080
```

`./tup-cloud.sh` also provides `stop`, `rebuild [service]`, `purge`, `status`,
and `logs [service]`. To bring an existing desktop-tup index into the cloud,
run `./scripts/import-registry.sh`.

Log in via Telegram: open the bot, press *Start*, send **`/login`**, and enter
the code it returns on the login page. You can log in only if you are a member
of at least one registered drive chat, and you see only those drives. The
first person to log in becomes admin. Sessions last 30 days of inactivity.

---

## Architecture

```text
┌────────────────────────────────── docker compose ──────────────────────────────────┐
│                                                                                    │
│   Browser ──────────► nginx (frontend container)                                   │
│   React SPA           • serves the built SPA (immutable hashed assets)             │
│                       • proxies /api and /ws to the backend                        │
│                                │                                                   │
│                                ▼                                                   │
│   ┌────────────────────── backend (FastAPI + Telethon) ─────────────────────────┐  │
│   │                                                                             │  │
│   │  auth (Telegram OTP)      vfs ops (ls/mkdir/mv/cp/trash)    uploads (spool)  │  │
│   │  membership guard         markdown save + versioning        transfers queue │  │
│   │  streaming (HTTP Range)   observer (/Other pipeline)        cache cleaner   │  │
│   │  WebSocket hub ◄──────────── Redis pub/sub ("tup:events")                   │  │
│   │                                                                             │  │
│   │            ONE Telethon MTProto client = the only file transport            │  │
│   └───────┬──────────────────────┬───────────────────────┬──────────────────────┘  │
│           ▼                      ▼                       ▼                         │
│   ┌──────────────┐       ┌──────────────┐       ┌───────────────────────┐          │
│   │  PostgreSQL  │       │    Redis     │       │  volumes              │          │
│   │  system of   │       │  sessions,   │       │  MTProto session,     │          │
│   │  record      │       │  pub/sub, RL │       │  file cache, spool    │          │
│   └──────────────┘       └──────────────┘       └───────────────────────┘          │
│                                                                                    │
└────────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
                                madly important:
                     Telegram itself is the actual file storage
```

### 🧱 The storage model — Telegram is the disk

The single most important idea, inherited from tup: **files live in Telegram
messages; everything else is an index.**

- Every drive is one chat/group/channel. Every file is one message whose
  caption carries a machine-readable protocol block:

  ```text
  📁 `/docs/report.pdf`
  🔗 SHA256: <64-hex>

  <optional user caption with #tags>

  #vfs #docs
  ```

- PostgreSQL's `vfs_index` is a **cache of that truth**, not the truth itself.
  The captions alone can rebuild the database: a `/.Trash/…` path means
  "in the Recycle Bin"; several messages with the same path are versions of
  one file (newest message wins, older ones are history).
- Consequence: the desktop tup CLI/GUI, this web app, and any future frontend
  interoperate on the same chats with no coordination — they all read/write
  the same caption protocol. (`TECH-DEBT.md` tracks which cloud features the
  desktop apps should adopt.)

### 📡 One transport: MTProto

A single long-lived **Telethon client** (bot-token login) does *all* byte
transfer: uploads (2 GB cap), downloads, Range streaming, thumbnails, caption
edits, deletes, and the login-code DMs. The HTTP Bot API is used only for
cheap metadata (`getChat`, `getChatMember`). Transfers are serialized through
one semaphore because Telegram throttles parallel bot transfers anyway. The
MTProto session persists in a Docker volume, so restarts don't re-login.

### 🔐 Auth: Telegram is the identity provider

No passwords. Two ways in, both ending in the same session:

1. **Bot-initiated (primary)** — DM `/login` to the bot. It verifies your
   group membership live, then replies with an 8-character single-use code
   (HMAC-hashed in Redis, 5-min TTL, per-user rate limit). Enter it on the
   login page. Works even for brand-new group members, because *you* message
   the bot first (bots cannot DM strangers).
2. **Web-initiated** — enter your @username; the bot DMs you a 6-digit code
   bound to a server-side challenge. Requires that the bot could already
   message you.

Sessions are JWT access tokens (60 min) plus rotating refresh tokens in Redis
(30 days, 30-second grace window so parallel tabs don't log each other out) —
both in `HttpOnly; SameSite=Lax` **cookies**. Cookies are load-bearing:
`<video>`/`<img>` tags and the WebSocket authenticate with them, which
header-based tokens cannot do.

### 🛂 Authorization: group membership is the ACL

There are no roles to manage (beyond one admin): **you can use tup-cloud iff
Telegram says you're a member of a drive chat**, checked live via
`getChatMember` and cached 5 minutes in Redis. Every VFS, file, upload, and
WebSocket endpoint filters by that membership; users see exactly the drives
whose groups they belong to. Admins see all drives and can register/remove
drives and block users.

### 🔄 Real-time: one event bus, one socket

Every mutation — upload finished, index changed, observer progress, transfer
progress — is published to a single Redis pub/sub channel (`tup:events`) and
fanned out over one WebSocket per client, **filtered per connection by drive
membership**. The frontend reacts to events (e.g. `index-changed` triggers a
quiet refetch), so two browsers watching the same drive stay in sync without
polling.

### 👁 Observer: the group is also an inbox

A Telethon `NewMessage` handler watches every registered chat:

- A file with a tup caption (sent by the CLI, GUI, or another tup-cloud) is
  indexed at its declared path.
- **Any other file lands in `/Other`** through a staged pipeline —
  `detected → analyzing → indexed` (or `skipped`/`failed`) — with every stage
  persisted and streamed live to the Observer feed in the UI. Observed files
  are marked `origin: observed` and use a `tg:<doc_id>` pseudo-hash (nothing
  is downloaded to hash).
- Private messages to the bot are handled here too: `/login` (OTP issuing),
  `/start` (onboarding), and shared contacts (enables phone-based lookup).

### 🎬 Streaming: play while it downloads

`GET /api/files/{id}/stream` honors HTTP `Range`: byte offsets are aligned to
MTProto's 1 MiB chunk grid and proxied straight from Telegram via
`iter_download`. That gives seekable `<video>`/`<audio>` playback with no full
download (Safari's `bytes=0-1` probe, mid-file seeks — all served as proper
`206 Partial Content`). Because MTProto tops out at a few MB/s, high-bitrate
files offer **⚡ Cache for fast playback**: the server pulls the file to its
disk cache in the background; cached files serve at disk speed. A sweeper
deletes cache entries idle past `CACHE_TTL_MINUTES` (serving a file refreshes
its clock).

### 📝 Documents: edit, version, restore

Markdown files are first-class: a full-page Tiptap editor (Mantine's editor
component) reads/writes real `.md` messages through
`POST /api/files/text`. Since every save posts a *new* Telegram message, the
replaced message is kept as a **version snapshot** (`file_versions` table,
capped at 20 per file) — History lists them, clicking one loads it into the
editor, saving restores it. The same save-through-server pipeline is the
planned mount point for Collabora Online.

### 🗑 Recycle Bin: delete is a move, purge is the delete

Deleting a file moves it under the hidden `/.Trash/` prefix **and rewrites its
Telegram caption to that path** — so the bin's state survives in Telegram
itself (see: rebuildable database). The bin is a sidebar node listing files by
original location; Restore strips the prefix, and Empty/Delete-forever purges
the message *and every version message*.

### ⬆️ Uploads: two hops, visible progress

Browser → backend (multipart into a spool volume, progress via XHR events),
then backend → Telegram (MTProto with progress callbacks published to the
event bus). The transfers dock shows both hops live. Failures keep the spooled
file and land in `failed_registry` for retry; same-folder SHA-256 duplicates
are *skips*, never errors (tup's dedup rule).

### 🖥 Frontend: one fetch, pure derivations

React 18 + TypeScript + zustand + Mantine v7 (default dark theme, Tabler
icons). The SPA fetches the **whole drive index in one request** and derives
everything client-side as pure functions — folder tree, listings, recursive
`ls -R` view, `#tag` filtering, the Recycle Bin view — mirroring the desktop
GUI's "one query per refresh" design. Drag & drop (OS files and folders in,
internal move/copy), full keyboard shortcuts, context menus, Mantine modals
for all dialogs, and media previews in a fullscreen modal.

## Highlights at a glance

| | |
| --- | --- |
| 🧱 Storage | Telegram messages **are** the files; Postgres only indexes them |
| 🔁 Rebuildable | Captions encode path, hash, tags, trash state, versions |
| 📡 Transport | One Telethon MTProto client; 2 GB/file; serialized transfers |
| 🔐 Auth | `/login` OTP via the bot; JWT + rotating refresh in HttpOnly cookies |
| 🛂 Access | Live group membership = drive visibility, enforced everywhere |
| 🔄 Live UI | Redis pub/sub → per-user-filtered WebSocket events |
| 👁 Observer | Group uploads auto-indexed into `/Other` with a live pipeline feed |
| 🎬 Playback | Range-streaming (seek without downloading) + optional server cache |
| 📝 Editing | Full-page markdown editor with per-save version history |
| 🗑 Safety | Recycle Bin with restore; purge deletes messages + versions |
| 🧹 Hygiene | TTL cache autocleaner; secrets scrubbed from logs |

## Repository layout

```text
tup-cloud/
├── docker-compose.yml       # postgres 16 · redis 7 · backend · frontend(nginx)
├── tup-cloud.sh             # start / stop / rebuild / purge / status / logs
├── scripts/import-registry.sh  # one-shot import of ~/.tup/registry.db
├── backend/app/
│   ├── main.py              # app factory, lifespan wiring, WebSocket endpoint
│   ├── config.py            # env-only settings
│   ├── db.py  models.py     # async SQLAlchemy + additive migrations
│   ├── security.py          # JWT, refresh rotation, OTP codes, rate limits
│   ├── membership.py        # group-membership ACL (Redis-cached)
│   ├── telegram.py          # THE Telethon client + caption protocol
│   ├── transfers.py         # spool → MTProto queue with live progress
│   ├── observer.py          # NewMessage → /Other pipeline, /login, /start
│   ├── events.py            # Redis pub/sub hub
│   ├── cleaner.py           # cache TTL sweeper
│   └── routers/             # auth · drives · vfs (incl. trash) · uploads ·
│                            # files (stream/thumb/text/versions) · observer
└── frontend/src/
    ├── store.ts             # zustand state + pure derivations (tree/rows/trash)
    ├── api.ts  ws.ts        # fetch wrapper (auto-refresh) + WebSocket client
    ├── media.tsx dialogs.tsx# type→icon mapping · Mantine modal helpers
    └── components/          # Explorer(AppShell) · FileList · Sidebar ·
                             # MarkdownEditor(page) · Preview · Transfers ·
                             # ObserverFeed · Login
```

See `PLAN.md` for the phased build history, `CLAUDE.md` for contributor/agent
notes, and `TECH-DEBT.md` for the cloud ↔ desktop parity ledger.
