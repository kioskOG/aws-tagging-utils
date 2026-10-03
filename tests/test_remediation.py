import pytest
from datetime import datetime, timezone, timedelta
from src.governance.state import MemoryStateStore
from src.governance.exemptions import ExemptionManager
from src.governance.models import ValidationResult, ValidationViolation
from src.governance.remediation import RemediationEngine

class MockNotificationProvider:
    def notify(self, severity, message, context):
        return True

@pytest.fixture
def remediation_engine():
    store = MemoryStateStore()
    exemptions = ExemptionManager(store)
    notifications = MockNotificationProvider()
    return RemediationEngine(store, exemptions, notifications)

def test_compliant_resource(remediation_engine):
    val_res = ValidationResult(compliant=True, violations=[], normalized_tags={})
    res = remediation_engine.process("123", "us-east-1", "ec2", "i-123", {"Owner": "dev"}, val_res)
    assert res["status"] == "COMPLIANT"

def test_non_compliant_detection(remediation_engine):
    val_res = ValidationResult(compliant=False, violations=[ValidationViolation("Owner", "MISSING_REQUIRED", "Value", "")], normalized_tags={})
    res = remediation_engine.process("123", "us-east-1", "ec2", "i-456", {}, val_res)
    assert res["status"] == "NON_COMPLIANT_DETECTED"
    assert "deadline" in res
    
    state = remediation_engine.state_store.get_resource_state("i-456")
    assert state["status"] == "NON_COMPLIANT"

def test_grace_period_active(remediation_engine):
    # Setup state
    now = datetime.now(timezone.utc)
    future = now + timedelta(days=5)
    remediation_engine.state_store.put_resource_state({
        "resource_id": "i-789",
        "status": "NON_COMPLIANT",
        "remediation_deadline": future.isoformat()
    })
    
    val_res = ValidationResult(compliant=False, violations=[], normalized_tags={})
    res = remediation_engine.process("123", "us-east-1", "ec2", "i-789", {}, val_res)
    assert res["status"] == "GRACE_PERIOD_ACTIVE"

def test_termination_disabled(remediation_engine):
    # Setup state past deadline
    past = datetime.now(timezone.utc) - timedelta(days=1)
    remediation_engine.state_store.put_resource_state({
        "resource_id": "i-999",
        "status": "NON_COMPLIANT",
        "remediation_deadline": past.isoformat()
    })
    
    val_res = ValidationResult(compliant=False, violations=[], normalized_tags={})
    # Requires GOVERNANCE_TERMINATION_ENABLED mock to be False or True
    import src.governance.remediation as rem
    rem.GOVERNANCE_TERMINATION_ENABLED = False
    
    res = remediation_engine.process("123", "us-east-1", "ec2", "i-999", {}, val_res)
    assert res["status"] == "TERMINATION_BLOCKED"

def test_exemption(remediation_engine):
    # Add mock exemption
    remediation_engine.exemption_manager._mock_exemptions = [{
        "id": "ex-123",
        "status": "ACTIVE",
        "resource_id": "i-exempt"
    }]
    
    val_res = ValidationResult(compliant=False, violations=[], normalized_tags={})
    res = remediation_engine.process("123", "us-east-1", "ec2", "i-exempt", {}, val_res)
    assert res["status"] == "EXEMPT"
