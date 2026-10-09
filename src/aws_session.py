"""
Accounts and sessions for multi-account operation.

- home_account_id(): account of the running credentials (cached)
- session_for(account_id): boto3 Session for that account (assumes MULTI_ACCOUNT_ROLE_NAME
  in member accounts; credentials are cached until shortly before they expire)
- target_accounts(): accounts to scan, from COMPLIANCE_ACCOUNTS
- account_directory(): {account_id: {name, ou_id, ou_name}} from AWS Organizations,
  cached in SQLite so dashboards and leaderboards never call Organizations per request
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import boto3

from src.config import (
    COMPLIANCE_ACCOUNTS,
    DEFAULT_REGION,
    MULTI_ACCOUNT_EXTERNAL_ID,
    MULTI_ACCOUNT_ROLE_NAME,
)
from src.logging_config import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_home_account: Optional[str] = None
_sessions: Dict[str, tuple] = {}  # account -> (session, expires_at_epoch)
_DIRECTORY_TTL = 24 * 3600


def home_account_id() -> str:
    global _home_account
    if _home_account:
        return _home_account
    from src.clients import get_sts_client
    _home_account = get_sts_client().get_caller_identity()["Account"]
    return _home_account


def session_for(account_id: Optional[str]) -> Optional[boto3.Session]:
    """None → use default credentials. Otherwise an assumed-role session for a member account."""
    if not account_id:
        return None
    try:
        if account_id == home_account_id():
            return None
    except Exception:
        pass
    with _lock:
        cached = _sessions.get(account_id)
        if cached and cached[1] - time.time() > 120:
            return cached[0]
    from src.clients import get_sts_client
    params = {
        "RoleArn": f"arn:aws:iam::{account_id}:role/{MULTI_ACCOUNT_ROLE_NAME}",
        "RoleSessionName": "TagGovernance",
        "DurationSeconds": 3600,
    }
    if MULTI_ACCOUNT_EXTERNAL_ID:
        params["ExternalId"] = MULTI_ACCOUNT_EXTERNAL_ID
    creds = get_sts_client().assume_role(**params)["Credentials"]
    session = boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )
    expires = creds["Expiration"]
    expires_at = expires.timestamp() if hasattr(expires, "timestamp") else time.time() + 3000
    with _lock:
        _sessions[account_id] = (session, expires_at)
    return session


def client_for(service: str, region: str, account_id: Optional[str] = None):
    from src.clients import get_client
    return get_client(service, region, session_for(account_id))


def target_accounts() -> List[Optional[str]]:
    """
    Accounts to scan. [None] means "the running credentials' account" (no STS call needed;
    the account ID is read from each ARN). Failures to list the Organization fall back to it.
    """
    raw = COMPLIANCE_ACCOUNTS
    if not raw:
        return [None]
    if raw.lower() == "all":
        try:
            org = client_for("organizations", DEFAULT_REGION)
            ids = []
            for page in org.get_paginator("list_accounts").paginate():
                ids += [a["Id"] for a in page.get("Accounts", []) if a.get("Status") == "ACTIVE"]
            return sorted(ids) or [None]
        except Exception as e:
            logger.error("Could not list Organization accounts (%s); scanning the home account only", e)
            return [None]
    return [a.strip() for a in raw.split(",") if a.strip()]


# ── Organization directory (names / OUs) ────────────────────────────

def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            account_id TEXT PRIMARY KEY,
            name TEXT,
            ou_id TEXT,
            ou_name TEXT,
            updated_at TEXT NOT NULL
        )
    """)


def refresh_account_directory() -> Dict[str, Dict[str, str]]:
    """Read account names and parent OUs from AWS Organizations into SQLite."""
    from src.db import get_connection
    org = client_for("organizations", DEFAULT_REGION)
    ou_names: Dict[str, str] = {}
    rows = []
    for page in org.get_paginator("list_accounts").paginate():
        for acct in page.get("Accounts", []):
            ou_id, ou_name = "", ""
            try:
                parents = org.list_parents(ChildId=acct["Id"]).get("Parents", [])
                if parents:
                    ou_id = parents[0]["Id"]
                    if parents[0].get("Type") == "ROOT":
                        ou_name = "Root"
                    else:
                        if ou_id not in ou_names:
                            ou_names[ou_id] = org.describe_organizational_unit(
                                OrganizationalUnitId=ou_id)["OrganizationalUnit"]["Name"]
                        ou_name = ou_names[ou_id]
            except Exception as e:
                logger.warning("Could not resolve OU for %s: %s", acct["Id"], e)
            rows.append((acct["Id"], acct.get("Name", ""), ou_id, ou_name,
                         datetime.now(timezone.utc).isoformat()))
    conn = get_connection()
    with conn:
        _ensure_table(conn)
        conn.executemany(
            "INSERT OR REPLACE INTO accounts (account_id, name, ou_id, ou_name, updated_at) VALUES (?,?,?,?,?)",
            rows)
    logger.info("Account directory refreshed: %d accounts", len(rows))
    return {r[0]: {"name": r[1], "ou_id": r[2], "ou_name": r[3]} for r in rows}


def account_directory(refresh_if_stale: bool = False) -> Dict[str, Dict[str, str]]:
    """Cached directory; optionally refreshed from Organizations when older than a day."""
    from src.db import get_connection
    conn = get_connection()
    with conn:
        _ensure_table(conn)
    rows = conn.execute("SELECT account_id, name, ou_id, ou_name, updated_at FROM accounts").fetchall()
    directory = {r["account_id"]: {"name": r["name"] or "", "ou_id": r["ou_id"] or "", "ou_name": r["ou_name"] or ""}
                 for r in rows}
    if refresh_if_stale:
        newest = max((r["updated_at"] for r in rows), default=None)
        stale = not newest or (time.time() - datetime.fromisoformat(newest).timestamp()) > _DIRECTORY_TTL
        if stale:
            try:
                directory = refresh_account_directory()
            except Exception as e:
                logger.info("Organizations directory unavailable (%s); using account IDs only", e)
    return directory
