import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.logging_config import get_logger

logger = get_logger(__name__)

DB_PATH: Path = (
    Path(os.environ.get("SQLITE_DB_PATH", ""))
    if os.environ.get("SQLITE_DB_PATH")
    else Path(__file__).resolve().parent.parent / "app.db"
)

# Use a threading.local to hold connections for thread-safety in background refresh
_local = threading.local()

def get_connection() -> sqlite3.Connection:
    """Get a thread-local SQLite connection with WAL enabled."""
    if not hasattr(_local, "conn"):
        # We use check_same_thread=False but still keep it thread-local for safety
        # and transaction isolation.
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        
        # Enable Write-Ahead Logging for better concurrency (readers don't block writers)
        conn.execute("PRAGMA journal_mode=WAL;")
        # Enable foreign key enforcement
        conn.execute("PRAGMA foreign_keys=ON;")
        
        _local.conn = conn
    return _local.conn

def init_db() -> None:
    """Initialize database schema."""
    conn = get_connection()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS compliance_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                regions TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                status TEXT NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS compliance_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                arn TEXT NOT NULL,
                region TEXT NOT NULL,
                account TEXT NOT NULL,
                type TEXT NOT NULL,
                is_compliant BOOLEAN NOT NULL,
                violations_json TEXT,
                tags_json TEXT,
                FOREIGN KEY (scan_id) REFERENCES compliance_scans(id) ON DELETE CASCADE
            );
            
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                actor TEXT,
                action TEXT NOT NULL,
                resource_arn TEXT,
                result TEXT,
                details_json TEXT,
                request_id TEXT
            );
            
            CREATE TABLE IF NOT EXISTS finops_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                report_json TEXT NOT NULL,
                status TEXT NOT NULL,
                error_msg TEXT
            );
            
            CREATE INDEX IF NOT EXISTS idx_compliance_resources_scan_id ON compliance_resources(scan_id);
            CREATE INDEX IF NOT EXISTS idx_compliance_scans_timestamp ON compliance_scans(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_finops_snapshots_timestamp ON finops_snapshots(timestamp);
        """)
        # Additive migrations for databases created by older versions
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(compliance_resources)")}
        for col, ddl in (("warnings_json", "TEXT"), ("resource_type", "TEXT"), ("never_tagged", "INTEGER DEFAULT 0")):
            if col not in cols:
                conn.execute(f"ALTER TABLE compliance_resources ADD COLUMN {col} {ddl}")
        conn.executescript("""
            -- Per-scan aggregates for leaderboards (kept HISTORY_RETENTION_DAYS, unlike resource rows)
            CREATE TABLE IF NOT EXISTS scan_group_stats (
                scan_id INTEGER NOT NULL,
                dimension TEXT NOT NULL,
                key TEXT NOT NULL,
                total INTEGER NOT NULL,
                compliant INTEGER NOT NULL,
                PRIMARY KEY (scan_id, dimension, key)
            );
            CREATE INDEX IF NOT EXISTS idx_group_stats_dim ON scan_group_stats(dimension, scan_id);
            CREATE INDEX IF NOT EXISTS idx_compliance_resources_arn ON compliance_resources(arn);

            -- Undoable tag change sets (bulk fixes, propagation)
            CREATE TABLE IF NOT EXISTS change_sets (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                actor TEXT,
                kind TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                summary_json TEXT,
                items_json TEXT NOT NULL,
                undo_of TEXT,
                undone_by_changeset TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_change_sets_created ON change_sets(created_at);

            -- Latest tag-propagation findings
            CREATE TABLE IF NOT EXISTS propagation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                findings_json TEXT NOT NULL
            );
        """)
    logger.info("Database initialized at %s", DB_PATH)

def insert_scan(report: Dict[str, Any], status: str = "COMPLETED") -> int:
    """Insert a compliance scan and its resources into the database."""
    conn = get_connection()
    
    timestamp = report.get("timestamp") or datetime.now(timezone.utc).isoformat()
    regions_list = list(report.get("regions", {}).keys())
    regions_str = json.dumps(regions_list)
    summary_str = json.dumps(report.get("summary", {}))
    
    with conn:
        cursor = conn.execute("""
            INSERT INTO compliance_scans (timestamp, regions, summary_json, status)
            VALUES (?, ?, ?, ?)
        """, (timestamp, regions_str, summary_str, status))
        scan_id = cursor.lastrowid
        
        resources_to_insert = []
        for region, region_data in report.get("regions", {}).items():
            if region_data.get("error"):
                continue
                
            for res in region_data.get("resources", []):
                arn = res.get("ResourceARN", "")
                parts = arn.split(":")
                res_type = parts[2] if len(parts) > 2 else "unknown"
                account = parts[4] if len(parts) > 4 else "unknown"
                is_compliant = res.get("IsCompliant", False)
                violations = json.dumps(res.get("Violations", []))
                warnings = json.dumps(res.get("Warnings", []))
                tags = json.dumps(res.get("Tags", {}))
                
                resources_to_insert.append((
                    scan_id, arn, region, account, res_type,
                    is_compliant, violations, tags, warnings,
                    res.get("ResourceType"), int(bool(res.get("NeverTagged"))),
                ))
        
        if resources_to_insert:
            conn.executemany("""
                INSERT INTO compliance_resources (scan_id, arn, region, account, type, is_compliant, violations_json, tags_json, warnings_json, resource_type, never_tagged)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, resources_to_insert)

        stats = group_stats(report)
        if stats:
            conn.executemany(
                "INSERT OR REPLACE INTO scan_group_stats (scan_id, dimension, key, total, compliant) VALUES (?,?,?,?,?)",
                [(scan_id, dim, key, v[0], v[1]) for (dim, key), v in stats.items()])

    prune_scans()
    return scan_id


def team_of(tags: Dict[str, str]) -> str:
    """Team for leaderboards: TEAM_TAG_KEY, else OWNER_TAG_KEY (case-insensitive keys)."""
    from src.config import OWNER_TAG_KEY, TEAM_TAG_KEY
    lowered = {str(k).lower(): v for k, v in (tags or {}).items()}
    for key in (TEAM_TAG_KEY, OWNER_TAG_KEY):
        val = str(lowered.get(key.lower(), "")).strip()
        if val:
            return val
    return "(no team)"


def group_stats(report: Dict[str, Any]) -> Dict[tuple, list]:
    """(dimension, key) -> [total, compliant] for account, team and service."""
    stats: Dict[tuple, list] = {}

    def add(dim: str, key: str, ok: bool) -> None:
        v = stats.setdefault((dim, key or "unknown"), [0, 0])
        v[0] += 1
        v[1] += int(ok)

    for region_data in report.get("regions", {}).values():
        if region_data.get("error"):
            continue
        for res in region_data.get("resources", []):
            arn = res.get("ResourceARN", "")
            parts = arn.split(":")
            account = parts[4] if len(parts) > 4 and parts[4] else "unknown"
            ok = bool(res.get("IsCompliant"))
            add("account", account, ok)  # OU is derived from account stats at query time
            add("team", team_of(res.get("Tags", {})), ok)
            add("service", parts[2] if len(parts) > 2 else "unknown", ok)
    return stats


def prune_scans(keep: Optional[int] = None) -> int:
    """
    Two-tier retention:
      * per-resource rows are kept for the newest `keep` (SCAN_RETENTION) scans;
      * scan summaries and leaderboard stats are kept HISTORY_RETENTION_DAYS (trends, week-over-week).
    Returns the number of scans whose resource rows were pruned.
    """
    from datetime import timedelta
    from src.config import HISTORY_RETENTION_DAYS, SCAN_RETENTION
    keep = keep or SCAN_RETENTION
    conn = get_connection()
    with conn:
        old_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM compliance_scans ORDER BY id DESC LIMIT -1 OFFSET ?", (keep,))]
        if old_ids:
            marks = ",".join("?" * len(old_ids))
            conn.execute(f"DELETE FROM compliance_resources WHERE scan_id IN ({marks})", old_ids)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat()
        expired = [r["id"] for r in conn.execute(
            "SELECT id FROM compliance_scans WHERE timestamp < ? AND id NOT IN "
            "(SELECT id FROM compliance_scans ORDER BY id DESC LIMIT 1)", (cutoff,))]
        if expired:
            marks = ",".join("?" * len(expired))
            conn.execute(f"DELETE FROM compliance_resources WHERE scan_id IN ({marks})", expired)
            conn.execute(f"DELETE FROM scan_group_stats WHERE scan_id IN ({marks})", expired)
            conn.execute(f"DELETE FROM compliance_scans WHERE id IN ({marks})", expired)
    return len(old_ids)


def scan_resource_ids_with_rows() -> List[int]:
    conn = get_connection()
    return [r[0] for r in conn.execute("SELECT DISTINCT scan_id FROM compliance_resources")]


def get_group_stats(dimension: str, scan_id: int) -> Dict[str, tuple]:
    conn = get_connection()
    return {r["key"]: (r["total"], r["compliant"]) for r in conn.execute(
        "SELECT key, total, compliant FROM scan_group_stats WHERE dimension = ? AND scan_id = ?",
        (dimension, scan_id))}


def find_scan_before(timestamp_iso: str) -> Optional[Dict[str, Any]]:
    """Most recent completed scan at or before `timestamp_iso`."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, timestamp FROM compliance_scans WHERE status='COMPLETED' AND timestamp <= ? "
        "ORDER BY timestamp DESC LIMIT 1", (timestamp_iso,)).fetchone()
    return {"id": row["id"], "timestamp": row["timestamp"]} if row else None


def latest_scan() -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute(
        "SELECT id, timestamp FROM compliance_scans WHERE status='COMPLETED' ORDER BY id DESC LIMIT 1").fetchone()
    return {"id": row["id"], "timestamp": row["timestamp"]} if row else None


def get_scan_history(limit: int = 30) -> List[Dict[str, Any]]:
    """Summary of the most recent completed scans, oldest first (for trend charts)."""
    if not DB_PATH.exists():
        return []
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT id, timestamp, regions, summary_json FROM compliance_scans
            WHERE status = 'COMPLETED' ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in reversed(rows):
        summary = json.loads(r["summary_json"] or "{}")
        out.append({
            "scan_id": r["id"],
            "timestamp": r["timestamp"],
            "regions": json.loads(r["regions"] or "[]"),
            "total_resources": summary.get("total_resources", 0),
            "compliant": summary.get("compliant", 0),
            "non_compliant": summary.get("non_compliant", 0),
            "compliance_score": summary.get("compliance_score", 0.0),
        })
    return out

def get_latest_scan_report() -> Optional[Dict[str, Any]]:
    """Reconstruct a report dictionary from the latest successful database scan."""
    # Ensure DB is initialized before querying (handles first-time setup or deleted db)
    if not DB_PATH.exists():
        return None
    
    conn = get_connection()
    
    try:
        cursor = conn.execute("""
            SELECT id, timestamp, regions, summary_json 
            FROM compliance_scans 
            WHERE status = 'COMPLETED'
            ORDER BY id DESC LIMIT 1
        """)
        scan_row = cursor.fetchone()
        
        if not scan_row:
            return None
            
        scan_id = scan_row["id"]
        timestamp = scan_row["timestamp"]
        regions = json.loads(scan_row["regions"])
        summary = json.loads(scan_row["summary_json"])
        
        # Reconstruct report structure
        report = {
            "timestamp": timestamp,
            "regions": {r: {"resources": [], "error": None} for r in regions},
            "summary": summary
        }
        
        res_cursor = conn.execute("""
            SELECT arn, region, is_compliant, violations_json, tags_json, warnings_json, resource_type, never_tagged
            FROM compliance_resources 
            WHERE scan_id = ?
        """, (scan_id,))
        
        for res_row in res_cursor:
            region = res_row["region"]
            if region not in report["regions"]:
                report["regions"][region] = {"resources": [], "error": None}
                
            # Try to match the original structure tag_report.py generates
            res_info = {
                "ResourceARN": res_row["arn"],
                "IsCompliant": bool(res_row["is_compliant"]),
                "Violations": json.loads(res_row["violations_json"]) if res_row["violations_json"] else [],
                "Tags": json.loads(res_row["tags_json"]) if res_row["tags_json"] else {},
                "Warnings": json.loads(res_row["warnings_json"]) if res_row["warnings_json"] else [],
                "ResourceType": res_row["resource_type"],
                "NeverTagged": bool(res_row["never_tagged"]),
            }
            report["regions"][region]["resources"].append(res_info)
            
        return report
    except sqlite3.OperationalError:
        # Tables might not exist if DB file was just created but not initialized
        return None

def insert_audit_log(actor: str, action: str, resource_arn: str, result: str, details: Dict[str, Any] = None, request_id: str = None) -> int:
    """Insert an audit log entry."""
    conn = get_connection()
    timestamp = datetime.now(timezone.utc).isoformat()
    details_str = json.dumps(details) if details else None
    
    with conn:
        cursor = conn.execute("""
            INSERT INTO audit_log (timestamp, actor, action, resource_arn, result, details_json, request_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, actor, action, resource_arn, result, details_str, request_id))
        return cursor.lastrowid

def get_recent_audit_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve recent audit logs."""
    if not DB_PATH.exists():
        return []
        
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT id, timestamp, actor, action, resource_arn, result, details_json, request_id
            FROM audit_log
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        
        results = []
        for row in cursor:
            results.append({
                "id": row["id"],
                "timestamp": row["timestamp"],
                "actor": row["actor"],
                "action": row["action"],
                "resource_arn": row["resource_arn"],
                "result": row["result"],
                "details": json.loads(row["details_json"]) if row["details_json"] else {},
                "request_id": row["request_id"]
            })
        return results
    except sqlite3.OperationalError:
        return []

def insert_finops_snapshot(report: Dict[str, Any], status: str = "COMPLETED", error_msg: Optional[str] = None) -> int:
    """Insert a FinOps snapshot into the database."""
    conn = get_connection()
    timestamp = report.get("timestamp") or datetime.now(timezone.utc).isoformat()
    report_json = json.dumps(report)
    
    with conn:
        cursor = conn.execute("""
            INSERT INTO finops_snapshots (timestamp, report_json, status, error_msg)
            VALUES (?, ?, ?, ?)
        """, (timestamp, report_json, status, error_msg))
        return cursor.lastrowid

def get_latest_finops_snapshot() -> Optional[Dict[str, Any]]:
    """Retrieve the latest successful FinOps snapshot."""
    if not DB_PATH.exists():
        return None
    
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT report_json, timestamp 
            FROM finops_snapshots 
            WHERE status = 'COMPLETED'
            ORDER BY id DESC LIMIT 1
        """)
        row = cursor.fetchone()
        
        if not row:
            return None
            
        report = json.loads(row["report_json"])
        report["timestamp"] = row["timestamp"]
        return report
    except sqlite3.OperationalError:
        return None
