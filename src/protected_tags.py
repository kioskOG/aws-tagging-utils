"""
Protected tags: schema keys with `protected: true`.

Writes
------
Only SecurityAdmin and PlatformAdmin may set or remove protected tags through the app
(enforced by web.app on every tag-writing endpoint).

Baselines
---------
The last authorized value of each protected tag is kept per resource in the governance
state store (item RES#<arn>, field `protected_tags`). It is updated by
- every successful app write that touches a protected key (record_write),
- compliance scans, for keys that have no baseline yet (seed_baselines),
- accepted changes on exempted resources.

Drift
-----
EventBridge "Tag Change on Resource" events carry the tags *after* a change, not before, so
each changed protected key is compared with its baseline (handle_tag_change_event).
DRIFT_ENABLED / DRIFT_AUTO_REVERT decide what happens to a drifted key:
  DRIFT_ENABLED=false            off     ignore tag change events
  DRIFT_AUTO_REVERT=false        detect  audit + notify, leave the resource as it is (default)
  DRIFT_AUTO_REVERT=true         revert  audit + notify and write the baseline value back
A key with no baseline yet is learned from the event, since there is nothing to compare with.
The revert itself raises another tag change event, which matches the baseline and is a no-op.
"""
from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from src.logging_config import get_logger

logger = get_logger(__name__)

_store = None


def _flag(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() == "true"


def drift_mode() -> str:
    """off | detect | revert, from DRIFT_ENABLED and DRIFT_AUTO_REVERT (read per call)."""
    if not _flag("DRIFT_ENABLED", True):
        return "off"
    return "revert" if _flag("DRIFT_AUTO_REVERT", False) else "detect"


def protected_keys() -> set[str]:
    try:
        from src.tag_report import schema_provider
        return {k for k, rule in schema_provider.get_schema().items() if getattr(rule, "protected", False)}
    except Exception as e:
        logger.error("Could not load protected tags from the schema: %s", e)
        return set()


def touched(keys: Iterable[str]) -> set[str]:
    """
    Protected keys among `keys`, matched case-insensitively and returned as written in the
    schema. Used for authorization, where a near-miss key must not slip through.
    """
    by_lower = {k.lower(): k for k in protected_keys()}
    return {by_lower[str(k).lower()] for k in keys if str(k).lower() in by_lower}


def _exact(keys: Iterable[str]) -> set[str]:
    """Protected keys among `keys`, exact case. AWS tag keys are case-sensitive, so baselines and
    drift only track the key exactly as the schema names it."""
    return set(keys) & protected_keys()


def get_store():
    global _store
    if _store is None:
        from src.governance.state import DynamoDBStateStore
        _store = DynamoDBStateStore()
    return _store


def set_store(store) -> None:
    """Tests and alternative deployments inject a store here."""
    global _store
    _store = store


# ── Baselines ───────────────────────────────────────────────────────

def get_baseline(arn: str) -> dict[str, str]:
    item = get_store().get_resource_state(arn) or {}
    return dict(item.get("protected_tags") or {})


def _put_baseline(arn: str, baseline: dict[str, str], actor: str, source: str) -> None:
    get_store().put_resource_state({
        "resource_id": arn,
        "protected_tags": baseline,
        "protected_updated_by": actor,
        "protected_source": source,
        "protected_updated_at": datetime.now(timezone.utc).isoformat(),
    })


def record_write(arn: str, set_tags: dict[str, str] | None = None, removed: Iterable[str] = (),
                 actor: str = "aws-tagging-utils", source: str = "app") -> None:
    """Store authorized values of protected keys written by the app. Never raises."""
    set_tags = set_tags or {}
    changed = _exact(list(set_tags) + list(removed))
    if not changed:
        return
    try:
        baseline = get_baseline(arn)
        for k in changed:
            if k in set_tags:
                baseline[k] = set_tags[k]
            else:
                baseline.pop(k, None)
        _put_baseline(arn, baseline, actor, source)
    except Exception as e:
        logger.error("Failed to record protected-tag baseline for %s: %s", arn, e)


def record_writes(arns: Iterable[str], set_tags: dict[str, str] | None = None, removed: Iterable[str] = (),
                  actor: str = "aws-tagging-utils", source: str = "app") -> None:
    removed = list(removed)
    if not _exact(list(set_tags or {}) + removed):
        return
    for arn in arns:
        record_write(arn, set_tags, removed, actor, source)


def seed_baselines(resources: Iterable[tuple[str, dict[str, str]]]) -> int:
    """Fill baselines for protected keys that have none yet (from a scan). Returns resources updated."""
    if drift_mode() == "off" or not protected_keys():
        return 0
    updated = 0
    for arn, tags in resources:
        if not arn:
            continue
        present = {k: tags[k] for k in _exact(tags)}
        if not present:
            continue
        try:
            baseline = get_baseline(arn)
            missing = {k: v for k, v in present.items() if k not in baseline}
            if missing:
                baseline.update(missing)
                _put_baseline(arn, baseline, "compliance-scan", "scan")
                updated += 1
        except Exception as e:
            logger.error("Failed to seed protected-tag baseline for %s: %s", arn, e)
            return updated
    return updated


# ── Drift ───────────────────────────────────────────────────────────

def _revert(arn: str, account: str | None, region: str, values: dict[str, str]) -> None:
    from src.aws_session import session_for
    from src.clients import get_tagging_client
    client = get_tagging_client(region, session_for(account))
    resp = client.tag_resources(ResourceARNList=[arn], Tags=values)
    failed = (resp.get("FailedResourcesMap") or {}).get(arn)
    if failed:
        raise RuntimeError(failed.get("ErrorMessage") or failed.get("ErrorCode") or "TagResources failed")


def handle_tag_change_event(event: dict[str, Any], exemption_manager=None, notifier=None) -> dict[str, Any]:
    """Evaluate an EventBridge 'Tag Change on Resource' event against protected-tag baselines."""
    from src.governance.audit import AuditLogger

    mode = drift_mode()
    if mode == "off":
        return {"statusCode": 200, "status": "DISABLED"}
    detail = event.get("detail") or {}
    arn = next(iter(event.get("resources") or []), None)
    if not arn:
        return {"statusCode": 400, "status": "INVALID_EVENT", "message": "No resource ARN in event"}
    changed = _exact(detail.get("changed-tag-keys") or [])
    if not changed:
        return {"statusCode": 200, "status": "NO_PROTECTED_CHANGE", "arn": arn}

    current: dict[str, str] = detail.get("tags") or {}
    account = event.get("account") or (arn.split(":")[4] if arn.count(":") >= 4 else None)
    region = event.get("region") or (arn.split(":")[3] if arn.count(":") >= 3 else "") or "us-east-1"
    resource_type = f"{detail.get('service', '')}:{detail.get('resource-type', '')}".strip(":")

    baseline = get_baseline(arn)
    learned = {k: current[k] for k in changed if k not in baseline and k in current}
    drift = {k: {"expected": baseline[k], "actual": current.get(k)}
             for k in sorted(changed) if k in baseline and current.get(k) != baseline[k]}

    result: dict[str, Any] = {"statusCode": 200, "arn": arn, "mode": mode, "drift": drift, "learned": sorted(learned)}
    if learned:
        baseline.update(learned)
        _put_baseline(arn, baseline, "drift-detector", "first-seen")
    if not drift:
        result["status"] = "NO_DRIFT"
        return result

    exemption = None
    if exemption_manager is not None:
        try:
            exemption = exemption_manager.is_exempt(account or "", resource_type, arn, current.get("Environment"))
        except Exception as e:
            logger.error("Exemption lookup failed for %s: %s", arn, e)
    if exemption:
        # Accept the change so the stale value isn't restored when the exemption ends
        for k, d in drift.items():
            if d["actual"] is None:
                baseline.pop(k, None)
            else:
                baseline[k] = d["actual"]
        _put_baseline(arn, baseline, "drift-detector", f"exemption:{exemption.get('id', '')}")
        result["status"] = "EXEMPT"
        AuditLogger.log("PROTECTED_TAG_DRIFT", "ACCEPT_EXEMPT", account or "", region, arn, resource_type,
                        "EXEMPT", actor="drift-detector", details={"drift": drift})
        return result

    action, outcome = "DETECT", "DETECTED"
    if mode == "revert":
        action = "REVERT"
        try:
            _revert(arn, account, region, {k: d["expected"] for k, d in drift.items()})
            outcome = "REVERTED"
        except Exception as e:
            outcome = "REVERT_FAILED"
            result["error"] = str(e)
            logger.error("Failed to revert protected tags on %s: %s", arn, e)
    result["status"] = outcome
    AuditLogger.log("PROTECTED_TAG_DRIFT", action, account or "", region, arn, resource_type, outcome,
                    actor="drift-detector", details={"drift": drift})
    if notifier is not None:
        keys = ", ".join(drift)
        verb = {"REVERTED": "were reverted", "REVERT_FAILED": "could NOT be reverted"}.get(outcome, "changed outside the app")
        try:
            notifier.notify("HIGH", f"Protected tags {keys} on {arn} {verb}", {"arn": arn, "drift": drift,
                                                                              "status": outcome, "mode": mode})
        except Exception as e:
            logger.error("Drift notification failed for %s: %s", arn, e)
    return result
