from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.config import MAX_REMEDIATION_ATTEMPTS
from src.governance.exemptions import ExemptionManager
from src.governance.remediation import RemediationEngine
from src.governance.state import MemoryStateStore


@pytest.fixture
def state_store():
    return MemoryStateStore()

@pytest.fixture
def exemption_manager():
    manager = MagicMock(spec=ExemptionManager)
    manager.is_exempt.return_value = None
    return manager

@pytest.fixture
def engine(state_store, exemption_manager):
    return RemediationEngine(state_store, exemption_manager)

def test_process_async_missing_action(engine):
    # Missing action -> poison pill behavior -> returns False
    assert engine.process_async("nonexistent", "worker-1", "req-1") is False

def test_process_async_terminal_action(engine, state_store):
    state_store.store["ACTION#act-1"] = {
        "action_id": "act-1",
        "status": "COMPLETED"
    }
    # Should just return True without executing
    assert engine.process_async("act-1", "worker-1", "req-1") is True

@patch("src.governance.remediation.RemediationEngine._execute_tag_mutation")
def test_process_async_successful(mock_exec, engine, state_store):
    mock_exec.return_value = ("COMPLETED", None)

    state_store.store["ACTION#act-1"] = {
        "action_id": "act-1",
        "resource_arn": "arn:aws:ec2:region:acct:instance/i-123",
        "requested_tags": {"Owner": "Alice"},
        "status": "PENDING",
        "attempt_count": 0
    }

    assert engine.process_async("act-1", "worker-1", "req-1") is True

    action = state_store.get_remediation_action("act-1")
    assert action["status"] == "COMPLETED"
    assert action["worker_id"] == "worker-1"
    assert action["attempt_count"] == 1
    mock_exec.assert_called_once_with({"Owner": "Alice"}, "arn:aws:ec2:region:acct:instance/i-123")

@patch("src.governance.remediation.RemediationEngine._execute_tag_mutation")
def test_process_async_failed_retryable(mock_exec, engine, state_store):
    mock_exec.return_value = ("FAILED_RETRYABLE", "THROTTLED")

    state_store.store["ACTION#act-1"] = {
        "action_id": "act-1",
        "status": "PENDING",
        "attempt_count": 0
    }

    # Returns False to keep message in SQS
    assert engine.process_async("act-1", "worker-1", "req-1") is False

    action = state_store.get_remediation_action("act-1")
    assert action["status"] == "FAILED_RETRYABLE"
    assert action["last_error_code"] == "THROTTLED"

@patch("src.governance.remediation.RemediationEngine._execute_tag_mutation")
def test_process_async_failed_terminal(mock_exec, engine, state_store):
    mock_exec.return_value = ("FAILED", "AccessDenied")

    state_store.store["ACTION#act-1"] = {
        "action_id": "act-1",
        "status": "PENDING",
        "attempt_count": 0
    }

    # Returns True to delete message since failure is permanent
    assert engine.process_async("act-1", "worker-1", "req-1") is True

    action = state_store.get_remediation_action("act-1")
    assert action["status"] == "FAILED"
    assert action["last_error_code"] == "AccessDenied"

@patch("src.governance.remediation.RemediationEngine._execute_tag_mutation")
def test_process_async_exhausted_retries(mock_exec, engine, state_store):
    # Should not even be called

    state_store.store["ACTION#act-1"] = {
        "action_id": "act-1",
        "status": "FAILED_RETRYABLE",
        "attempt_count": MAX_REMEDIATION_ATTEMPTS
    }

    assert engine.process_async("act-1", "worker-1", "req-1") is True

    action = state_store.get_remediation_action("act-1")
    assert action["status"] == "FAILED"
    assert action["last_error_code"] == "MAX_RETRIES_EXHAUSTED"
    mock_exec.assert_not_called()

@patch("src.governance.remediation.RemediationEngine._execute_tag_mutation")
def test_process_async_valid_lease_rejected(mock_exec, engine, state_store):
    # Lease is currently valid and owned by someone else
    now_dt = datetime.now(timezone.utc)
    lease_until = (now_dt + timedelta(minutes=5)).isoformat()

    state_store.store["ACTION#act-1"] = {
        "action_id": "act-1",
        "status": "IN_PROGRESS",
        "worker_id": "worker-other",
        "lease_until": lease_until,
        "attempt_count": 1
    }

    # Cannot claim lease -> False (let SQS retry)
    assert engine.process_async("act-1", "worker-1", "req-1") is False
    mock_exec.assert_not_called()

@patch("src.governance.remediation.RemediationEngine._execute_tag_mutation")
def test_process_async_expired_lease_recovered(mock_exec, engine, state_store):
    mock_exec.return_value = ("COMPLETED", None)
    # Lease has expired
    now_dt = datetime.now(timezone.utc)
    lease_until = (now_dt - timedelta(minutes=5)).isoformat()

    state_store.store["ACTION#act-1"] = {
        "action_id": "act-1",
        "status": "IN_PROGRESS",
        "worker_id": "worker-other",
        "lease_until": lease_until,
        "attempt_count": 1
    }

    # Claims expired lease -> successful execution -> True
    assert engine.process_async("act-1", "worker-1", "req-1") is True

    action = state_store.get_remediation_action("act-1")
    assert action["status"] == "COMPLETED"
    assert action["worker_id"] == "worker-1"
    assert action["attempt_count"] == 2
    mock_exec.assert_called_once()
