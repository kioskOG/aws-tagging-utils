import json
import os
import signal
import sys
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from src.config import (
    DEFAULT_REGION,
    SQS_MAX_MESSAGES,
    SQS_QUEUE_URL,
    SQS_VISIBILITY_TIMEOUT_SECONDS,
    SQS_WAIT_TIME_SECONDS,
)
from src.governance.exemptions import ExemptionManager
from src.governance.remediation import RemediationEngine
from src.governance.state import DynamoDBStateStore
from src.logging_config import get_logger

logger = get_logger(__name__)

WORKER_ID = os.getenv("ECS_TASK_ID", f"worker-{uuid.uuid4()}")

class WorkerState:
    shutdown_requested = False

def handle_shutdown_signal(signum: int, frame: Any) -> None:
    logger.info(f"Worker shutdown requested via signal {signum}. Will exit gracefully.")
    WorkerState.shutdown_requested = True

def validate_message(body: str) -> dict[str, Any]:
    """
    Validates the worker message envelope.
    Returns the parsed payload or raises ValueError on validation failure.
    """
    try:
        msg = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError("Malformed JSON payload") from e

    if not isinstance(msg, dict):
        raise ValueError("Message body must be a JSON object")

    if msg.get("version") != "1":
        raise ValueError(f"Unsupported or missing version: {msg.get('version')}")

    if not msg.get("job_id"):
        raise ValueError("Missing job_id")

    if not msg.get("job_type"):
        raise ValueError("Missing job_type")

    if "correlation_id" not in msg:
        raise ValueError("Missing correlation_id")

    payload = msg.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Malformed payload (must be an object)")

    if msg["job_type"] == "REMEDIATION" and "action_id" not in payload:
        raise ValueError("Missing action_id in REMEDIATION payload")

    return msg

def process_message(msg_envelope: dict[str, Any], engine: RemediationEngine) -> bool:
    """
    Dispatches the message to a handler.
    Returns True if successfully processed (can be deleted), False otherwise.
    """
    job_id = msg_envelope["job_id"]
    job_type = msg_envelope["job_type"]
    correlation_id = msg_envelope["correlation_id"]
    payload = msg_envelope["payload"]

    try:
        logger.info(
            "Handler started",
            extra={
                "job_id": job_id,
                "job_type": job_type,
                "correlation_id": correlation_id,
                "worker_id": WORKER_ID
            }
        )
        if job_type == "WORKER_HEALTH_CHECK":
            logger.info("Processed health check job.")
            success = True
        elif job_type == "REMEDIATION":
            action_id = payload["action_id"]
            logger.info(f"Processing remediation action {action_id}")
            success = engine.process_async(
                action_id=action_id,
                worker_id=WORKER_ID,
                correlation_id=correlation_id
            )
        else:
            logger.warning(f"Unsupported job type: {job_type}")
            # We don't delete unsupported jobs; they might be for a newer worker version
            success = False

        if success:
            logger.info(
                "Handler succeeded",
                extra={
                    "job_id": job_id,
                    "job_type": job_type,
                    "correlation_id": correlation_id
                }
            )
        else:
            logger.warning(
                "Handler failed",
                extra={
                    "job_id": job_id,
                    "job_type": job_type,
                    "correlation_id": correlation_id
                }
            )
        return success
    except Exception as e:
        logger.error(
            f"Handler failed with exception: {e}",
            exc_info=True,
            extra={
                "job_id": job_id,
                "job_type": job_type,
                "correlation_id": correlation_id
            }
        )
        return False

def run_worker() -> None:
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)

    if not SQS_QUEUE_URL:
        logger.error("SQS_QUEUE_URL is not set. Worker cannot start.")
        sys.exit(1)

    sqs_client = boto3.client("sqs", region_name=DEFAULT_REGION)

    logger.info("Worker started", extra={"queue_url": SQS_QUEUE_URL, "worker_id": WORKER_ID})

    state_store = DynamoDBStateStore()
    exemption_manager = ExemptionManager(state_store)
    engine = RemediationEngine(state_store=state_store, exemption_manager=exemption_manager)

    while not WorkerState.shutdown_requested:
        try:
            response = sqs_client.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=SQS_MAX_MESSAGES,
                WaitTimeSeconds=SQS_WAIT_TIME_SECONDS,
                VisibilityTimeout=SQS_VISIBILITY_TIMEOUT_SECONDS,
            )

            messages = response.get("Messages", [])

            for sqs_msg in messages:
                if WorkerState.shutdown_requested:
                    logger.info("Shutdown requested, skipping remaining messages in batch.")
                    break

                receipt_handle = sqs_msg["ReceiptHandle"]
                message_id = sqs_msg["MessageId"]
                body = sqs_msg.get("Body", "")

                logger.info("Message received", extra={"message_id": message_id})

                try:
                    envelope = validate_message(body)
                except ValueError as e:
                    logger.error(
                        "Message validation failure",
                        extra={"message_id": message_id, "error": str(e)}
                    )
                    # We do NOT delete malformed messages. Let DLQ handle them.
                    continue

                job_id = envelope["job_id"]
                correlation_id = envelope["correlation_id"]

                success = process_message(envelope, engine)

                if success:
                    try:
                        sqs_client.delete_message(
                            QueueUrl=SQS_QUEUE_URL,
                            ReceiptHandle=receipt_handle
                        )
                        logger.info(
                            "Message deleted",
                            extra={
                                "message_id": message_id,
                                "job_id": job_id,
                                "correlation_id": correlation_id
                            }
                        )
                    except (ClientError, BotoCoreError) as e:
                        logger.error(
                            "Failed to delete message",
                            extra={"message_id": message_id, "error": str(e)}
                        )
                else:
                    # Message is not deleted, remains in SQS to be retried/DLQ'd
                    pass

        except (ClientError, BotoCoreError) as e:
            logger.error("Polling/AWS error", extra={"error": str(e)})
            # Sleep briefly to avoid tight loop on persistent AWS errors
            time.sleep(5)

    logger.info("Worker shutdown complete.")

if __name__ == "__main__":
    run_worker()
