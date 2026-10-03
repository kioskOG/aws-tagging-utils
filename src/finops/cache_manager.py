"""
FinOps report cache manager.

Handles persistence of FinOps reports to SQLite database,
background refresh with a threading lock to prevent duplicate scans,
and TTL-based stale detection (24 hours).
"""
from __future__ import annotations

import logging
import os
import time
import threading
from datetime import datetime, timezone

from src.db import init_db, insert_finops_snapshot, get_latest_finops_snapshot, get_connection

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FINOPS_CACHE_TTL: int = int(os.environ.get("FINOPS_CACHE_TTL_SECONDS", "86400")) # 24 hours

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
                _db_initialized = True

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_cached_report() -> dict | None:
    """Read the persisted FinOps report from DB. Returns None if no report exists."""
    _ensure_db()
    try:
        report = get_latest_finops_snapshot()
        if report:
            # Re-add the _meta block for UI
            timestamp_iso = report.get("timestamp")
            gen_at = time.time()
            if timestamp_iso:
                try:
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
        log.warning("Failed to read FinOps cache from DB: %s", exc)
        return None

def is_cache_stale() -> bool:
    """Return True when no cache exists or the latest snapshot is older than TTL."""
    _ensure_db()
    try:
        with get_connection() as conn:
            cursor = conn.execute("SELECT timestamp FROM finops_snapshots WHERE status = 'COMPLETED' ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if not row:
                return True
                
            timestamp_iso = row["timestamp"]
            dt = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
            return time.time() - dt.timestamp() > FINOPS_CACHE_TTL
    except Exception as exc:
        log.warning("Error checking FinOps cache staleness: %s", exc)
        return True

def is_refreshing() -> bool:
    return _is_refreshing

def get_refresh_error() -> str | None:
    return _last_refresh_error

def clear_refresh_error() -> None:
    global _last_refresh_error
    _last_refresh_error = None

def background_refresh() -> None:
    """
    Perform a FinOps Cost Explorer query and persist the result.
    Must be called in a daemon thread. Uses a non-blocking lock.
    """
    global _is_refreshing, _last_refresh_error

    if not _refresh_lock.acquire(blocking=False):
        log.debug("finops background_refresh: already running, skipping.")
        return

    _is_refreshing = True
    _last_refresh_error = None
    _ensure_db()

    try:
        # Lazy imports
        from src.finops.report import FinOpsReportGenerator
        from src.finops.cost_explorer import CostExplorerClient
        from src.governance.schema_provider import FileSchemaProvider
        from src.config import GOVERNANCE_SCHEMA_PATH
        
        schema_provider = FileSchemaProvider(GOVERNANCE_SCHEMA_PATH)
        gen = FinOpsReportGenerator(CostExplorerClient(), schema_provider)
        
        log.info("finops background_refresh: starting Cost Explorer scan")
        report = gen.generate_report()
        
        insert_finops_snapshot(report)
        log.info("finops background_refresh: saved new snapshot.")
    except Exception as exc:
        _last_refresh_error = str(exc)
        log.error("finops background_refresh failed: %s", exc)
        # We can explicitly log the failure in DB too
        try:
            insert_finops_snapshot({"error": str(exc)}, status="FAILED", error_msg=str(exc))
        except Exception as db_exc:
            log.error("finops background_refresh also failed to write failure to db: %s", db_exc)
    finally:
        _is_refreshing = False
        _refresh_lock.release()
