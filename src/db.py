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
        if "warnings_json" not in cols:
            conn.execute("ALTER TABLE compliance_resources ADD COLUMN warnings_json TEXT")
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
                    is_compliant, violations, tags, warnings
                ))
        
        if resources_to_insert:
            conn.executemany("""
                INSERT INTO compliance_resources (scan_id, arn, region, account, type, is_compliant, violations_json, tags_json, warnings_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, resources_to_insert)

    prune_scans()
    return scan_id


def prune_scans(keep: Optional[int] = None) -> int:
    """Delete all but the newest `keep` scans (and their resources). Returns rows deleted."""
    from src.config import SCAN_RETENTION
    keep = keep or SCAN_RETENTION
    conn = get_connection()
    with conn:
        old_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM compliance_scans ORDER BY id DESC LIMIT -1 OFFSET ?", (keep,))]
        if not old_ids:
            return 0
        marks = ",".join("?" * len(old_ids))
        conn.execute(f"DELETE FROM compliance_resources WHERE scan_id IN ({marks})", old_ids)
        conn.execute(f"DELETE FROM compliance_scans WHERE id IN ({marks})", old_ids)
    return len(old_ids)


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
            SELECT arn, region, is_compliant, violations_json, tags_json, warnings_json
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
