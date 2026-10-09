import pytest
from src.governance.state import MemoryStateStore
from src.governance.exemptions import ExemptionManager
from src.errors import APIError

@pytest.fixture
def exemption_manager():
    store = MemoryStateStore()
    return ExemptionManager(store)

def test_resource_id_exemption(exemption_manager):
    exemption_manager.create_exemption({
        "resource_id": "i-1234"
    }, "user1")
    
    assert exemption_manager.is_exempt("111", "ec2", "i-1234") is not None
    assert exemption_manager.is_exempt("111", "ec2", "i-9999") is None

def test_account_exemption(exemption_manager):
    exemption_manager.create_exemption({
        "account_id": "222222222222"
    }, "user1")
    
    assert exemption_manager.is_exempt("222222222222", "ec2", "i-1234") is not None
    assert exemption_manager.is_exempt("333333333333", "ec2", "i-1234") is None

def test_account_env_exemption(exemption_manager):
    exemption_manager.create_exemption({
        "account_id": "111",
        "environment": "prod"
    }, "user1")
    
    assert exemption_manager.is_exempt("111", "ec2", "i-1", "prod") is not None
    assert exemption_manager.is_exempt("111", "ec2", "i-1", "dev") is None

def test_resource_type_env_exemption(exemption_manager):
    exemption_manager.create_exemption({
        "resource_type": "ec2",
        "environment": "prod"
    }, "user1")
    
    assert exemption_manager.is_exempt("111", "ec2", "i-1", "prod") is not None
    assert exemption_manager.is_exempt("111", "s3", "b-1", "prod") is None

def test_resource_type_exemption(exemption_manager):
    exemption_manager.create_exemption({
        "resource_type": "ec2"
    }, "user1")
    
    assert exemption_manager.is_exempt("111", "ec2", "i-1", "prod") is not None
    assert exemption_manager.is_exempt("111", "s3", "b-1", "prod") is None

def test_unsupported_combinations(exemption_manager):
    with pytest.raises(APIError) as exc:
        exemption_manager.create_exemption({"account_id": "111", "resource_type": "ec2"}, "user1")
    assert exc.value.status_code == 400

    with pytest.raises(APIError) as exc:
        exemption_manager.create_exemption({"resource_id": "i-1", "account_id": "111"}, "user1")
    assert exc.value.status_code == 400

    with pytest.raises(APIError) as exc:
        exemption_manager.create_exemption({"environment": "prod"}, "user1")
    assert exc.value.status_code == 400

def test_precedence_resolution(exemption_manager):
    # rank 1
    ex1 = exemption_manager.create_exemption({"account_id": "111"}, "user1")
    # rank 2
    ex2 = exemption_manager.create_exemption({"account_id": "111", "environment": "prod"}, "user1")
    # rank 5
    ex5 = exemption_manager.create_exemption({"resource_id": "i-1"}, "user1")
    
    res = exemption_manager.is_exempt("111", "ec2", "i-1", "prod")
    assert res["id"] == ex5["id"]

    res2 = exemption_manager.is_exempt("111", "ec2", "i-2", "prod")
    assert res2["id"] == ex2["id"]

def test_expired_exemption(exemption_manager):
    ex = exemption_manager.create_exemption({
        "resource_id": "i-1234",
        "expires_at": "2020-01-01T00:00:00Z"
    }, "user1")
    
    assert exemption_manager.is_exempt("111", "ec2", "i-1234") is None

def test_inactive_exemption(exemption_manager):
    ex = exemption_manager.create_exemption({"resource_id": "i-1234"}, "user1")
    exemption_manager.revoke_exemption(ex["id"])
    assert exemption_manager.is_exempt("111", "ec2", "i-1234") is None

def test_duplicate_conflict(exemption_manager):
    exemption_manager.create_exemption({"resource_id": "i-1234"}, "user1")
    with pytest.raises(APIError) as exc:
        exemption_manager.create_exemption({"resource_id": "i-1234"}, "user1")
    assert exc.value.status_code == 409

def test_equal_specificity_corrupt_state(exemption_manager):
    # Force two active exemptions with same specificity directly into state store
    ex1 = {"id": "x1", "status": "ACTIVE", "account_id": "111", "PK": "EXEMPTION#x1"}
    ex2 = {"id": "x2", "status": "ACTIVE", "account_id": "111", "PK": "EXEMPTION#x2"}
    exemption_manager.state_store.put_exemption(ex1)
    exemption_manager.state_store.put_exemption(ex2)
    
    # Should fail closed
    assert exemption_manager.is_exempt("111", "ec2", "i-1") is None
