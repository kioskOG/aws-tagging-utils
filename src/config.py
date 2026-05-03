"""
Centralized configuration for AWS Tagging Utilities.

All environment variables and constants are read once here and imported
by every other module.  This avoids scattered os.environ.get() calls
and the subtle region-mismatch bugs that follow.
"""

from __future__ import annotations

import os
from typing import List


# ── AWS ──────────────────────────────────────────────────────────────
DEFAULT_REGION: str = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-east-2"))

# ── Tagging Governance ───────────────────────────────────────────────
OWNER_TAG_KEY: str = os.environ.get("OWNER_TAG_KEY", "Owner")

MANDATORY_TAGS: List[str] = [
    t.strip()
    for t in os.environ.get("MANDATORY_TAGS", "Owner").split(",")
    if t.strip()
]

# ── API Limits & Retry ───────────────────────────────────────────────
TAG_API_BATCH_SIZE: int = int(os.environ.get("TAG_API_BATCH_SIZE", "20"))
TAG_LOOKUP_RETRIES: int = int(os.environ.get("TAG_LOOKUP_RETRIES", "10"))
TAG_LOOKUP_DELAY_SEC: float = float(os.environ.get("TAG_LOOKUP_DELAY_SEC", "1.5"))

# ── S3 Reporting ─────────────────────────────────────────────────────
REPORT_BUCKET: str = os.environ.get("REPORT_BUCKET", "")

# ── Logging ──────────────────────────────────────────────────────────
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_FORMAT: str = os.environ.get("LOG_FORMAT", "json")  # "json" or "text"

# ── Boto Retry Config ───────────────────────────────────────────────
# Applied to every boto3 client created through src.clients
BOTO_MAX_RETRIES: int = int(os.environ.get("BOTO_MAX_RETRIES", "5"))
BOTO_RETRY_MODE: str = os.environ.get("BOTO_RETRY_MODE", "adaptive")
