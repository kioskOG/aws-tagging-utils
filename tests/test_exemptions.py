import pytest
import os
import json
import tempfile
from src.governance.state import MemoryStateStore
from src.governance.exemptions import ExemptionManager

@pytest.fixture
def exemption_manager():
    store = MemoryStateStore()
    return ExemptionManager(store)

def test_resource_id_exemption(exemption_manager):
    exemption_manager._mock_exemptions = [{
        "id": "ex-1",
        "status": "ACTIVE",
        "resource_id": "i-1234"
    }]
    
    assert exemption_manager.is_exempt("111", "ec2", "i-1234") is not None
    assert exemption_manager.is_exempt("111", "ec2", "i-9999") is None

def test_account_exemption(exemption_manager):
    exemption_manager._mock_exemptions = [{
        "id": "ex-2",
        "status": "ACTIVE",
        "account_id": "222222222222"
    }]
    
    assert exemption_manager.is_exempt("222222222222", "ec2", "i-1234") is not None
    assert exemption_manager.is_exempt("333333333333", "ec2", "i-1234") is None

def test_expired_exemption(exemption_manager):
    exemption_manager._mock_exemptions = [{
        "id": "ex-3",
        "status": "ACTIVE",
        "resource_id": "i-1234",
        "expires_at": "2020-01-01T00:00:00Z"
    }]
    
    assert exemption_manager.is_exempt("111", "ec2", "i-1234") is None

def test_inactive_exemption(exemption_manager):
    exemption_manager._mock_exemptions = [{
        "id": "ex-4",
        "status": "REVOKED",
        "resource_id": "i-1234"
    }]
    
    assert exemption_manager.is_exempt("111", "ec2", "i-1234") is None
