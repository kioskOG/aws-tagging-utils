import pytest
from datetime import datetime, timezone, timedelta
from src.governance.state import MemoryStateStore
from src.governance.exemptions import ExemptionManager
from src.governance.models import ValidationResult, ValidationViolation
from src.governance.remediation import RemediationEngine
from src.errors import APIError

@pytest.fixture
def remediation_engine(monkeypatch):
    store = MemoryStateStore()
    exemptions = ExemptionManager(store)
    engine = RemediationEngine(store, exemptions, None)
    
    # Mock AWS tag write
    def mock_tag_resources(arns, tags, region):
        if "fail-arn" in arns:
            return {"tagged_count": 0, "failed_resources": {"fail-arn": {"ErrorCode": "AccessDenied"}}}
        if "throttle-arn" in arns:
            return {"tagged_count": 0, "failed_resources": {"throttle-arn": {"ErrorCode": "ThrottlingException"}}}
        return {"tagged_count": len(arns), "failed_resources": {}}
        
    monkeypatch.setattr("src.tag_writer.tag_resources", mock_tag_resources)
    
    # Mock governance engine evaluation
    class MockGovernanceEngine:
        def evaluate(self, tags, arn=None):
            if "invalid" in tags:
                return ValidationResult(compliant=False, violations=[ValidationViolation("invalid", "INVALID", "", "")], normalized_tags=tags)
            return ValidationResult(compliant=True, violations=[], normalized_tags=tags)
    monkeypatch.setattr("src.tag_writer.governance_engine", MockGovernanceEngine())
    
    return engine

def test_successful_remediation(remediation_engine):
    req = {
        "resource_arn": "arn:aws:ec2:us-east-1:123456789012:instance/i-123",
        "requested_tags": {"Owner": "team-a"}
    }
    res = remediation_engine.process_sync(req, "user1", "req-1")
    assert res["status"] == "COMPLETED"
    assert res["actor"] == "user1"
    
    # Verify idempotency
    res2 = remediation_engine.process_sync(req, "user1", "req-2")
    assert res2["status"] == "COMPLETED"
    assert res2["action_id"] == res["action_id"]
    assert res2.get("attempt_count") == 1 # Second call didn't execute

def test_exemption_skip(remediation_engine):
    remediation_engine.exemption_manager.create_exemption({"resource_id": "arn:aws:ec2:us-east-1:123456789012:instance/i-456"}, "admin")
    
    req = {
        "resource_arn": "arn:aws:ec2:us-east-1:123456789012:instance/i-456",
        "requested_tags": {"Owner": "team-b"}
    }
    res = remediation_engine.process_sync(req, "user1", "req-1")
    assert res["status"] == "SKIPPED_EXEMPT"
    assert "exemption_id" in res

def test_failed_remediation(remediation_engine):
    req = {
        "resource_arn": "fail-arn",
        "requested_tags": {"Owner": "team-c"}
    }
    res = remediation_engine.process_sync(req, "user1", "req-1")
    assert res["status"] == "FAILED"
    assert res["last_error_code"] == "AccessDenied"

def test_throttled_remediation(remediation_engine):
    req = {
        "resource_arn": "throttle-arn",
        "requested_tags": {"Owner": "team-d"}
    }
    res = remediation_engine.process_sync(req, "user1", "req-1")
    assert res["status"] == "FAILED_RETRYABLE"
    assert res["last_error_code"] == "ThrottlingException"
    assert res["attempt_count"] == 1
    
    # Retry should increment attempt_count
    res2 = remediation_engine.process_sync(req, "user1", "req-2")
    assert res2["status"] == "FAILED_RETRYABLE"
    assert res2["attempt_count"] == 2

def test_validation_failure(remediation_engine, monkeypatch):
    import src.config
    monkeypatch.setattr(src.config, "GOVERNANCE_STRICT_MODE", True)
    
    req = {
        "resource_arn": "arn:aws:ec2:us-east-1:123456789012:instance/i-999",
        "requested_tags": {"invalid": "value"}
    }
    res = remediation_engine.process_sync(req, "user1", "req-1")
    assert res["status"] == "FAILED"
    assert res["last_error_code"] == "VALIDATION_FAILED"

def test_invalid_request(remediation_engine):
    with pytest.raises(APIError):
        remediation_engine.process_sync({}, "user1", "req-1")

def test_concurrency_protection(remediation_engine, monkeypatch):
    req = {
        "resource_arn": "arn:aws:ec2:us-east-1:123456789012:instance/i-race",
        "requested_tags": {"Owner": "team-a"}
    }
    
    # Mock claim to fail as if another worker took it
    monkeypatch.setattr(remediation_engine.state_store, "claim_remediation_action", lambda *args, **kwargs: False)
    
    res = remediation_engine.process_sync(req, "user1", "req-1")
    # Because claim fails, it returns the current state which is PENDING
    assert res["status"] == "PENDING"
    assert res.get("attempt_count") == 0

def test_idempotency_mismatch(remediation_engine):
    req1 = {
        "idempotency_key": "my-explicit-key",
        "resource_arn": "arn:aws:ec2:us-east-1:123456789012:instance/i-idemp",
        "requested_tags": {"Owner": "team-a"}
    }
    res1 = remediation_engine.process_sync(req1, "user1", "req-1")
    assert res1["status"] == "COMPLETED"

    # Same key, identical request
    res2 = remediation_engine.process_sync(req1, "user1", "req-2")
    assert res2["status"] == "COMPLETED"
    
    # Same key, different requested_tags
    req_diff_tags = dict(req1, requested_tags={"Owner": "team-b"})
    with pytest.raises(APIError) as exc:
        remediation_engine.process_sync(req_diff_tags, "user1", "req-3")
    assert exc.value.status_code == 409

    # Same key, different resource_arn
    req_diff_arn = dict(req1, resource_arn="arn:aws:ec2:us-east-1:123456789012:instance/i-diff")
    with pytest.raises(APIError) as exc:
        remediation_engine.process_sync(req_diff_arn, "user1", "req-4")
    assert exc.value.status_code == 409

def test_max_remediation_attempts(remediation_engine, monkeypatch):
    # Set MAX_REMEDIATION_ATTEMPTS = 2 for this test
    monkeypatch.setattr("src.governance.remediation.MAX_REMEDIATION_ATTEMPTS", 2)
    
    req = {
        "resource_arn": "throttle-arn",
        "requested_tags": {"Owner": "team-retry"}
    }
    
    # Attempt 1 -> fails with throttle
    res1 = remediation_engine.process_sync(req, "user1", "req-1")
    assert res1["status"] == "FAILED_RETRYABLE"
    assert res1["attempt_count"] == 1
    
    # Attempt 2 -> fails with throttle
    res2 = remediation_engine.process_sync(req, "user1", "req-2")
    assert res2["status"] == "FAILED_RETRYABLE"
    assert res2["attempt_count"] == 2
    
    # Attempt 3 -> exhausted max attempts, becomes FAILED
    res3 = remediation_engine.process_sync(req, "user1", "req-3")
    assert res3["status"] == "FAILED"
    assert res3["last_error_code"] == "MAX_RETRIES_EXHAUSTED"
    assert res3["attempt_count"] == 2  # Does not increment past the limit
