# TECH-DEBT.md — cloud ↔ CLI/GUI parity ledger

tup-cloud keeps growing capabilities that the desktop siblings (`../tup` CLI and
PyQt GUI) do not have yet. Because all three frontends share one storage model
(Telegram messages + the VFS caption protocol), most cloud features are
backportable. This file is the ledger of that debt.

**Standing rule: every time the cloud version gains a capability, add or update
an entry here in the same change.** (This rule is also recorded in CLAUDE.md so
AI-assisted sessions keep the ledger current.)

## Backported (desktop has parity as of 2026-07-21)

| Cloud feature | Desktop status |
| --- | --- |
| **User captions & #tags** | ✅ Schema v3 (`user_caption`, `tags`, `origin` + `file_versions`). CLI: `tup caption`, `tup tag`, `tup ls --tag`; GUI: Tags column, filter box matches tags, "Caption & tags…" context action. Same `#(\w+)`/lowercase/sorted normalization. |
| **Markdown editor + save-through-server** | ✅ Core `save_content()` in `tup/vfs_ops.py` (same no-op-on-identical-hash, caption carry-forward, document kind). CLI: `tup edit` ($EDITOR round-trip). GUI text-editor dialog not yet done. |
| **File versioning** | ✅ `file_versions` table, cap 20 with message pruning, `tup versions [--restore]` (restore = re-save, cloud semantics). Observed files never versioned. |
| **Recycle Bin (`/.Trash/`)** | ✅ `tup rm` trashes by default (`--force` purges), `tup trash list/restore/empty`, GUI Recycle Bin sidebar node + Restore/Delete-permanently/Empty. Same caption-rewrite convention + " (2)" name dedup. |
| **Rebuild-from-group convention** | ✅ `tup index --reconstruct` treats same-path duplicates as version chains (newest = current) and indexes captions/tags; `/.Trash/` paths land as trashed naturally. |
| **Extension-based media fallback** | ✅ `fallback_kind()` in utils; GUI icons/kind column and photo-cache detection use it for legacy rows. |
| **Server cache + TTL autoclean** | ✅ GUI cache sweep on startup (LRU by last open, `cache_ttl_hours` setting, default 168h) + mtime touch on open; `.part` files always evicted. Only per-drive dirs are swept (`~/.tup` is also tup's home). |
| **Database backups to Telegram** | ✅ `tup backup [--restore] [--keep N]` → `/Backups/tup-backup-<stamp>.json.gz`. Same JSON structure (`format`/`version`/`tables`); desktop restore accepts cloud dumps (unknown tables/columns ignored, missing NOT NULLs defaulted). No scheduler — use cron/launchd. |

## Backportable to CLI/GUI

| Cloud feature | What it is | Backport notes for tup CLI/GUI |
| --- | --- | --- |
| **Observer → `/Other`** | Live NewMessage handler indexes non-tup files posted in drive chats into `/Other` (staged: detected → analyzing → indexed; `origin='observed'`, `tg:<doc_id>` pseudo-hash). | CLI could grow `tup watch` (daemon) using the same Telethon handler; GUI could run it on the bridge loop. Schema now has `origin` (v3), and desktop ops already respect observed-message ownership — only the handler itself is missing. |
| **GUI text editor** | In-place text editing dialog (desktop `save_content()` pipeline already exists; `tup edit` covers the CLI). | A QPlainTextEdit dialog wired to `tup.vfs_ops.save_content()`. |
| **Range streaming** | Seekable playback via MTProto `iter_download` with chunk-aligned offsets. | GUI could stream-to-player instead of download-then-open (e.g. local HTTP bridge for the media player). |
| **Collabora Online (CODE) editing + export** | CODE container + WOPI host endpoints; office docs (docx/xlsx/pptx/…) open in a full-page editor, saves version through the shared pipeline, Save-As creates VFS siblings, `convert-to` powers Export-as-PDF and blank-document creation. | Desktop apps can reuse the `convert-to` idea against a local LibreOffice (`soffice --convert-to pdf`) for `tup export <drive> <path> --pdf`; GUI could open office files via local LibreOffice with save-back-through-`save_content()`. |

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
