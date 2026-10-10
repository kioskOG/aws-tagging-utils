"""
Compliance report cache manager.

Handles persistence so results survive Flask restarts,
background refresh with a threading lock to prevent duplicate scans,
and TTL-based stale detection.

Configuration (env vars):
    COMPLIANCE_CACHE_TTL_SECONDS  — How old a cache can be before it is
                                    considered stale and triggers a background
                                    refresh. Default: 300 (5 minutes).
"""
from __future__ import annotations

import json
import logging
import os
import time
import threading
from pathlib import Path
from datetime import datetime, timezone

from src.db import init_db, insert_scan, get_latest_scan_report, get_connection, DB_PATH

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CACHE_FILE: Path = (
    Path(os.environ.get("COMPLIANCE_CACHE_FILE", ""))
    if os.environ.get("COMPLIANCE_CACHE_FILE")
    else Path(__file__).resolve().parent.parent / ".compliance_cache.json"
)

CACHE_TTL: int = int(os.environ.get("COMPLIANCE_CACHE_TTL_SECONDS", "300"))

# ---------------------------------------------------------------------------
# In-process state
# ---------------------------------------------------------------------------
_refresh_lock = threading.Lock()
_is_refreshing: bool = False
_last_refresh_error: str | None = None
_db_initialized = False
_db_init_lock = threading.Lock()

def _ensure_db():
    global _db_initialized
    if not _db_initialized:
        with _db_init_lock:
            if not _db_initialized:
                init_db()
                _migrate_from_json()
                _db_initialized = True

def _migrate_from_json():
    """One-time migration from .compliance_cache.json to SQLite."""
    if not CACHE_FILE.exists():
        return
        
    try:
        with get_connection() as conn:
            # Check if we already have scans
            cursor = conn.execute("SELECT COUNT(*) FROM compliance_scans")
            count = cursor.fetchone()[0]
            if count > 0:
                return # Already migrated or has newer data
                
        # Read old JSON
        log.info(f"Migrating {CACHE_FILE} to SQLite DB")
        with CACHE_FILE.open("r") as fh:
            data = json.load(fh)
            
        if data:
            # Reconstruct timestamp from _meta if possible
            gen_at = data.get("_meta", {}).get("generated_at")
            if gen_at:
                data["timestamp"] = datetime.fromtimestamp(gen_at, timezone.utc).isoformat()
            
            insert_scan(data, status="COMPLETED")
            log.info("Migration successful. Note: Original JSON file preserved.")
            
    except Exception as e:
        log.warning("Failed to migrate JSON cache to SQLite: %s", e)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_cached_report() -> dict | None:
    """Read the persisted report from DB. Returns None on any error."""
    _ensure_db()
    try:
        report = get_latest_scan_report()
        if report:
            # Re-add the _meta block which UI expects
            timestamp_iso = report.get("timestamp")
            gen_at = time.time()
            if timestamp_iso:
                try:
                    # Parse isoformat back to timestamp
                    dt = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
                    gen_at = dt.timestamp()
                except ValueError:
                    pass
            
            report["_meta"] = {
                "generated_at": gen_at,
                "status": "Fresh"
            }
        return report
    except Exception as exc:
        log.warning("Failed to read compliance cache from DB: %s", exc)
        return None


def save_report(report_data: dict) -> dict:
    """Persist a report to DB, adding a _meta timestamp block."""
    _ensure_db()
    try:
        insert_scan(report_data)
    except Exception as exc:
        log.error("Failed to save compliance report to DB: %s", exc)
    try:
        from src import protected_tags
        protected_tags.seed_baselines(
            (r.get("ResourceARN"), r.get("Tags") or {})
            for region in (report_data.get("regions") or {}).values() for r in region.get("resources") or [])
    except Exception as exc:
        log.error("Failed to seed protected-tag baselines: %s", exc)
        
    out = dict(report_data)
    out["_meta"] = {
        "generated_at": time.time(),
        "status": "Fresh",
    }
    return out


def is_cache_stale() -> bool:
    """Return True when no cache exists or the latest scan is older than CACHE_TTL."""
    _ensure_db()
    try:
        with get_connection() as conn:
            cursor = conn.execute("SELECT timestamp FROM compliance_scans WHERE status = 'COMPLETED' ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if not row:
                return True
                
            timestamp_iso = row["timestamp"]
            dt = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
            return time.time() - dt.timestamp() > CACHE_TTL
    except Exception as exc:
        log.warning("Error checking cache staleness: %s", exc)
        return True


def is_refreshing() -> bool:
    return _is_refreshing


def get_refresh_error() -> str | None:
    return _last_refresh_error


def clear_refresh_error() -> None:
    global _last_refresh_error
    _last_refresh_error = None


def resolve_scan_regions() -> list[str]:
    """Regions for scheduled/background scans, from COMPLIANCE_REGIONS (comma list or 'all')."""
    from src.config import COMPLIANCE_REGIONS, DEFAULT_REGION
    raw = COMPLIANCE_REGIONS.strip()
    if raw.lower() == "all":
        from src.tag_report import get_all_regions
        return get_all_regions()
    regs = [r.strip() for r in raw.split(",") if r.strip()]
    return regs or [DEFAULT_REGION]


def background_refresh(regions: list[str] | None = None,
                       mandatory_tags: list[str] | None = None) -> None:
    """
    Perform a compliance scan and persist the result.

    Must be called in a daemon thread. Uses a non-blocking lock so that a
    second call while one is already running is silently ignored.
    """
    global _is_refreshing, _last_refresh_error

    if not _refresh_lock.acquire(blocking=False):
        log.debug("background_refresh: already running, skipping.")
        return

    _is_refreshing = True
    _last_refresh_error = None
    _ensure_db()

    try:
        # Import lazily to avoid circular imports at module load time
        from src.config import MANDATORY_TAGS
        from src.tag_report import generate_report

        regs = regions or resolve_scan_regions()
        tags = mandatory_tags or MANDATORY_TAGS

        from src.config import COMPLIANCE_ACCOUNTS
        if COMPLIANCE_ACCOUNTS:
            # Account names / OUs for leaderboards (refreshed at most daily)
            from src.aws_session import account_directory
            account_directory(refresh_if_stale=True)

        log.info("background_refresh: scanning regions=%s tags=%s", regs, tags)
        report = generate_report(regs, tags)
        region_errors = {r: d.get("error") for r, d in report.get("regions", {}).items() if d.get("error")}
        if region_errors and len(region_errors) == len(regs):
            # Every region failed (bad credentials, no network...). Keep the last good scan.
            first = next(iter(region_errors.values()))
            raise RuntimeError(f"All regions failed to scan: {first}")
        save_report(report)
        if region_errors:
            _last_refresh_error = "Partial scan, failed regions: " + ", ".join(sorted(region_errors))
        log.info("background_refresh: saved %d resources.",
                 report.get("summary", {}).get("total_resources", 0))
    except Exception as exc:
        _last_refresh_error = str(exc)
        log.error("background_refresh failed: %s", exc)
    finally:
        _is_refreshing = False
        _refresh_lock.release()
