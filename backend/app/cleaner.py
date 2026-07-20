"""Cache autocleaner: deletes cached files untouched for CACHE_TTL_MINUTES,
prunes empty directories, and announces sweeps on the event hub."""

from __future__ import annotations

import asyncio
import logging
import time

from app.config import get_settings
from app.events import EventHub

logger = logging.getLogger("tup.cleaner")


def _sweep_once() -> tuple[int, int]:
    """Delete stale cache files; returns (files_removed, bytes_freed)."""
    settings = get_settings()
    cutoff = time.time() - settings.cache_ttl_minutes * 60
    removed = 0
    freed = 0
    if not settings.cache_dir.exists():
        return 0, 0
    for path in sorted(settings.cache_dir.rglob("*"), reverse=True):
        try:
            if path.is_file():
                if path.suffix == ".part" or path.stat().st_mtime < cutoff:
                    freed += path.stat().st_size
                    path.unlink()
                    removed += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            continue
    return removed, freed


async def run_cleaner(hub: EventHub) -> None:
    settings = get_settings()
    while True:
        try:
            removed, freed = await asyncio.to_thread(_sweep_once)
            if removed:
                logger.info("Cache sweep removed %d file(s), freed %d bytes", removed, freed)
                await hub.publish(
                    {"type": "cache-sweep", "removed": removed, "freed_bytes": freed}
                )
        except Exception:
            logger.exception("Cache sweep failed")
        await asyncio.sleep(settings.cache_sweep_seconds)
