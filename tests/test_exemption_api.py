import pytest
from src.errors import APIError
from web.app import app, exemption_manager

@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()

@pytest.fixture
def auth_headers():
    # Use local_dev auth header for tests if enabled, otherwise mock Identity
    return {"X-User": "testuser", "X-Role": "SecurityAdmin"}

@pytest.fixture
def setup_mock_store(monkeypatch):
    from src.governance.state import MemoryStateStore
    store = MemoryStateStore()
    monkeypatch.setattr(exemption_manager, "state_store", store)
    return store

def test_api_list_exemptions(client, auth_headers, setup_mock_store):
    exemption_manager.create_exemption({"resource_id": "i-1234"}, "testuser")
    res = client.get("/api/exemptions", headers=auth_headers)
    assert res.status_code == 200
    assert len(res.json["exemptions"]) == 1
    assert res.json["exemptions"][0]["resource_id"] == "i-1234"

def test_api_create_exemption(client, auth_headers, setup_mock_store):
    payload = {"resource_type": "ec2", "environment": "prod"}
    res = client.post("/api/exemptions", json=payload, headers=auth_headers)
    assert res.status_code == 201
    assert res.json["resource_type"] == "ec2"
    assert res.json["environment"] == "prod"
    assert res.json["status"] == "ACTIVE"
    
def test_api_create_exemption_conflict(client, auth_headers, setup_mock_store):
    payload = {"resource_id": "i-9999"}
    res1 = client.post("/api/exemptions", json=payload, headers=auth_headers)
    assert res1.status_code == 201
    
    res2 = client.post("/api/exemptions", json=payload, headers=auth_headers)
    assert res2.status_code == 409
    assert res2.json["error_code"] == "EXEMPTION_CONFLICT"

def test_api_create_exemption_unsupported_scope(client, auth_headers, setup_mock_store):
    payload = {"account_id": "111", "resource_type": "ec2"}
    res = client.post("/api/exemptions", json=payload, headers=auth_headers)
    assert res.status_code == 400

def test_api_get_exemption(client, auth_headers, setup_mock_store):
    ex = exemption_manager.create_exemption({"resource_id": "i-1234"}, "testuser")
    res = client.get(f"/api/exemptions/{ex['id']}", headers=auth_headers)
    assert res.status_code == 200
    assert res.json["resource_id"] == "i-1234"

def test_api_delete_exemption(client, auth_headers, setup_mock_store):
    ex = exemption_manager.create_exemption({"resource_id": "i-1234"}, "testuser")
    res = client.delete(f"/api/exemptions/{ex['id']}", headers=auth_headers)
    assert res.status_code == 200
    
    # Check it's revoked
    res_get = client.get(f"/api/exemptions/{ex['id']}", headers=auth_headers)
    assert res_get.status_code == 200
    assert res_get.json["status"] == "REVOKED"
    
    # Check it's not active in evaluator
    assert exemption_manager.is_exempt("111", "ec2", "i-1234") is None

def test_api_delete_not_found(client, auth_headers, setup_mock_store):
    res = client.delete("/api/exemptions/missing-id", headers=auth_headers)
    assert res.status_code == 404

def test_api_unauthorized(client, setup_mock_store, monkeypatch):
    import os
    monkeypatch.setenv("AUTH_MODE", "alb_oidc")
    res = client.get("/api/exemptions")
    assert res.status_code == 401

def test_api_forbidden(client, setup_mock_store, monkeypatch):
    monkeypatch.setenv("DEV_AUTH_USER", "test-viewer:Viewer")
    headers = {"X-User": "testuser", "X-Role": "Viewer"}
    res = client.post("/api/exemptions", json={"resource_id": "i-1"}, headers=headers)
    assert res.status_code == 403
