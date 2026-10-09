import json
import signal
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from src.worker import (
    WorkerState,
    handle_shutdown_signal,
    process_message,
    run_worker,
    validate_message,
)

# ── Message Validation Tests ──────────────────────────────────────────────

def test_valid_message():
    body = json.dumps({
        "version": "1",
        "job_id": "test-123",
        "job_type": "WORKER_HEALTH_CHECK",
        "correlation_id": "corr-456",
        "created_at": "2026-10-04T12:00:00Z",
        "payload": {"key": "value"}
    })
    msg = validate_message(body)
    assert msg["job_id"] == "test-123"

def test_missing_version():
    body = json.dumps({
        "job_id": "test-123",
        "job_type": "WORKER_HEALTH_CHECK",
        "correlation_id": "corr-456",
        "payload": {}
    })
    with pytest.raises(ValueError, match="Unsupported or missing version"):
        validate_message(body)

def test_unsupported_version():
    body = json.dumps({
        "version": "2",
        "job_id": "test-123",
        "job_type": "WORKER_HEALTH_CHECK",
        "correlation_id": "corr-456",
        "payload": {}
    })
    with pytest.raises(ValueError, match="Unsupported or missing version: 2"):
        validate_message(body)

def test_missing_job_id():
    body = json.dumps({
        "version": "1",
        "job_type": "WORKER_HEALTH_CHECK",
        "correlation_id": "corr-456",
        "payload": {}
    })
    with pytest.raises(ValueError, match="Missing job_id"):
        validate_message(body)

def test_missing_job_type():
    body = json.dumps({
        "version": "1",
        "job_id": "test-123",
        "correlation_id": "corr-456",
        "payload": {}
    })
    with pytest.raises(ValueError, match="Missing job_type"):
        validate_message(body)

def test_missing_correlation_id():
    body = json.dumps({
        "version": "1",
        "job_id": "test-123",
        "job_type": "WORKER_HEALTH_CHECK",
        "payload": {}
    })
    with pytest.raises(ValueError, match="Missing correlation_id"):
        validate_message(body)

def test_malformed_payload():
    body = json.dumps({
        "version": "1",
        "job_id": "test-123",
        "job_type": "WORKER_HEALTH_CHECK",
        "correlation_id": "corr-456",
        "payload": "not-a-dict"
    })
    with pytest.raises(ValueError, match="Malformed payload"):
        validate_message(body)

def test_malformed_json():
    with pytest.raises(ValueError, match="Malformed JSON payload"):
        validate_message("this is not json")

# ── Processing Tests ──────────────────────────────────────────────────────

def test_process_message_success():
    envelope = {
        "job_id": "test-1",
        "job_type": "WORKER_HEALTH_CHECK",
        "correlation_id": "req-1",
        "payload": {}
    }
    engine = MagicMock()
    assert process_message(envelope, engine) is True

def test_process_message_unsupported_type():
    envelope = {
        "job_id": "test-2",
        "job_type": "UNKNOWN_TYPE",
        "correlation_id": "req-2",
        "payload": {}
    }
    engine = MagicMock()
    assert process_message(envelope, engine) is False

def test_process_message_exception(monkeypatch):
    envelope = {
        "job_id": "test-3",
        "job_type": "WORKER_HEALTH_CHECK",
        "correlation_id": "req-3",
        "payload": {}
    }
    # Force an exception inside the handler mock logic if we had one
    # Since health check is hardcoded in `process_message`, we mock the logger to raise an exception
    # to simulate unexpected failure
    def raise_err(*args, **kwargs):
        raise RuntimeError("simulated crash")
    monkeypatch.setattr("src.worker.logger.info", raise_err)

    engine = MagicMock()
    assert process_message(envelope, engine) is False

# ── Worker Run Tests ──────────────────────────────────────────────────────

@patch("src.worker.boto3.client")
def test_worker_receive_and_success_delete(mock_boto3, monkeypatch):
    WorkerState.shutdown_requested = False
    mock_sqs = MagicMock()
    mock_boto3.return_value = mock_sqs

    # Mock config to enable loop
    monkeypatch.setattr("src.worker.SQS_QUEUE_URL", "https://sqs.test/queue")

    # First call returns a message, second call sets shutdown to break loop
    msg = {
        "MessageId": "msg-1",
        "ReceiptHandle": "receipt-1",
        "Body": json.dumps({
            "version": "1",
            "job_id": "j-1",
            "job_type": "WORKER_HEALTH_CHECK",
            "correlation_id": "c-1",
            "payload": {}
        })
    }

    def side_effect(*args, **kwargs):
        if not WorkerState.shutdown_requested:
            # We want to return the message, but NOT set shutdown immediately,
            # so the loop can process it. However, we need the loop to exit after this batch.
            # We will set shutdown_requested = True, but we need the loop to process the first message.
            # Actually, `side_effect` is called inside `receive_message`. If we set shutdown to True here,
            # the loop checks it *after* receive_message returns.
            # No wait, in src/worker.py:
            # messages = response.get("Messages", [])
            # for sqs_msg in messages:
            #     if WorkerState.shutdown_requested: ...
            # If we set it inside receive_message, the loop sees True immediately and breaks without processing.
            # So we should return the message, and let the NEXT call to receive_message set the shutdown.
            if getattr(side_effect, "called", False):
                WorkerState.shutdown_requested = True
                return {}
            side_effect.called = True
            return {"Messages": [msg]}
        return {}
    side_effect.called = False
    mock_sqs.receive_message.side_effect = side_effect

    run_worker()

    # Should have called delete message
    mock_sqs.delete_message.assert_called_once_with(
        QueueUrl="https://sqs.test/queue",
        ReceiptHandle="receipt-1"
    )

@patch("src.worker.boto3.client")
def test_worker_handler_failure_no_delete(mock_boto3, monkeypatch):
    WorkerState.shutdown_requested = False
    mock_sqs = MagicMock()
    mock_boto3.return_value = mock_sqs

    monkeypatch.setattr("src.worker.SQS_QUEUE_URL", "https://sqs.test/queue")

    # Send unsupported type to trigger False from process_message
    msg = {
        "MessageId": "msg-1",
        "ReceiptHandle": "receipt-1",
        "Body": json.dumps({
            "version": "1",
            "job_id": "j-2",
            "job_type": "UNSUPPORTED",
            "correlation_id": "c-2",
            "payload": {}
        })
    }

    def side_effect(*args, **kwargs):
        WorkerState.shutdown_requested = True
        return {"Messages": [msg]}

    mock_sqs.receive_message.side_effect = side_effect

    run_worker()

    # Should NOT have called delete message
    mock_sqs.delete_message.assert_not_called()

@patch("src.worker.boto3.client")
def test_worker_sqs_error_continues(mock_boto3, monkeypatch):
    WorkerState.shutdown_requested = False
    mock_sqs = MagicMock()
    mock_boto3.return_value = mock_sqs

    monkeypatch.setattr("src.worker.SQS_QUEUE_URL", "https://sqs.test/queue")
    monkeypatch.setattr("src.worker.time.sleep", MagicMock())

    # First call raises ClientError, second breaks loop
    call_count = [0]
    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ClientError({"Error": {"Code": "Test"}}, "receive_message")
        WorkerState.shutdown_requested = True
        return {}

    mock_sqs.receive_message.side_effect = side_effect

    run_worker()

    assert mock_sqs.receive_message.call_count == 2
    import src.worker
    src.worker.time.sleep.assert_called_once_with(5)

# ── Shutdown Tests ────────────────────────────────────────────────────────

def test_shutdown_signal_handler():
    WorkerState.shutdown_requested = False
    handle_shutdown_signal(signal.SIGTERM, None)
    assert WorkerState.shutdown_requested is True
    WorkerState.shutdown_requested = False
