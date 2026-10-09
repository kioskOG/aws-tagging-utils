"""
Tests for FinOps Caching and API endpoints.
"""
import json
import time
import threading
import sqlite3
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

@pytest.fixture(autouse=True)
def isolated_finops_cache(tmp_path, monkeypatch):
    """
    Redirect the SQLite db to a temp directory for every test so tests are
    fully isolated and do not touch the real files.
    """
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(db_file))
    monkeypatch.setenv("FINOPS_CACHE_TTL_SECONDS", "86400") # 24 hrs
    monkeypatch.setenv("FINOPS_PRIMARY_TAG", "CostCenter")
    
    import src.finops.cache_manager as fcm
    import src.db as db
    
    db.DB_PATH = db_file
    
    # Reset in-process state
    fcm._is_refreshing = False
    fcm._last_refresh_error = None
    fcm._db_initialized = False
    
    # Reset db connection
    db._local = threading.local()
    
    # Release any leftover lock
    if fcm._refresh_lock.locked():
        try:
            fcm._refresh_lock.release()
        except RuntimeError:
            pass
            
    yield db_file
    
    # Wait for any lingering background refresh threads to finish
    timeout = time.time() + 5
    while fcm._is_refreshing and time.time() < timeout:
        time.sleep(0.05)
        
    if hasattr(db._local, "conn"):
        db._local.conn.close()


@pytest.fixture
def flask_client():
    """Flask test client wired to the web app with testing mode on."""
    from web.app import app as flask_app
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        yield client

@pytest.fixture
def mock_ce_response():
    return {
        'ResultsByTime': [{'Total': {'UnblendedCost': {'Amount': '125400.00'}}}]
    }
    
@pytest.fixture
def mock_tag_response():
    return {
        'ResultsByTime': [
            {
                'Groups': [
                    {'Keys': ['CostCenter$Engineering'], 'Metrics': {'UnblendedCost': {'Amount': '100000.00'}}},
                    {'Keys': ['CostCenter$'], 'Metrics': {'UnblendedCost': {'Amount': '25400.00'}}}
                ]
            }
        ]
    }

def test_no_cache_behavior(flask_client, mock_ce_response, mock_tag_response):
    """When no cache exists, GET /api/finops should return 202 and NOT block for CE."""
    import src.finops.cache_manager as fcm
    
    with patch("src.finops.cost_explorer.CostExplorerClient.get_cost_and_usage", return_value=mock_ce_response) as mock_ce, \
         patch("src.finops.cost_explorer.CostExplorerClient.get_cost_by_tag", return_value=mock_tag_response) as mock_tag:
        
        start = time.time()
        resp = flask_client.get("/api/finops")
        end = time.time()
        
        assert end - start < 0.1 # Should be very fast, no waiting
        assert resp.status_code == 202
        data = resp.get_json()
        assert data["error"] == "not_ready"
        
        # Wait for thread to start and finish
        time.sleep(0.2)
        timeout = time.time() + 5
        while fcm._is_refreshing and time.time() < timeout:
            time.sleep(0.05)
            
        mock_ce.assert_called_once()
        mock_tag.assert_called_once()
        
        # Next call should return 200 with data
        resp2 = flask_client.get("/api/finops")
        assert resp2.status_code == 200
        assert resp2.get_json()["TotalSpend"] == 125400.0

def test_fresh_cache_hit(flask_client, mock_ce_response, mock_tag_response):
    """Fresh cache should return immediately without triggering background refresh."""
    import src.finops.cache_manager as fcm
    
    # Pre-populate cache
    with patch("src.finops.cost_explorer.CostExplorerClient.get_cost_and_usage", return_value=mock_ce_response), \
         patch("src.finops.cost_explorer.CostExplorerClient.get_cost_by_tag", return_value=mock_tag_response):
        fcm.background_refresh()
        
    with patch("src.finops.cost_explorer.CostExplorerClient.get_cost_and_usage") as mock_ce:
        resp = flask_client.get("/api/finops")
        assert resp.status_code == 200
        assert resp.get_json()["TotalSpend"] == 125400.0
        
        # No CE call should be made because cache is fresh
        mock_ce.assert_not_called()

def test_stale_cache_behavior(flask_client, mock_ce_response, mock_tag_response, monkeypatch):
    """Stale cache should return stale data immediately and start background refresh."""
    import src.finops.cache_manager as fcm
    
    # Pre-populate cache
    with patch("src.finops.cost_explorer.CostExplorerClient.get_cost_and_usage", return_value=mock_ce_response), \
         patch("src.finops.cost_explorer.CostExplorerClient.get_cost_by_tag", return_value=mock_tag_response):
        fcm.background_refresh()
        
    # Make the cache stale (TTL is 86400, so pretend time advanced)
    with patch('time.time', return_value=time.time() + 90000):
        # Now it's stale. 
        with patch("src.finops.cost_explorer.CostExplorerClient.get_cost_and_usage", return_value=mock_ce_response) as mock_ce, \
             patch("src.finops.cost_explorer.CostExplorerClient.get_cost_by_tag", return_value=mock_tag_response) as mock_tag:
            resp = flask_client.get("/api/finops")
            
            # Still returns 200 with data!
            assert resp.status_code == 200
            assert resp.get_json()["TotalSpend"] == 125400.0
            
            # Wait for thread to start and finish
            time.sleep(0.2)
            timeout = time.time() + 5
            while fcm._is_refreshing and time.time() < timeout:
                time.sleep(0.05)
            
            mock_ce.assert_called_once()


def test_failed_refresh_preserves_old_cache(flask_client, mock_ce_response, mock_tag_response):
    """A failed background refresh should NOT destroy the existing cache."""
    import src.finops.cache_manager as fcm
    
    with patch("src.finops.cost_explorer.CostExplorerClient.get_cost_and_usage", return_value=mock_ce_response), \
         patch("src.finops.cost_explorer.CostExplorerClient.get_cost_by_tag", return_value=mock_tag_response):
        fcm.background_refresh()
        
    assert fcm.get_cached_report()["TotalSpend"] == 125400.0
    
    # Now simulate a failure
    with patch("src.finops.cost_explorer.CostExplorerClient.get_cost_and_usage", side_effect=Exception("AWS Outage")):
        fcm.background_refresh()
        
    # Error should be recorded
    assert "AWS Outage" in fcm.get_refresh_error()
    
    # Data is preserved
    assert fcm.get_cached_report()["TotalSpend"] == 125400.0


def test_explicit_refresh_endpoint(flask_client, mock_ce_response, mock_tag_response):
    """POST /api/finops/refresh should start a refresh if not running."""
    import src.finops.cache_manager as fcm
    
    with patch("src.finops.cost_explorer.CostExplorerClient.get_cost_and_usage", return_value=mock_ce_response) as mock_ce, \
         patch("src.finops.cost_explorer.CostExplorerClient.get_cost_by_tag", return_value=mock_tag_response):
        
        resp = flask_client.post("/api/finops/refresh")
        assert resp.status_code == 202
        assert resp.get_json()["status"] == "started"
        
        # Second call should say already refreshing
        resp2 = flask_client.post("/api/finops/refresh")
        assert resp2.status_code in [200, 202]
        if resp2.status_code == 200:
            assert resp2.get_json()["status"] == "already_refreshing"
        
        time.sleep(0.2)
        timeout = time.time() + 5
        while fcm._is_refreshing and time.time() < timeout:
            time.sleep(0.05)
            
        mock_ce.assert_called_once()

def test_concurrent_get_requests_only_one_ce_call(flask_client, mock_ce_response, mock_tag_response):
    """10 simultaneous GET requests with no cache should result in exactly ONE AWS call."""
    import src.finops.cache_manager as fcm
    call_count = 0
    barrier = threading.Event()
    
    def slow_ce(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        barrier.wait(timeout=2)
        return mock_ce_response
        
    with patch("src.finops.cost_explorer.CostExplorerClient.get_cost_and_usage", side_effect=slow_ce), \
         patch("src.finops.cost_explorer.CostExplorerClient.get_cost_by_tag", return_value=mock_tag_response):
        
        # We simulate the thread starting directly instead of flask requests 
        # to avoid request context teardown errors across threads
        threads = []
        for _ in range(10):
            t = threading.Thread(target=fcm.background_refresh)
            threads.append(t)
            t.start()
            
        time.sleep(0.2) # Let them all run
        barrier.set()
        
        for t in threads:
            t.join()
            
        timeout = time.time() + 5
        while fcm._is_refreshing and time.time() < timeout:
            time.sleep(0.05)
            
        assert call_count == 1 # Only ONE AWS call made!

def test_flask_restart_persistence(isolated_finops_cache, mock_ce_response, mock_tag_response):
    """Verify that restarting Flask (clearing memory) still loads from DB."""
    import src.finops.cache_manager as fcm
    import src.db as db
    
    with patch("src.finops.cost_explorer.CostExplorerClient.get_cost_and_usage", return_value=mock_ce_response), \
         patch("src.finops.cost_explorer.CostExplorerClient.get_cost_by_tag", return_value=mock_tag_response):
        fcm.background_refresh()
        
    # Simulate restart
    fcm._db_initialized = False
    db._local = threading.local()
    
    report = fcm.get_cached_report()
    assert report is not None
    assert report["TotalSpend"] == 125400.0
