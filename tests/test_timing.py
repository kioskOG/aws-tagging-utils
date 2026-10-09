import pytest
import time
from unittest.mock import patch, MagicMock
from web.app import app
from src.errors import APIError

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_timing_header_normal_response(client):
    """1. Normal API response contains X-Response-Time-Ms. 
       2. Timing header is numeric. 
       3. Timing value is non-negative.
       8. Existing response body remains unchanged."""
    response = client.get("/health")
    assert response.status_code == 200
    
    # Check body remains unchanged
    assert response.get_json() == {"status": "ok"}
    
    # Check header
    header_val = response.headers.get("X-Response-Time-Ms")
    assert header_val is not None
    
    val = float(header_val)
    assert val >= 0.0

def test_timing_header_error_response(client):
    """4. Error response also contains timing header.
       5. Request ID and timing header coexist correctly."""
    with patch("web.app.get_cached_report", side_effect=APIError("Test error")):
        response = client.get("/api/dashboard")
        assert response.status_code == 500
        
        # Check both headers coexist
        req_id = response.headers.get("X-Request-ID")
        time_ms = response.headers.get("X-Response-Time-Ms")
        
        assert req_id is not None
        assert time_ms is not None
        assert float(time_ms) >= 0.0
        
        # Check they exist in JSON response as well for request_id
        data = response.get_json()
        assert data["request_id"] == req_id

def test_slow_request_logs_warning(client, caplog):
    """6. Slow request emits a warning when duration >1 second."""
    # Mock time.perf_counter to return 0.0 then 1.5 to simulate 1500ms
    with patch("web.app.time.perf_counter", side_effect=[0.0, 1.5]):
        response = client.get("/health")
        assert response.status_code == 200
        
        time_ms = response.headers.get("X-Response-Time-Ms")
        assert float(time_ms) == 1500.0
        
        assert "Slow API request" in caplog.text
        assert "duration_ms=1500.00" in caplog.text
        assert "method=GET" in caplog.text
        assert "path=/health" in caplog.text
        assert "status=200" in caplog.text

def test_normal_request_does_not_log_warning(client, caplog):
    """7. Normal request does not emit the slow-request warning."""
    # Mock time.perf_counter to return 0.0 then 0.5 to simulate 500ms
    with patch("web.app.time.perf_counter", side_effect=[0.0, 0.5]):
        response = client.get("/health")
        assert response.status_code == 200
        
        time_ms = response.headers.get("X-Response-Time-Ms")
        assert float(time_ms) == 500.0
        
        assert "Slow API request" not in caplog.text

def test_background_refresh_behavior_unchanged(client):
    """9. Background refresh endpoints still behave correctly."""
    with patch("web.app.finops_is_refreshing", return_value=False), \
         patch("threading.Thread.start") as mock_start:
        response = client.post("/api/finops/refresh")
        assert response.status_code == 202
        assert response.get_json() == {"status": "started"}
        assert response.headers.get("X-Response-Time-Ms") is not None
        mock_start.assert_called_once()
