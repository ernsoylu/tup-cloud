#!/usr/bin/env bash
# Import the local tup CLI/GUI index (~/.tup/registry.db) into tup-cloud's
# PostgreSQL, so drives indexed by the desktop tools appear in the web app.
#
#   ./scripts/import-registry.sh [path-to-registry.db]
#
# Idempotent: rows that already exist (same chat/path/name) are left untouched.
# Only vfs_index rows are imported; drives themselves are registered via
# BOOTSTRAP_DRIVES or the admin UI.

set -euo pipefail
cd "$(dirname "$0")/.."

REGISTRY="${1:-$HOME/.tup/registry.db}"
if [ ! -f "$REGISTRY" ]; then
    echo "No registry at $REGISTRY" >&2
    exit 1
fi

if ! docker compose ps postgres --format '{{.Status}}' 2>/dev/null | grep -q Up; then
    echo "The postgres container is not running — start with ./tup-cloud.sh start" >&2
    exit 1
fi

python3 - "$REGISTRY" <<'PYEOF' | docker compose exec -T postgres psql -U "${POSTGRES_USER:-tup}" -d "${POSTGRES_DB:-tup}" -v ON_ERROR_STOP=1 -f -
import mimetypes
import sqlite3
import sys

COLUMNS = (
    "chat_id, virtual_path, file_name, file_size, file_hash, telegram_message_id, "
    "upload_timestamp, mime_type, media_kind, width, height, duration, origin, "
    "uploaded_by, user_caption, tags"
)


def field(value):
    if value is None:
        return r"\N"
    text = str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def route_for_mime(mime):
    if mime.startswith("image/") and mime not in ("image/svg+xml", "image/x-icon"):
        return "photo"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "document"


def infer_media(file_name, mime, kind):
    """Pre-v2 tup rows carry no MIME/kind; infer from the extension so the web
    app can pick players and icons."""
    if not mime or mime == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(file_name)
        mime = guessed or mime or ""
    if not kind and mime:
        kind = route_for_mime(mime)
    return mime, kind


conn = sqlite3.connect(sys.argv[1])
rows = conn.execute(
    """
    SELECT chat_id, virtual_path, file_name, file_size, file_hash,
           telegram_message_id, upload_timestamp, mime_type, media_kind,
           width, height, duration
    FROM vfs_index
    """
).fetchall()

print("CREATE TEMP TABLE staging (LIKE vfs_index INCLUDING DEFAULTS);")
print("ALTER TABLE staging DROP COLUMN id;")
print(f"COPY staging ({COLUMNS}) FROM stdin;")
for row in rows:
    row = list(row)
    row[7], row[8] = infer_media(row[2], row[7] or "", row[8] or "")
    values = [field(v) for v in row] + ["upload", "tup-import", "", ""]
    print("\t".join(values))
print("\\.")
print(
    f"INSERT INTO vfs_index ({COLUMNS}) SELECT {COLUMNS} FROM staging "
    "ON CONFLICT ON CONSTRAINT uq_vfs_path_name DO NOTHING;"
)
print("SELECT chat_id, COUNT(*) AS files FROM vfs_index GROUP BY chat_id;")
print(f"-- imported candidates: {len(rows)}", file=sys.stderr)
PYEOF
