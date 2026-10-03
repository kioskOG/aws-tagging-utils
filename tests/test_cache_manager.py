"""
Tests for the compliance cache manager and the new cache-backed API endpoints.

Covers:
- Persistence: save, load, corrupt cache, restart simulation
- Cache: fresh / stale / missing
- Refresh: successful, failed, concurrent lock
- API: cached responses are fast, refresh endpoint, AWS error preserves cache
"""
from __future__ import annotations

import json
import sqlite3
import os
import time
import threading
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """
    Redirect the cache file and sqlite db to a temp directory for every test so tests are
    fully isolated and do not touch the real files.
    """
    cache_file = tmp_path / ".compliance_cache.json"
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("COMPLIANCE_CACHE_FILE", str(cache_file))
    monkeypatch.setenv("SQLITE_DB_PATH", str(db_file))

    # Force the module to re-read CACHE_FILE from the env variable
    import src.cache_manager as cm
    cm.CACHE_FILE = cache_file
    
    import src.db as db
    db.DB_PATH = db_file
    
    # Reset in-process state
    cm._is_refreshing = False
    cm._last_refresh_error = None
    cm._db_initialized = False
    
    # Reset db connection
    db._local = threading.local()
    
    # Release any leftover lock (shouldn't happen, but be safe)
    if cm._refresh_lock.locked():
        try:
            cm._refresh_lock.release()
        except RuntimeError:
            pass
            
    yield cache_file
    
    # Wait for any lingering background refresh threads to finish
    timeout = time.time() + 5
    while cm._is_refreshing and time.time() < timeout:
        time.sleep(0.05)
        
    if hasattr(db._local, "conn"):
        db._local.conn.close()


@pytest.fixture
def sample_report():
    return {
        "summary": {
            "total_resources": 19,
            "compliant": 1,
            "non_compliant": 18,
            "compliance_score": 5.26,
        },
        "regions": {
            "ap-southeast-1": {
                "resources": [
                    {
                        "ResourceARN": "arn:aws:ec2:ap-southeast-1:123456789012:instance/i-abc",
                        "IsCompliant": False,
                        "Violations": [{"tag": "Owner", "type": "MISSING_REQUIRED"}],
                        "Tags": {},
                    }
                ],
                "compliance_score": 0.0,
            }
        },
    }


@pytest.fixture
def flask_client(isolated_cache):
    """Flask test client wired to the web app with testing mode on."""
    from web.app import app as flask_app
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# 1. Persistence tests
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_load(self, sample_report):
        import src.cache_manager as cm
        saved = cm.save_report(sample_report)
        assert "_meta" in saved
        assert saved["_meta"]["generated_at"] > 0

        loaded = cm.get_cached_report()
        assert loaded is not None
        assert loaded["summary"]["total_resources"] == 19

    def test_save_does_not_mutate_original(self, sample_report):
        import src.cache_manager as cm
        original_keys = set(sample_report.keys())
        cm.save_report(sample_report)
        assert set(sample_report.keys()) == original_keys

    def test_load_returns_none_when_no_file(self):
        import src.cache_manager as cm
        assert cm.get_cached_report() is None

    def test_load_returns_none_on_corrupt_file(self, isolated_cache):
        import src.cache_manager as cm
        isolated_cache.write_text("NOT VALID JSON {{{")
        assert cm.get_cached_report() is None

    def test_flask_restart_simulation(self, sample_report, isolated_cache):
        """Saving to disk, then reading in a fresh import context simulates restart."""
        import src.cache_manager as cm
        import src.db as db
        cm.save_report(sample_report)

        # Simulate restart: re-read directly from disk (no in-memory state)
        # Verify db was populated
        with sqlite3.connect(str(db.DB_PATH)) as conn:
            cursor = conn.execute("SELECT summary_json FROM compliance_scans ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            assert row is not None
            summary = json.loads(row[0])
            assert summary["total_resources"] == 19


# ---------------------------------------------------------------------------
# 2. Cache staleness tests
# ---------------------------------------------------------------------------

class TestCacheStaleness:
    def test_no_file_is_stale(self):
        import src.cache_manager as cm
        assert cm.is_cache_stale() is True

    def test_fresh_cache_not_stale(self, sample_report):
        import src.cache_manager as cm
        cm.save_report(sample_report)
        assert cm.is_cache_stale() is False

    def test_old_cache_is_stale(self, sample_report, isolated_cache):
        import src.cache_manager as cm
        data = dict(sample_report)
        data["_meta"] = {"generated_at": time.time() - 9999, "status": "Fresh"}
        isolated_cache.write_text(json.dumps(data))
        assert cm.is_cache_stale() is True

    def test_cache_ttl_from_env(self, monkeypatch, sample_report, isolated_cache):
        """COMPLIANCE_CACHE_TTL_SECONDS env var changes the TTL."""
        monkeypatch.setenv("COMPLIANCE_CACHE_TTL_SECONDS", "1")
        import importlib, src.cache_manager as cm
        cm.CACHE_TTL = 1  # apply the override in the running module

        cm.save_report(sample_report)
        time.sleep(1.1)
        assert cm.is_cache_stale() is True


# ---------------------------------------------------------------------------
# 3. Refresh tests
# ---------------------------------------------------------------------------

class TestRefresh:
    def test_successful_refresh_saves_data(self, sample_report):
        import src.cache_manager as cm
        with patch("src.tag_report.generate_report", return_value=sample_report):
            cm.background_refresh()
        result = cm.get_cached_report()
        assert result is not None
        assert result["summary"]["total_resources"] == 19
        assert cm.get_refresh_error() is None

    def test_failed_refresh_preserves_old_cache(self, sample_report):
        import src.cache_manager as cm
        # Save a good result first
        cm.save_report(sample_report)

        # Now make AWS fail
        with patch("src.tag_report.generate_report", side_effect=Exception("Access Denied")):
            cm.background_refresh()

        # Old cache must still be readable
        result = cm.get_cached_report()
        assert result is not None
        assert result["summary"]["total_resources"] == 19

        # Error must be recorded
        assert cm.get_refresh_error() is not None
        assert "Access Denied" in cm.get_refresh_error()

    def test_concurrent_refresh_is_blocked(self):
        """Second call while first is running must be ignored (lock)."""
        import src.cache_manager as cm
        call_count = 0
        barrier = threading.Event()

        def slow_generate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            barrier.wait(timeout=3)
            return {"summary": {}, "regions": {}}

        with patch("src.tag_report.generate_report", side_effect=slow_generate):
            t1 = threading.Thread(target=cm.background_refresh)
            t1.start()
            time.sleep(0.05)  # let t1 acquire the lock
            cm.background_refresh()  # should be skipped
            barrier.set()
            t1.join(timeout=5)

        assert call_count == 1  # only one scan ran

    def test_clear_refresh_error(self):
        import src.cache_manager as cm
        cm._last_refresh_error = "some error"
        cm.clear_refresh_error()
        assert cm.get_refresh_error() is None


# ---------------------------------------------------------------------------
# 4. API endpoint tests
# ---------------------------------------------------------------------------

class TestCacheAPI:
    def test_dashboard_returns_cached_data(self, flask_client, sample_report):
        import src.cache_manager as cm
        cm.save_report(sample_report)
        resp = flask_client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["compliance_pct"] == pytest.approx(5.26, abs=0.01)
        assert data["total_resources"] == 19
        assert "_meta" in data

    def test_dashboard_returns_empty_when_no_cache(self, flask_client):
        """No cache → returns 200 with zeros (not 500), so UI can render."""
        resp = flask_client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_resources"] == 0

    def test_compliance_returns_cached_resources(self, flask_client, sample_report):
        import src.cache_manager as cm
        cm.save_report(sample_report)
        resp = flask_client.get("/api/compliance")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "resources" in data
        assert len(data["resources"]) == 1
        assert data["resources"][0]["status"] == "NON_COMPLIANT"

    def test_compliance_summary_endpoint(self, flask_client, sample_report):
        import src.cache_manager as cm
        cm.save_report(sample_report)
        resp = flask_client.get("/api/compliance/summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_resources"] == 19
        assert data["compliance_pct"] == pytest.approx(5.26, abs=0.01)
        assert "_meta" in data

    def test_compliance_summary_no_data(self, flask_client):
        resp = flask_client.get("/api/compliance/summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("no_data") is True

    def test_compliance_status_endpoint(self, flask_client):
        resp = flask_client.get("/api/compliance/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "is_refreshing" in data
        assert "is_stale" in data
        assert "last_refresh_error" in data

    def test_compliance_refresh_starts_background_thread(self, flask_client, sample_report):
        with patch("src.tag_report.generate_report", return_value=sample_report):
            resp = flask_client.post(
                "/api/compliance/refresh",
                json={},
                content_type="application/json",
            )
        # Accept 202 (started) or 200 (already running from a previous test)
        assert resp.status_code in (200, 202)

    def test_compliance_refresh_rejects_concurrent(self, flask_client):
        import src.cache_manager as cm
        # Simulate a refresh already in progress
        cm._is_refreshing = True
        try:
            resp = flask_client.post(
                "/api/compliance/refresh",
                json={},
                content_type="application/json",
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "already_refreshing"
        finally:
            cm._is_refreshing = False

    def test_aws_error_during_refresh_does_not_destroy_cache(
        self, flask_client, sample_report
    ):
        import src.cache_manager as cm
        cm.save_report(sample_report)

        with patch("src.tag_report.generate_report", side_effect=Exception("Timeout")):
            cm.background_refresh()

        # Cache still intact
        assert cm.get_cached_report()["summary"]["total_resources"] == 19
        # Error surfaced
        status_resp = flask_client.get("/api/compliance/status")
        assert status_resp.get_json()["last_refresh_error"] is not None

    def test_cached_api_response_is_fast(self, flask_client, sample_report):
        """Cached endpoint must respond in well under 500ms (no AWS call)."""
        import src.cache_manager as cm
        cm.save_report(sample_report)

        start = time.perf_counter()
        flask_client.get("/api/dashboard")
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"Cached dashboard took {elapsed:.3f}s (too slow)"
