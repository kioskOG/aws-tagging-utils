"""
Centralized AWS client factory with built-in retry configuration.

Usage:
    from src.clients import get_tagging_client, get_ec2_client

All clients get adaptive retry with configurable max attempts,
eliminating the need for manual backoff/retry logic in business code.
"""

from __future__ import annotations

import boto3
from botocore.config import Config

from src.config import BOTO_MAX_RETRIES, BOTO_RETRY_MODE, DEFAULT_REGION

_BOTO_CONFIG = Config(
    retries={
        "max_attempts": BOTO_MAX_RETRIES,
        "mode": BOTO_RETRY_MODE,
    }
)


def get_tagging_client(region: str = DEFAULT_REGION):
    """Resource Groups Tagging API client."""
    return boto3.client(
        "resourcegroupstaggingapi",
        region_name=region,
        config=_BOTO_CONFIG,
    )


def get_ec2_client(region: str = DEFAULT_REGION):
    """EC2 client (used for region discovery and VPC sync)."""
    return boto3.client("ec2", region_name=region, config=_BOTO_CONFIG)


def get_cloudtrail_client(region: str = DEFAULT_REGION):
    """CloudTrail client (used for creator lookup)."""
    return boto3.client("cloudtrail", region_name=region, config=_BOTO_CONFIG)


def get_s3_client(region: str = DEFAULT_REGION):
    """S3 client (used for report export)."""
    return boto3.client("s3", region_name=region, config=_BOTO_CONFIG)


def get_sts_client():
    """STS client (used for account identity)."""
    return boto3.client("sts", config=_BOTO_CONFIG)
