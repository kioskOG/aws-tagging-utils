import pytest
from unittest.mock import patch
import json
import sqlite3

from web.app import app
import src.db as db

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_audit.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(db_file))
    
    db.DB_PATH = db_file
    db._local = __import__("threading").local()
    db.init_db()
    
    yield db_file
    
    if hasattr(db._local, "conn"):
        db._local.conn.close()

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_audit_successful_write(client):
    """Successful AWS mock -> audit row exists"""
    payload = {
        "arn": "arn:aws:ec2:us-east-1:123:instance/i-123",
        "tags": {"Owner": "team-a", "Environment": "dev"}
    }
    
    with patch("web.app.write_handler", return_value={"statusCode": 200, "body": {"count": 1}}):
        response = client.post("/api/write", json=payload, headers={"X-Request-ID": "test-req-1"})
        assert response.status_code == 200
        
    audit_resp = client.get("/api/audit")
    assert audit_resp.status_code == 200
    events = audit_resp.get_json()["audit_events"]
    assert len(events) == 1
    
    event = events[0]
    assert event["action"] == "TAG_WRITE"
    assert event["actor"] == "anonymous"
    assert event["resource_arn"] == payload["arn"]
    assert event["result"] == "SUCCESS"
    assert event["request_id"] == "test-req-1"
    assert event["details"]["tags_requested"] == payload["tags"]

def test_audit_generated_request_id(client):
    """Missing X-Request-ID generates one that is saved in audit"""
    payload = {
        "arn": "arn:aws:ec2:us-east-1:123:instance/i-123",
        "tags": {"Environment": "dev"}
    }
    
    with patch("web.app.write_handler", return_value={"statusCode": 200, "body": {"count": 1}}):
        response = client.post("/api/write", json=payload)
        assert response.status_code == 200
        
    req_id = response.headers.get("X-Request-ID")
    assert req_id is not None
    
    audit_resp = client.get("/api/audit")
    events = audit_resp.get_json()["audit_events"]
    assert len(events) == 1
    assert events[0]["request_id"] == req_id

def test_audit_multiple_resources(client):
    """Writes audit for each resource"""
    payload = {
        "arns": ["arn1", "arn2"],
        "tags": {"Environment": "dev"}
    }
    
    with patch("web.app.write_handler", return_value={"statusCode": 200, "body": {"count": 2}}):
        response = client.post("/api/write", json=payload)
        assert response.status_code == 200
        
    audit_resp = client.get("/api/audit")
    events = audit_resp.get_json()["audit_events"]
    assert len(events) == 2
    arns_audited = {e["resource_arn"] for e in events}
    assert arns_audited == {"arn1", "arn2"}

def test_audit_partial_success(client):
    """Resources mapped correctly to SUCCESS/FAILED based on 207 response"""
    payload = {
        "arns": ["arn_success", "arn_failed"],
        "tags": {"Environment": "dev"}
    }
    
    mock_result = {
        "statusCode": 207,
        "body": {
            "details": {
                "failed_resources": {
                    "arn_failed": {"ErrorCode": "ClientError", "ErrorMessage": "err"}
                }
            }
        }
    }
    
    with patch("web.app.write_handler", return_value=mock_result):
        response = client.post("/api/write", json=payload)
        assert response.status_code == 207
        
    audit_resp = client.get("/api/audit")
    events = audit_resp.get_json()["audit_events"]
    assert len(events) == 2
    for e in events:
        if e["resource_arn"] == "arn_success":
            assert e["result"] == "SUCCESS"
        else:
            assert e["result"] == "FAILED"

def test_audit_query_ordering_and_limit(client):
    """Audit query returns newest first and respects limit"""
    # Insert multiple
    for i in range(5):
        db.insert_audit_log(
            actor="anonymous", action="TAG_WRITE", resource_arn=f"arn{i}", result="SUCCESS"
        )
        
    audit_resp = client.get("/api/audit?limit=2")
    data = audit_resp.get_json()
    events = data["audit_events"]
    assert len(events) == 2
    # newest first
    assert events[0]["resource_arn"] == "arn4"
    assert events[1]["resource_arn"] == "arn3"

def test_audit_failure_isolation(client, caplog):
    """Audit insertion failure does not fail the API response"""
    payload = {
        "arn": "arn:aws:ec2:us-east-1:123:instance/i-123",
        "tags": {"Environment": "dev"}
    }
    
    with patch("web.app.write_handler", return_value={"statusCode": 200, "body": {"count": 1}}):
        with patch("src.db.insert_audit_log", side_effect=Exception("DB Locked")):
            response = client.post("/api/write", json=payload, headers={"X-Request-ID": "test-iso"})
            # Should STILL be 200
            assert response.status_code == 200
            
    # And error should be logged
    assert "Failed to persist audit log: DB Locked" in caplog.text
    # Log should contain the correlation ID (thanks to FlaskRequestIDFilter)

def test_no_secret_leakage(client):
    """Ensure we are not storing the whole request payload, just the requested tags."""
    payload = {
        "arn": "arn:aws:ec2:us-east-1:123:instance/i-123",
        "tags": {"Environment": "dev", "MySecret": "do-not-leak"},
        "super_secret_token": "abc123xyz" # Should never be in the audit!
    }
    
    with patch("web.app.write_handler", return_value={"statusCode": 200, "body": {"count": 1}}):
        response = client.post("/api/write", json=payload)
        assert response.status_code == 200
        
    audit_resp = client.get("/api/audit")
    events = audit_resp.get_json()["audit_events"]
    event = events[0]
    
    details_str = json.dumps(event["details"])
    assert "abc123xyz" not in details_str
    assert "super_secret_token" not in details_str
    # tags_requested can contain the tags since that's what we are auditing
    assert "MySecret" in details_str
