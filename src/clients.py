"""
Centralized AWS client factory with built-in retry configuration.

Usage:
    from src.clients import get_tagging_client, get_ec2_client

All clients get adaptive retry with configurable max attempts,
eliminating the need for manual backoff/retry logic in business code.
Every client is instrumented: each API call is counted and timed
(see src/observability/metrics.py).
"""

from __future__ import annotations

import threading
import time

import boto3
from botocore.config import Config

from src.config import BOTO_MAX_RETRIES, BOTO_RETRY_MODE, DEFAULT_REGION

_BOTO_CONFIG = Config(
    retries={
        "max_attempts": BOTO_MAX_RETRIES,
        "mode": BOTO_RETRY_MODE,
    }
)

# boto3's default session is not thread-safe for client creation; the web app
# creates clients from request threads and background refresh threads.
_CREATE_LOCK = threading.Lock()


def _before_call(model, context, **kwargs):
    context["_metrics"] = (model.service_model.service_name, model.name, time.perf_counter())


def _after_call(http_response, parsed, model, context, **kwargs):
    from src.observability.metrics import record_aws_call
    service, op = model.service_model.service_name, model.name
    start = context.get("_metrics", (None, None, None))[2]
    duration = time.perf_counter() - start if start else None
    status = getattr(http_response, "status_code", 0) or 0
    error_code = parsed.get("Error", {}).get("Code") if isinstance(parsed, dict) else None
    outcome = "success" if status < 400 and not error_code else (error_code or f"http_{status}")
    record_aws_call(service, op, outcome, duration)


def _after_call_error(context, exception, **kwargs):
    # Raised without an HTTP response (connection error, missing credentials, ...)
    from src.observability.metrics import record_aws_call
    service, op, start = context.get("_metrics", ("unknown", "unknown", None))
    duration = time.perf_counter() - start if start else None
    record_aws_call(service, op, type(exception).__name__, duration)


def _client(service: str, region: str | None = None):
    with _CREATE_LOCK:
        kwargs = {"config": _BOTO_CONFIG}
        if region:
            kwargs["region_name"] = region
        client = boto3.client(service, **kwargs)
    events = client.meta.events
    events.register("before-call", _before_call)
    events.register("after-call", _after_call)
    events.register("after-call-error", _after_call_error)
    return client


def get_tagging_client(region: str = DEFAULT_REGION):
    """Resource Groups Tagging API client."""
    return _client("resourcegroupstaggingapi", region)


def get_ec2_client(region: str = DEFAULT_REGION):
    """EC2 client (used for region discovery and VPC sync)."""
    return _client("ec2", region)


def get_cloudtrail_client(region: str = DEFAULT_REGION):
    """CloudTrail client (used for creator lookup)."""
    return _client("cloudtrail", region)


def get_s3_client(region: str = DEFAULT_REGION):
    """S3 client (used for report export)."""
    return _client("s3", region)


def get_sts_client():
    """STS client (used for account identity)."""
    return _client("sts")
