import pytest
from unittest.mock import patch, MagicMock
from flask import json
from web.app import app
from src.errors import APIError
from botocore.exceptions import ClientError, BotoCoreError, NoCredentialsError

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_api_error_format(client):
    """Every API error contains error_code, message, request_id and X-Request-ID."""
    with patch("web.app.get_cached_report", side_effect=APIError("Test message", status_code=400, error_code="TEST_ERROR")):
        response = client.get("/api/dashboard")
        assert response.status_code == 400
        
        # Header check
        req_id = response.headers.get("X-Request-ID")
        assert req_id is not None
        
        # Body check
        data = response.get_json()
        assert data["error_code"] == "TEST_ERROR"
        assert data["message"] == "Test message"
        assert data["request_id"] == req_id

def test_incoming_request_id_preserved(client):
    """Incoming request ID behavior is preserved."""
    with patch("web.app.get_cached_report", side_effect=APIError("Test")):
        headers = {"X-Request-ID": "my-custom-uuid"}
        response = client.get("/api/dashboard", headers=headers)
        
        assert response.headers.get("X-Request-ID") == "my-custom-uuid"
        assert response.get_json()["request_id"] == "my-custom-uuid"

def test_aws_credentials_mapping(client):
    """Missing *server* AWS credentials are a 503, never a 401 (which would prompt the user to re-login)."""
    with patch("web.app.get_cached_report", side_effect=NoCredentialsError()):
        response = client.get("/api/dashboard")
        assert response.status_code == 503
        data = response.get_json()
        assert data["error_code"] == "INVALID_CREDENTIALS"
        assert "credentials" in data["message"].lower()

def test_aws_access_denied_mapping(client):
    """Access denied maps correctly."""
    error_response = {'Error': {'Code': 'AccessDeniedException', 'Message': 'Access Denied'}}
    with patch("web.app.get_cached_report", side_effect=ClientError(error_response, 'operation')):
        response = client.get("/api/dashboard")
        assert response.status_code == 403
        data = response.get_json()
        assert data["error_code"] == "ACCESS_DENIED"
        assert "access denied" in data["message"].lower()

def test_aws_throttling_mapping(client):
    """Throttling maps correctly."""
    error_response = {'Error': {'Code': 'ThrottlingException', 'Message': 'Rate exceeded'}}
    with patch("web.app.get_cached_report", side_effect=ClientError(error_response, 'operation')):
        response = client.get("/api/dashboard")
        assert response.status_code == 429
        data = response.get_json()
        assert data["error_code"] == "THROTTLED"

def test_unexpected_exception(client, caplog):
    """Unexpected exception returns safe 500, no internal details exposed, and logs are sanitized."""
    with patch("web.app.get_cached_report", side_effect=ValueError("Secret database password is SUPER_SECRET_123")):
        response = client.get("/api/dashboard")
        assert response.status_code == 500
        data = response.get_json()
        assert data["error_code"] == "INTERNAL_ERROR"
        assert "unexpected error" in data["message"].lower()
        assert "password123" not in data["message"]
        assert "SUPER_SECRET_123" not in data["message"]
        
        # Verify log sanitization
        log_text = caplog.text
        assert "Unexpected API Error of type ValueError" in log_text
        assert "SUPER_SECRET_123" not in log_text
        assert "database" not in log_text # because the entire exception string was redacted
        assert "<redacted for security>" in log_text

def test_successful_responses_unchanged(client):
    """Existing successful responses remain unchanged."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert response.headers.get("X-Request-ID") is not None

def test_finops_not_ready_not_converted(client):
    """FinOps 202/not_ready is NOT accidentally converted into an error response."""
    with patch("web.app.finops_get_cached_report", return_value=None), \
         patch("web.app.finops_is_refreshing", return_value=True), \
         patch("web.app.finops_is_cache_stale", return_value=False):
        response = client.get("/api/finops")
        assert response.status_code == 202
        data = response.get_json()
        # FinOps not_ready returns "error": "not_ready", it is an API envelope behavior we must preserve
        assert data["error"] == "not_ready"
        assert data.get("error_code") is None

def test_lambda_error_mapping(client):
    """Test that lambda 400 responses are mapped properly in _lambda_result_to_response."""
    mock_payload = {"resource": ""}
    with patch("web.app.read_handler", return_value={"statusCode": 400, "body": {"message": "Resource type is required"}}):
        response = client.post("/api/read", json=mock_payload)
        assert response.status_code == 400
        data = response.get_json()
        assert data["error_code"] == "VALIDATION_ERROR"
        assert data["message"] == "Resource type is required"
        assert "request_id" in data
