import pytest
import os
import json
import time
import tempfile
from unittest.mock import patch, MagicMock

import boto3
from moto import mock_aws

from web.app import app


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Redirect cache to tmp_path so tests never touch the real cache file."""
    cache_file = tmp_path / ".compliance_cache.json"
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("COMPLIANCE_CACHE_FILE", str(cache_file))
    monkeypatch.setenv("SQLITE_DB_PATH", str(db_file))
    
    import src.cache_manager as cm
    cm.CACHE_FILE = cache_file
    cm._is_refreshing = False
    cm._last_refresh_error = None
    cm._db_initialized = False
    
    import src.db as db
    db.DB_PATH = db_file
    db._local = __import__("threading").local()
    
    yield cache_file
    
    if hasattr(db._local, "conn"):
        db._local.conn.close()


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


SAMPLE_REPORT = {
    "summary": {
        "total_resources": 10,
        "compliant": 8,
        "non_compliant": 2,
        "compliance_score": 80.0
    },
    "regions": {
        "us-east-1": {
            "resources": [
                {
                    "ResourceARN": "arn:aws:ec2:us-east-1:123456789012:instance/i-1234",
                    "IsCompliant": False,
                    "Violations": [{"tag": "Owner", "type": "MISSING_REQUIRED"}],
                    "Tags": {},
                    "NormalizedTags": {}
                }
            ],
            "compliance_score": 0.0,
        }
    }
}


# ── Health & Meta ────────────────────────────────────────────────────

def test_health_api(client):
    response = client.get('/health')
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"


def test_resource_types(client):
    response = client.get('/api/meta/resource-types')
    assert response.status_code == 200
    data = response.get_json()
    assert "aliases" in data
    assert "map" in data
    assert len(data["aliases"]) > 0


# ── Schema (real, reads config/tag-schema.yaml) ─────────────────────

def test_schema_api(client):
    response = client.get('/api/schema')
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "tag" in data[0]
    assert "required" in data[0]
    assert "description" in data[0]


# ── Dashboard (reads from cache, not AWS) ───────────────────────────

def test_dashboard_api_with_cached_data(client):
    """Dashboard should read from file cache and return correct structure."""
    import src.cache_manager as cm
    cm.save_report(SAMPLE_REPORT)

    response = client.get('/api/dashboard')
    assert response.status_code == 200
    data = response.get_json()
    assert data["compliance_pct"] == 80.0
    assert data["total_resources"] == 10
    assert data["compliant_resources"] == 8
    assert data["non_compliant_resources"] == 2
    assert "_meta" in data


def test_dashboard_api_no_cache_returns_zeros(client):
    """Without cache, dashboard returns zeros (not 500) so UI renders."""
    response = client.get('/api/dashboard')
    assert response.status_code == 200
    data = response.get_json()
    assert data["total_resources"] == 0


def test_dashboard_api_handles_aws_error(client):
    """If cache is empty AND a fallback generate_report call raises, return 500."""
    # This tests the error path that can still happen if someone adds a
    # generate_report fallback that fails.
    with patch("web.app.get_cached_report", side_effect=Exception("Unexpected")):
        response = client.get('/api/dashboard')
        assert response.status_code == 500
        data = response.get_json()
        assert "error_code" in data
        assert "message" in data
        assert "request_id" in data


# ── Compliance (reads from cache) ───────────────────────────────────

def test_compliance_api_with_cached_data(client):
    """Compliance endpoint should read from cache and return resource-level data."""
    import src.cache_manager as cm
    cm.save_report(SAMPLE_REPORT)

    response = client.get('/api/compliance')
    assert response.status_code == 200
    data = response.get_json()
    assert "resources" in data
    assert len(data["resources"]) == 1
    r = data["resources"][0]
    assert r["status"] == "NON_COMPLIANT"
    assert "Owner" in r["missing_tags"]


def test_compliance_api_no_cache_returns_empty(client):
    """Without cache the compliance endpoint returns an empty resource list."""
    response = client.get('/api/compliance')
    assert response.status_code == 200
    data = response.get_json()
    assert data["resources"] == []


def test_compliance_api_handles_aws_error(client):
    """If an unexpected exception occurs inside the endpoint, return 500."""
    with patch("web.app.get_cached_report", side_effect=Exception("NoCredentialProviders")):
        response = client.get('/api/compliance')
        assert response.status_code == 500
        data = response.get_json()
        assert "error_code" in data
        assert "message" in data
        assert "request_id" in data


# ── FinOps ───────────────────────────────────────────────────────────

def test_finops_api_success(client):
    """FinOps should trigger refresh and return 202 or 200."""
    with patch("src.finops.cost_explorer.CostExplorerClient") as MockCE:
        mock_instance = MockCE.return_value
        mock_instance.get_cost_and_usage.return_value = {
            'ResultsByTime': [{'Total': {'UnblendedCost': {'Amount': '500.00'}}}]
        }
        response = client.get('/api/finops')
        assert response.status_code in [200, 202]
        data = response.get_json()
        if response.status_code == 200:
            assert "TotalSpend" in data
            assert "error" not in data
        else:
            assert data["error"] == "not_ready"


def test_finops_api_failure(client):
    """FinOps should return 202 initially."""
    with patch("src.finops.cost_explorer.CostExplorerClient.__init__", side_effect=Exception("CE not enabled")):
        response = client.get('/api/finops')
        assert response.status_code in [200, 202, 502]
        data = response.get_json()
        if response.status_code == 502:
            assert "error_code" in data
        else:
            assert "error" in data  # For 202 not_ready behavior
        assert "message" in data
        # Must NOT contain fabricated spend numbers
        assert "TotalSpend" not in data


# ── Unimplemented Enterprise Endpoints (501) ─────────────────────────

@pytest.mark.parametrize("endpoint,expected_message_keyword", [
    ("/api/security", "DynamoDB"),
    ("/api/cicd", "local"),
])
def test_unimplemented_endpoints(client, endpoint, expected_message_keyword):
    """Unimplemented endpoints should return 501 with a clear message."""
    response = client.get(endpoint)
    assert response.status_code == 501
    data = response.get_json()
    assert data["error_code"] == "NOT_IMPLEMENTED"
    assert "message" in data
    assert expected_message_keyword.lower() in data["message"].lower()
    assert "request_id" in data
