"""
Owner view and leaderboards.

Owner matching
--------------
A resource belongs to a user when any OWNER_MATCH_TAGS value (default: Owner) equals one of
the user's identifiers (user id, email), case-insensitively, or ends with "/<identifier>"
(the AWS SSO session format "AWSReservedSSO_Role_x/jane@example.com").

Leaderboards
------------
Compliance per team / account / OU / service from the latest scan, with the change versus the
newest scan at least 7 days older (week-over-week), read from scan_group_stats.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from src.config import (
    EXEMPTION_EXPIRY_WARNING_DAYS,
    OWNER_COST_CACHE_SECONDS,
    OWNER_MATCH_TAGS,
)
from src.logging_config import get_logger

logger = get_logger(__name__)

DIMENSIONS = ("team", "account", "ou", "service")


# ── Owner matching ──────────────────────────────────────────────────

def owner_identifiers(identity) -> List[str]:
    ids = [getattr(identity, "user_id", None), getattr(identity, "email", None)]
    return sorted({str(i).strip().lower() for i in ids if i and str(i).strip()})


def owner_matches(identifiers: Iterable[str], tags: Dict[str, str]) -> bool:
    ids = [i.lower() for i in identifiers if i]
    if not ids:
        return False
    lowered = {str(k).lower(): str(v).strip().lower() for k, v in (tags or {}).items()}
    for key in OWNER_MATCH_TAGS:
        value = lowered.get(key.lower())
        if not value:
            continue
        for ident in ids:
            if value == ident or value.endswith("/" + ident):
                return True
    return False


# ── Owner cost (Cost Explorer, cached) ──────────────────────────────

_cost_cache: Dict[tuple, tuple] = {}
_cost_lock = threading.Lock()


def _primary_cost_tag() -> str:
    import os
    from src.tag_report import schema_provider
    tag = os.environ.get("FINOPS_PRIMARY_TAG")
    if tag:
        return tag
    schema = schema_provider.get_schema()
    finops = [k for k, r in schema.items() if r.finops and r.finops.get("cost_allocation")]
    return finops[0] if finops else "CostCenter"


def owner_cost(owner_values: List[str]) -> Dict[str, Any]:
    """
    Month-to-date spend of resources tagged with these owner values, and the part of it that
    has no primary cost-allocation tag (unallocated). Requires the owner tag to be activated
    as a cost allocation tag. Cached OWNER_COST_CACHE_SECONDS (each CE call costs $0.01).
    """
    from src.clients import get_client
    owner_key = OWNER_MATCH_TAGS[0] if OWNER_MATCH_TAGS else "Owner"
    values = sorted(set(owner_values))
    cache_key = (owner_key, tuple(values))
    with _cost_lock:
        hit = _cost_cache.get(cache_key)
        if hit and time.time() - hit[0] < OWNER_COST_CACHE_SECONDS:
            return hit[1]

    today = date.today()
    period = {"Start": today.replace(day=1).isoformat(), "End": (today + timedelta(days=1)).isoformat()}
    ce = get_client("ce", "us-east-1")
    cost_tag = _primary_cost_tag()
    owner_filter = {"Tags": {"Key": owner_key, "Values": values, "MatchOptions": ["EQUALS"]}}

    def total(flt) -> float:
        resp = ce.get_cost_and_usage(TimePeriod=period, Granularity="MONTHLY", Metrics=["UnblendedCost"], Filter=flt)
        return round(sum(float(r["Total"]["UnblendedCost"]["Amount"]) for r in resp.get("ResultsByTime", [])), 2)

    owned = total(owner_filter)
    unallocated = total({"And": [owner_filter, {"Tags": {"Key": cost_tag, "MatchOptions": ["ABSENT"]}}]})
    result = {
        "period": period, "owner_tag": owner_key, "owner_values": values, "cost_allocation_tag": cost_tag,
        "owned_spend": owned, "unallocated_spend": unallocated,
        "unallocated_share_pct": round(unallocated * 100 / owned, 1) if owned else 0.0,
    }
    with _cost_lock:
        _cost_cache[cache_key] = (time.time(), result)
    return result


# ── Owner view ──────────────────────────────────────────────────────

def owner_view(identifiers: List[str], rows: List[Dict[str, Any]],
               active_exemptions: Optional[List[Dict[str, Any]]], exemption_manager=None,
               include_cost: bool = True) -> Dict[str, Any]:
    """`rows` are flattened compliance rows (web.app._flatten_resources)."""
    mine = [r for r in rows if owner_matches(identifiers, r.get("tags", {}))]
    owner_values = sorted({str(v) for r in mine for k, v in (r.get("tags") or {}).items()
                           if k.lower() in {t.lower() for t in OWNER_MATCH_TAGS}})
    non_compliant = [r for r in mine if r["status"] != "COMPLIANT"]
    violations: Dict[str, int] = {}
    for r in non_compliant:
        for v in r.get("violations", []):
            key = f"{v.get('tag')}:{v.get('type')}"
            violations[key] = violations.get(key, 0) + 1

    expiring = []
    exemptions_error = None
    if active_exemptions is None:
        exemptions_error = "Exemption store unavailable"
    else:
        horizon = datetime.now(timezone.utc) + timedelta(days=EXEMPTION_EXPIRY_WARNING_DAYS)
        from src.governance.exemptions import parse_expiry
        for ex in active_exemptions:
            try:
                exp = parse_expiry(ex.get("expires_at"))
            except ValueError:
                continue
            if not exp or exp > horizon:
                continue
            covered = [r["id"] for r in mine if exemption_manager and exemption_manager._matches(
                ex, r["account"], r["type"], r["id"], (r.get("tags") or {}).get("Environment"))]
            if covered:
                expiring.append({**ex, "resources": covered,
                                 "days_left": max(0, (exp - datetime.now(timezone.utc)).days)})

    cost: Optional[Dict[str, Any]] = None
    cost_error = None
    if include_cost and owner_values:
        try:
            cost = owner_cost(owner_values)
        except Exception as e:
            cost_error = (f"Cost Explorer unavailable: {str(e).rstrip('.')}. The owner tag must be activated as a "
                          "cost allocation tag (Billing → Cost allocation tags).")

    total = len(mine)
    return {
        "identifiers": identifiers,
        "owner_values": owner_values,
        "summary": {
            "total": total, "non_compliant": len(non_compliant),
            "compliance_pct": round((total - len(non_compliant)) * 100 / total, 1) if total else 100.0,
            "exemptions_expiring": len(expiring),
        },
        "violations": sorted(({"key": k, "tag": k.split(":")[0], "type": k.split(":")[1], "count": n}
                              for k, n in violations.items()), key=lambda x: -x["count"]),
        "resources": mine,
        "expiring_exemptions": sorted(expiring, key=lambda e: e["days_left"]),
        "exemptions_error": exemptions_error,
        "cost": cost,
        "cost_error": cost_error,
    }


# ── Leaderboards ────────────────────────────────────────────────────

def leaderboard(dimension: str, compare_days: int = 7) -> Dict[str, Any]:
    from src.db import find_scan_before, get_group_stats, init_db, latest_scan
    if dimension not in DIMENSIONS:
        raise ValueError(f"dimension must be one of {DIMENSIONS}")
    init_db()
    latest = latest_scan()
    if not latest:
        return {"dimension": dimension, "rows": [], "compared_to": None}
    cutoff = (datetime.fromisoformat(latest["timestamp"].replace("Z", "+00:00")) - timedelta(days=compare_days)).isoformat()
    prev = find_scan_before(cutoff)

    names: Dict[str, Dict[str, str]] = {}
    if dimension in ("account", "ou"):
        try:
            from src.aws_session import account_directory
            names = account_directory()
        except Exception:
            names = {}

    def stats_for(scan_id: int) -> Dict[str, tuple]:
        if dimension != "ou":
            return get_group_stats(dimension, scan_id)
        # OU is derived at query time from per-account stats and the *current* directory,
        # so OUs resolve even for scans taken before the directory was loaded.
        out: Dict[str, list] = {}
        for acct, (total, compliant) in get_group_stats("account", scan_id).items():
            ou = (names.get(acct) or {}).get("ou_name") or "(unknown OU)"
            v = out.setdefault(ou, [0, 0])
            v[0] += total
            v[1] += compliant
        return {k: tuple(v) for k, v in out.items()}

    now_stats = stats_for(latest["id"])
    prev_stats = stats_for(prev["id"]) if prev else {}

    rows = []
    for key, (total, compliant) in now_stats.items():
        pct = round(compliant * 100 / total, 1) if total else 100.0
        p = prev_stats.get(key)
        prev_pct = round(p[1] * 100 / p[0], 1) if p and p[0] else None
        rows.append({
            "key": key,
            "name": (names.get(key) or {}).get("name") or key,
            "ou": (names.get(key) or {}).get("ou_name") if dimension == "account" else None,
            "total": total, "compliant": compliant, "non_compliant": total - compliant,
            "compliance_pct": pct,
            "previous_pct": prev_pct,
            "delta": round(pct - prev_pct, 1) if prev_pct is not None else None,
        })
    rows.sort(key=lambda r: (-r["compliance_pct"], -r["total"], r["key"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    improved = sorted((r for r in rows if r["delta"] is not None), key=lambda r: -r["delta"])[:3]
    return {"dimension": dimension, "rows": rows, "scan_timestamp": latest["timestamp"],
            "compared_to": prev["timestamp"] if prev else None, "most_improved": improved}
