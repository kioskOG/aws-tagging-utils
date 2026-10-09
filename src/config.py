"""
Centralized configuration for AWS Tagging Utilities.

All environment variables and constants are read once here and imported
by every other module.  This avoids scattered os.environ.get() calls
and the subtle region-mismatch bugs that follow.
"""

from __future__ import annotations

import os
import logging
from typing import List

logger = logging.getLogger(__name__)


# ── AWS ──────────────────────────────────────────────────────────────
DEFAULT_REGION: str = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-east-2"))

# ── Tagging Governance ───────────────────────────────────────────────
OWNER_TAG_KEY: str = os.environ.get("OWNER_TAG_KEY", "Owner")

MANDATORY_TAGS: List[str] = [
    t.strip()
    for t in os.environ.get("MANDATORY_TAGS", "Owner").split(",")
    if t.strip()
]

# ── Advanced Tag Governance ──────────────────────────────────────────
GOVERNANCE_SCHEMA_PATH: str = os.environ.get("GOVERNANCE_SCHEMA_PATH", "config/tag-schema.yaml")
GOVERNANCE_UNKNOWN_TAGS: str = os.environ.get("GOVERNANCE_UNKNOWN_TAGS", "warn")
GOVERNANCE_STRICT_MODE: bool = os.environ.get("GOVERNANCE_STRICT_MODE", "true").lower() == "true"
GOVERNANCE_NORMALIZATION: bool = os.environ.get("GOVERNANCE_NORMALIZATION", "true").lower() == "true"

# ── Enforcement & Remediation (Part 2) ───────────────────────────────
GOVERNANCE_DYNAMODB_TABLE: str = os.environ.get("GOVERNANCE_DYNAMODB_TABLE", "TagGovernanceState")
GOVERNANCE_SNS_TOPIC_ARN: str = os.environ.get("GOVERNANCE_SNS_TOPIC_ARN", "")
GOVERNANCE_REMEDIATION_ENABLED: bool = os.environ.get("GOVERNANCE_REMEDIATION_ENABLED", "true").lower() == "true"
GOVERNANCE_GRACE_PERIOD_DAYS: int = int(os.environ.get("GOVERNANCE_GRACE_PERIOD_DAYS", "7"))
GOVERNANCE_TERMINATION_ENABLED: bool = os.environ.get("GOVERNANCE_TERMINATION_ENABLED", "false").lower() == "true"
MAX_REMEDIATION_ATTEMPTS: int = int(os.environ.get("MAX_REMEDIATION_ATTEMPTS", "3"))
MULTI_ACCOUNT_ROLE_NAME: str = os.environ.get("MULTI_ACCOUNT_ROLE_NAME", "AWSOrganizationTagGovernanceRole")

# ── Worker & Async (Phase 2D) ────────────────────────────────────────
WORKER_ENABLED: bool = os.environ.get("WORKER_ENABLED", "false").lower() == "true"
SQS_QUEUE_URL: str = os.environ.get("SQS_QUEUE_URL", "")
SQS_WAIT_TIME_SECONDS: int = int(os.environ.get("SQS_WAIT_TIME_SECONDS", "20"))
SQS_VISIBILITY_TIMEOUT_SECONDS: int = int(os.environ.get("SQS_VISIBILITY_TIMEOUT_SECONDS", "900")) # 15 min default
SQS_MAX_MESSAGES: int = int(os.environ.get("SQS_MAX_MESSAGES", "10"))
WORKER_SHUTDOWN_TIMEOUT_SECONDS: int = int(os.environ.get("WORKER_SHUTDOWN_TIMEOUT_SECONDS", "30"))

# ── FinOps, Security & Enterprise (Part 3) ───────────────────────────
FINOPS_ENABLED: bool = os.environ.get("FINOPS_ENABLED", "true").lower() == "true"
FINOPS_AUTO_ACTIVATE_COST_TAGS: bool = os.environ.get("FINOPS_AUTO_ACTIVATE_COST_TAGS", "false").lower() == "true"
DRIFT_ENABLED: bool = os.environ.get("DRIFT_ENABLED", "true").lower() == "true"
DRIFT_AUTO_REVERT: bool = os.environ.get("DRIFT_AUTO_REVERT", "false").lower() == "true"
RBAC_ENABLED: bool = os.environ.get("RBAC_ENABLED", "false").lower() == "true"
OBSERVABILITY_ENABLED: bool = os.environ.get("OBSERVABILITY_ENABLED", "true").lower() == "true"

# ── Metrics ──────────────────────────────────────────────────────────
# Prometheus /metrics is served when OBSERVABILITY_ENABLED=true.
# CloudWatch EMF lines (stdout) are emitted when METRICS_EMF_ENABLED=true.
METRICS_EMF_ENABLED: bool = os.environ.get("METRICS_EMF_ENABLED", "false").lower() == "true"
METRICS_NAMESPACE: str = os.environ.get("METRICS_NAMESPACE", "TagGovernance")
# Optional bearer token required to scrape /metrics (empty = open, rely on network controls)
METRICS_AUTH_TOKEN: str = os.environ.get("METRICS_AUTH_TOKEN", "")

# ── Compliance scanning ──────────────────────────────────────────────
# Regions scanned by background/auto refresh: comma list, or "all". Empty = DEFAULT_REGION.
COMPLIANCE_REGIONS: str = os.environ.get("COMPLIANCE_REGIONS", "")
# Number of completed scans kept in SQLite (older scans and their resources are pruned)
SCAN_RETENTION: int = max(1, int(os.environ.get("SCAN_RETENTION", "30")))
# Server-side scheduled scans (web app): seconds between checks; 0 disables.
# A scan runs when the cache is older than COMPLIANCE_CACHE_TTL_SECONDS.
COMPLIANCE_SCAN_INTERVAL_SECONDS: int = int(os.environ.get("COMPLIANCE_SCAN_INTERVAL_SECONDS", "0"))

# ── Auth ─────────────────────────────────────────────────────────────
# Secure default: verify ALB OIDC unless explicitly set to local_dev.
AUTH_MODE_DEFAULT: str = "alb_oidc"

# ── MCP server ───────────────────────────────────────────────────────
# When true, MCP write tools (write_tags, apply_governance, sync_tags) are refused.
MCP_READ_ONLY: bool = os.environ.get("MCP_READ_ONLY", "false").lower() == "true"

APP_VERSION: str = os.environ.get("APP_VERSION", "0.4.0")


def _csv(name: str, default: str = "") -> List[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


# ── Inventory ────────────────────────────────────────────────────────
# tagging           → Resource Groups Tagging API only (sees resources that are/were tagged)
# resource_explorer → AWS Resource Explorer inventory merged with tagging API tags
#                     (also finds resources that were never tagged)
INVENTORY_SOURCE: str = os.environ.get("INVENTORY_SOURCE", "tagging").strip().lower()
# Region holding the Resource Explorer aggregator index (default: each scanned region's local index)
RESOURCE_EXPLORER_REGION: str = os.environ.get("RESOURCE_EXPLORER_REGION", "")
RESOURCE_EXPLORER_VIEW_ARN: str = os.environ.get("RESOURCE_EXPLORER_VIEW_ARN", "")
# mapped → evaluate only RESOURCE_TYPE_MAP types (others are reported as coverage gaps)
# all    → evaluate every discovered resource type
COMPLIANCE_SCOPE: str = os.environ.get("COMPLIANCE_SCOPE", "mapped").strip().lower()

# ── Multi-account ────────────────────────────────────────────────────
# ""        → only the account of the running credentials
# "all"     → every ACTIVE account in the AWS Organization (needs organizations:ListAccounts)
# "1,2,..." → explicit account IDs (MULTI_ACCOUNT_ROLE_NAME is assumed in each)
COMPLIANCE_ACCOUNTS: str = os.environ.get("COMPLIANCE_ACCOUNTS", "").strip()
MULTI_ACCOUNT_EXTERNAL_ID: str = os.environ.get("MULTI_ACCOUNT_EXTERNAL_ID", "")

# ── Leaderboards & history ───────────────────────────────────────────
# Tag whose value identifies a team (falls back to OWNER_TAG_KEY when absent on a resource)
TEAM_TAG_KEY: str = os.environ.get("TEAM_TAG_KEY", "Team")
# Scan summaries/leaderboard stats are kept this long (resource rows follow SCAN_RETENTION)
HISTORY_RETENTION_DAYS: int = max(1, int(os.environ.get("HISTORY_RETENTION_DAYS", "90")))

# ── Owner view ───────────────────────────────────────────────────────
OWNER_MATCH_TAGS: List[str] = _csv("OWNER_MATCH_TAGS", "Owner")
EXEMPTION_EXPIRY_WARNING_DAYS: int = int(os.environ.get("EXEMPTION_EXPIRY_WARNING_DAYS", "14"))
OWNER_COST_CACHE_SECONDS: int = int(os.environ.get("OWNER_COST_CACHE_SECONDS", "21600"))

# ── Bulk changes & propagation ───────────────────────────────────────
MAX_BULK_RESOURCES: int = int(os.environ.get("MAX_BULK_RESOURCES", "1000"))
# Tags never copied from parent to child resources
PROPAGATE_EXCLUDE_KEYS: List[str] = _csv("PROPAGATE_EXCLUDE_KEYS", "Name")
# Tags that are propagated: "schema" (every schema tag), "all" (every tag), or a comma list
PROPAGATE_KEYS: str = os.environ.get("PROPAGATE_KEYS", "schema").strip()
# Change sets (and their undo data) kept for this many days
CHANGESET_RETENTION_DAYS: int = int(os.environ.get("CHANGESET_RETENTION_DAYS", "90"))




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

def validate_config() -> str:
    """
    Validates AWS connectivity and optional configurations.
    Does not crash the application if AWS is unavailable, enabling local/cached usage.
    """
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    
    state = "VALID"
    
    # 1. Minimal AWS Identity/Connectivity Check
    try:
        from botocore.config import Config
        # Using a very short timeout just for startup validation
        sts = boto3.client('sts', region_name=DEFAULT_REGION, config=Config(connect_timeout=3, read_timeout=3))
        identity = sts.get_caller_identity()
        logger.info(f"Configuration Validation: AWS reachable. Assumed identity: {identity.get('Arn')}")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("AccessDenied", "AccessDeniedException"):
            logger.warning("Configuration Validation: AWS ACCESS DENIED. Application is running but AWS operations will fail.")
            state = "AWS ACCESS DENIED"
        elif code in ("UnrecognizedClientException", "InvalidClientTokenId", "InvalidAccessKeyId", "AuthFailure"):
            logger.warning("Configuration Validation: INVALID / MISSING AWS CREDENTIALS. Application is running in disconnected mode.")
            state = "INVALID / MISSING AWS CREDENTIALS"
        else:
            logger.warning("Configuration Validation: AWS SERVICE ERROR. Application is running but AWS may be unreachable.")
            state = "AWS SERVICE ERROR"
    except BotoCoreError as e:
        if "Credential" in e.__class__.__name__:
            logger.warning("Configuration Validation: INVALID / MISSING AWS CREDENTIALS. Application is running in disconnected mode.")
            state = "INVALID / MISSING AWS CREDENTIALS"
        else:
            logger.warning("Configuration Validation: AWS SERVICE ERROR. Application is running but AWS may be unreachable.")
            state = "AWS SERVICE ERROR"
    except Exception as e:
        # Safe logging without exposing exception secrets
        logger.warning(f"Configuration Validation: AWS SERVICE ERROR ({type(e).__name__}). Application is running but AWS may be unreachable.")
        state = "AWS SERVICE ERROR"
        
    # 2. Check Optional Configuration
    missing_optionals = []
    if not REPORT_BUCKET:
        missing_optionals.append("REPORT_BUCKET")
    if not GOVERNANCE_SNS_TOPIC_ARN:
        missing_optionals.append("GOVERNANCE_SNS_TOPIC_ARN")
        
    if missing_optionals:
        logger.warning(f"Configuration Validation: OPTIONAL CONFIGURATION MISSING ({', '.join(missing_optionals)}). Some background features may be disabled.")
        if state == "VALID":
            state = "OPTIONAL CONFIGURATION MISSING"
            
    return state
