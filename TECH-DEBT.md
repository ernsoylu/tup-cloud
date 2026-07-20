# TECH-DEBT.md — cloud ↔ CLI/GUI parity ledger

tup-cloud keeps growing capabilities that the desktop siblings (`../tup` CLI and
PyQt GUI) do not have yet. Because all three frontends share one storage model
(Telegram messages + the VFS caption protocol), most cloud features are
backportable. This file is the ledger of that debt.

**Standing rule: every time the cloud version gains a capability, add or update
an entry here in the same change.** (This rule is also recorded in CLAUDE.md so
AI-assisted sessions keep the ledger current.)

## Backportable to CLI/GUI

| Cloud feature | What it is | Backport notes for tup CLI/GUI |
| --- | --- | --- |
| **Observer → `/Other`** | Live NewMessage handler indexes non-tup files posted in drive chats into `/Other` (staged: detected → analyzing → indexed; `origin='observed'`, `tg:<doc_id>` pseudo-hash). | CLI could grow `tup watch` (daemon) using the same Telethon handler; GUI could run it on the bridge loop. Schema needs `origin` + pseudo-hash convention in `vfs_index`. |
| **User captions & #tags** | `user_caption`/`tags` columns; tags parsed from caption hashtags; `#tag` filtering; caption editing re-renders the protocol block. | Registry schema v3 candidate (`ALTER TABLE vfs_index ADD user_caption, tags`). CLI: `tup tag <drive> <path> "#a #b"`, `ls --tag`. GUI: tag column + filter. Caption protocol already carries the data — only indexing is missing. |
| **Markdown editor + save-through-server** | Create/edit text files in place; save = upload new message, update index row. | CLI: `tup edit <drive> <path>` ($EDITOR → re-upload). GUI: text editor dialog. Core: a `save_content()` op (bytes → upload → replace row) in `uploader.py`. |
| **File versioning** | Superseded messages of edited files are kept and recorded (`file_versions` table, capped at 20); history list + restore. | Same `save_content()` op should insert into a `file_versions` table instead of deleting the old message. Applies to any future in-place edit (Collabora Online is planned on this same pipeline). |
| **Recycle Bin (`/.Trash/`)** | Delete = move under `/.Trash/<original path>` with the caption rewritten to that path (state survives in Telegram → DB is rebuildable from the group). Restore strips the prefix; empty purges messages + versions. | CLI: `tup rm` → trash by default, `--force` to purge; `tup trash list/restore/empty`. GUI: Trash node in sidebar. Convention is caption-only, so all frontends interoperate immediately. |
| **Rebuild-from-group convention** | Captions alone describe the full state: path (incl. `/.Trash/`), SHA-256, user caption, tags. Multiple messages with the same path = versions; the newest message is current, older ones are history. | `tup index --reconstruct` should learn: treat `/.Trash/` paths as trashed and same-path duplicates as versions instead of conflicts. |
| **Range streaming** | Seekable playback via MTProto `iter_download` with chunk-aligned offsets. | GUI could stream-to-player instead of download-then-open (e.g. local HTTP bridge for the media player). |
| **Extension-based media fallback** | Legacy rows with no MIME/kind get type inferred from extension (serve-time and display-time). | Port to GUI models.py display layer; `tup index` backfill command. |
| **Server cache + TTL autoclean** | Download cache with mtime-refresh on serve and periodic TTL sweep. | GUI cache (`~/.tup/<chat_id>/…`) has no eviction at all today — same sweeper would fit. |
| **Collabora Online (CODE) editing + export** | CODE container + WOPI host endpoints; office docs (docx/xlsx/pptx/…) open in a full-page editor, saves version through the shared pipeline, Save-As creates VFS siblings, `convert-to` powers Export-as-PDF and blank-document creation. | Desktop apps can reuse the `convert-to` idea against a local LibreOffice (`soffice --convert-to pdf`) for `tup export <drive> <path> --pdf`; GUI could open office files via local LibreOffice with save-back-through-`save_content()`. |

| **CAD editing (OpenCADStudio, WOPI-patched)** | Self-built wasm fork of OpenCADStudio served from its own container; our `wopi.patch` teaches it `?wopi=…&access_token=…` sessions — auto-open from the drive, Ctrl+S saves back through the shared pipeline (versioned). DWG/DXF edit; STL/STEP/PDF export via manual save-back. | Desktop tup could associate CAD files with the native OpenCADStudio build; the patch is upstreamable (GPL) as a generic WOPI client mode, which would benefit any host. |
| **Database backups to Telegram** | Scheduled gzipped-JSON dumps of all tables uploaded as VFS files under `/Backups/` in a chosen drive; retention (keep last N); one-click full restore (transactional replace, sequences reset, newer backup entries preserved). | Desktop equivalent: `tup backup [--to <drive>]` uploading `registry.db` (or a JSON dump) to `/Backups/`, `tup restore <file>`; a cron/launchd entry covers scheduling. Same caption-indexed convention, so cloud and desktop can restore each other's dumps if the JSON format is shared. |

## Cloud-only (not applicable to desktop)

- Telegram-OTP auth, group-membership authorization, per-user drive visibility
  (desktop is single-user with local credentials).
- Redis pub/sub → WebSocket live updates (desktop uses in-process signals).
- Browser→server upload spool (desktop reads local files directly).

## Known debt inside the cloud itself

- `mv`/`cp`/`rm` of *folders* is not implemented (files only, matching GUI v1);
  folder delete requires the folder to be empty.
- Observed files keep a `tg:<doc_id>` pseudo-hash — no SHA-256 without
  downloading; dedup across upload/observed origins therefore can't match.
- Version history is capped at 20; no diff view.
- No automated test suite yet (validated via typecheck, OpenAPI generation,
  and live smoke tests).
