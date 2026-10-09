import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from datetime import datetime, timezone

from src.db import init_db, insert_scan, get_latest_scan_report, get_connection, DB_PATH
from src.cache_manager import get_cached_report, save_report, is_cache_stale, background_refresh, _migrate_from_json, CACHE_FILE, _db_initialized


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Ensure tests run against a temporary database and JSON cache file."""
    db_file = tmp_path / "test.db"
    json_file = tmp_path / ".compliance_cache.json"
    
    monkeypatch.setattr("src.db.DB_PATH", db_file)
    monkeypatch.setattr("src.cache_manager.CACHE_FILE", json_file)
    monkeypatch.setattr("src.cache_manager.DB_PATH", db_file)
    
    # Reset globals
    monkeypatch.setattr("src.cache_manager._db_initialized", False)
    import src.cache_manager
    src.cache_manager._db_initialized = False
    
    # Reset connection cache
    import src.db
    src.db._local = threading.local()
    
    yield
    
    # Cleanup DB connection (WAL lock files etc)
    if hasattr(src.db._local, "conn"):
        src.db._local.conn.close()


def test_save_and_load_scan():
    """Test saving a scan and retrieving it."""
    init_db()
    
    mock_report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {"total_resources": 1, "compliant_resources": 0, "non_compliant_resources": 1},
        "regions": {
            "us-east-1": {
                "error": None,
                "resources": [
                    {
                        "ResourceARN": "arn:aws:ec2:us-east-1:123456789012:instance/i-12345",
                        "IsCompliant": False,
                        "Violations": ["Missing Owner"],
                        "Tags": {"Environment": "dev"}
                    }
                ]
            }
        }
    }
    
    scan_id = insert_scan(mock_report)
    assert scan_id == 1
    
    loaded_report = get_latest_scan_report()
    assert loaded_report is not None
    assert loaded_report["timestamp"] == mock_report["timestamp"]
    assert loaded_report["summary"] == mock_report["summary"]
    
    loaded_resources = loaded_report["regions"]["us-east-1"]["resources"]
    assert len(loaded_resources) == 1
    assert loaded_resources[0]["ResourceARN"] == "arn:aws:ec2:us-east-1:123456789012:instance/i-12345"
    assert loaded_resources[0]["IsCompliant"] is False
    assert loaded_resources[0]["Violations"] == ["Missing Owner"]
    assert loaded_resources[0]["Tags"] == {"Environment": "dev"}


def test_scan_history_and_persistence():
    """Test that multiple scans are recorded and only the latest is returned."""
    init_db()
    
    report1 = {
        "timestamp": "2026-10-01T10:00:00+00:00",
        "summary": {"total_resources": 1},
        "regions": {"us-east-1": {"error": None, "resources": []}}
    }
    insert_scan(report1)
    
    report2 = {
        "timestamp": "2026-10-02T10:00:00+00:00",
        "summary": {"total_resources": 2},
        "regions": {"us-east-2": {"error": None, "resources": []}}
    }
    insert_scan(report2)
    
    loaded_report = get_latest_scan_report()
    assert loaded_report is not None
    assert loaded_report["timestamp"] == "2026-10-02T10:00:00+00:00"
    
    with get_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM compliance_scans")
        count = cursor.fetchone()[0]
        assert count == 2


def test_json_migration(tmp_path, monkeypatch):
    """Test migration from .compliance_cache.json to SQLite."""
    json_file = tmp_path / ".compliance_cache.json"
    
    # Create legacy JSON file
    old_report = {
        "summary": {"total_resources": 5},
        "regions": {},
        "_meta": {
            "generated_at": 1696340000.0,
            "status": "Fresh"
        }
    }
    json_file.write_text(json.dumps(old_report))
    
    init_db()
    _migrate_from_json()
    
    # Verify migration worked
    loaded_report = get_latest_scan_report()
    assert loaded_report is not None
    assert loaded_report["summary"]["total_resources"] == 5
    assert json_file.exists()  # JSON file should be preserved
    
    # Second migration should not duplicate
    _migrate_from_json()
    with get_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM compliance_scans")
        assert cursor.fetchone()[0] == 1


def test_missing_database_handling():
    """Test behavior when DB doesn't exist yet."""
    # DB not initialized
    report = get_latest_scan_report()
    assert report is None
    
    # Calling public API should initialize it
    cached = get_cached_report()
    assert cached is None
    
    # Save report should work
    saved = save_report({"summary": {"total_resources": 3}, "regions": {}})
    assert saved["_meta"]["status"] == "Fresh"


def test_concurrent_access():
    """Test concurrent reads and writes to DB using background refresh mechanism."""
    init_db()
    
    mock_report = {
        "summary": {"total_resources": 1},
        "regions": {"us-east-1": {"error": None, "resources": []}}
    }
    insert_scan(mock_report)
    
    # Simulate background write
    def write_worker():
        try:
            report2 = {
                "summary": {"total_resources": 2},
                "regions": {"us-east-1": {"error": None, "resources": []}}
            }
            save_report(report2)
        except Exception as e:
            print(f"Write error: {e}")
            
    # Simulate foreground read
    def read_worker():
        try:
            for _ in range(5):
                get_cached_report()
                time.sleep(0.01)
        except Exception as e:
            print(f"Read error: {e}")

    t1 = threading.Thread(target=write_worker)
    t2 = threading.Thread(target=read_worker)
    t3 = threading.Thread(target=read_worker)
    
    t1.start()
    t2.start()
    t3.start()
    
    t1.join()
    t2.join()
    t3.join()
    
    # Verify final state is consistent
    report = get_latest_scan_report()
    assert report is not None
    assert report["summary"]["total_resources"] == 2
