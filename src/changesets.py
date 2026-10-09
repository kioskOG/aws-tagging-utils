"""
Change sets: previewable, auditable, undoable tag changes.

Every bulk fix and every propagation fix goes through here:

    plan    = build_plan(targets, overwrite)          # live current tags → per-resource diff
    preview = preview(targets, overwrite)              # plan + compliance before/after + token
    result  = apply(targets, overwrite, actor, ...)    # re-plans live, optional token check, tags, records
    undo(change_set_id, actor)                         # restores previous values, skipping conflicts

A target is {"arn": str, "tags": {key: value}}. Only keys in `tags` are touched; other tags on
the resource are never modified. With overwrite=False, existing keys with a different value are
left alone and reported as "skipped".

Undo is conflict-safe: a key is only restored if its current value is still the value this
change set wrote. If someone changed it since, the key is reported as a conflict and left alone.

Besides tag operations, items can carry special "ops" (e.g. ASG PropagateAtLaunch, ECS
propagateTags) registered by src.propagation via register_op().
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.arn_utils import account_of, region_of
from src.aws_session import session_for
from src.clients import get_client
from src.config import CHANGESET_RETENTION_DAYS, DEFAULT_REGION, MANDATORY_TAGS, MAX_BULK_RESOURCES
from src.errors import APIError
from src.logging_config import get_logger

logger = get_logger(__name__)

_TAG_BATCH = 20          # TagResources / UntagResources max ARNs per call
_READ_BATCH = 100        # GetResources ResourceARNList max


# ── Engine & ops ────────────────────────────────────────────────────

def _engine():
    from src.tag_report import governance_engine
    return governance_engine


# op name -> (apply_fn(item) -> None, undo_fn(item) -> None)
_OPS: Dict[str, Tuple[Callable[[Dict[str, Any]], None], Callable[[Dict[str, Any]], None]]] = {}


def register_op(name: str, apply_fn: Callable[[Dict[str, Any]], None], undo_fn: Callable[[Dict[str, Any]], None]) -> None:
    _OPS[name] = (apply_fn, undo_fn)


def _home_partition_account(arn: str) -> Optional[str]:
    """Account to act in for this ARN (None = running credentials)."""
    acct = account_of(arn)
    return None if acct in ("unknown", "", "aws") else acct


def _group_key(arn: str) -> Tuple[Optional[str], str]:
    return _home_partition_account(arn), region_of(arn) or DEFAULT_REGION


def _tagging(account: Optional[str], region: str):
    try:
        session = session_for(account)
    except Exception:
        session = None  # home account / no STS: use default credentials
    return get_client("resourcegroupstaggingapi", region, session)


# ── Reading current state ───────────────────────────────────────────

def fetch_current_tags(arns: List[str]) -> Dict[str, Dict[str, str]]:
    """Live tags per ARN (resources the tagging API doesn't return are treated as untagged)."""
    groups: Dict[Tuple[Optional[str], str], List[str]] = {}
    for arn in arns:
        groups.setdefault(_group_key(arn), []).append(arn)
    out: Dict[str, Dict[str, str]] = {a: {} for a in arns}
    for (account, region), group in groups.items():
        client = _tagging(account, region)
        for i in range(0, len(group), _READ_BATCH):
            batch = group[i:i + _READ_BATCH]
            for page in client.get_paginator("get_resources").paginate(ResourceARNList=batch):
                for item in page.get("ResourceTagMappingList", []):
                    out[item["ResourceARN"]] = {t["Key"]: t.get("Value", "") for t in item.get("Tags", [])}
    return out


# ── Planning ────────────────────────────────────────────────────────

def _validate_targets(targets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(targets, list) or not targets:
        raise APIError("At least one target is required.", status_code=400, error_code="INVALID_REQUEST")
    if len(targets) > MAX_BULK_RESOURCES:
        raise APIError(f"At most {MAX_BULK_RESOURCES} resources per change set.", status_code=400,
                       error_code="TOO_MANY_RESOURCES")
    merged: Dict[str, Dict[str, str]] = {}
    for t in targets:
        arn = str((t or {}).get("arn", "")).strip()
        tags = (t or {}).get("tags") or {}
        if not arn.startswith("arn:"):
            raise APIError(f"Invalid ARN: {arn!r}", status_code=400, error_code="INVALID_REQUEST")
        if not isinstance(tags, dict) or not tags:
            raise APIError(f"No tags given for {arn}", status_code=400, error_code="INVALID_REQUEST")
        for k, v in tags.items():
            k = str(k).strip()
            if not k or k.lower().startswith("aws:"):
                raise APIError(f"Invalid tag key {k!r} (empty or reserved 'aws:' prefix)", status_code=400,
                               error_code="INVALID_REQUEST")
            merged.setdefault(arn, {})[k] = str(v).strip()
    return [{"arn": a, "tags": t} for a, t in merged.items()]


def build_plan(targets: List[Dict[str, Any]], overwrite: bool = False,
               current: Optional[Dict[str, Dict[str, str]]] = None) -> List[Dict[str, Any]]:
    targets = _validate_targets(targets)
    current = current if current is not None else fetch_current_tags([t["arn"] for t in targets])
    engine = _engine()
    plan = []
    for t in targets:
        arn, wanted = t["arn"], t["tags"]
        cur = current.get(arn, {})
        # Validate requested values (allowed values / regex) as a partial update
        validation = engine.evaluate(wanted, resource_id=arn, partial=True)
        normalized = validation.normalized_tags or wanted
        changes, skipped = {}, {}
        for k, v in normalized.items():
            if cur.get(k) == v:
                continue
            if k in cur and not overwrite:
                skipped[k] = {"current": cur[k], "requested": v}
                continue
            changes[k] = {"before": cur.get(k), "after": v}
        after = dict(cur)
        after.update({k: c["after"] for k, c in changes.items()})
        before_eval = engine.evaluate(cur, resource_id=arn, extra_required=MANDATORY_TAGS)
        after_eval = engine.evaluate(after, resource_id=arn, extra_required=MANDATORY_TAGS)
        plan.append({
            "arn": arn,
            "changes": changes,
            "skipped": skipped,
            "invalid": [v.to_dict() for v in validation.violations],
            "compliant_before": before_eval.compliant,
            "compliant_after": after_eval.compliant,
            "remaining_violations": [v.to_dict() for v in after_eval.violations],
        })
    return plan


def plan_token(plan: List[Dict[str, Any]]) -> str:
    canonical = [{"arn": p["arn"], "changes": p["changes"]} for p in sorted(plan, key=lambda p: p["arn"])]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:32]


def _plan_summary(plan: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "resources": len(plan),
        "resources_changing": sum(1 for p in plan if p["changes"] and not p["invalid"]),
        "keys_added": sum(1 for p in plan for c in p["changes"].values() if c["before"] is None),
        "keys_changed": sum(1 for p in plan for c in p["changes"].values() if c["before"] is not None),
        "keys_skipped": sum(len(p["skipped"]) for p in plan),
        "invalid_resources": sum(1 for p in plan if p["invalid"]),
        "compliant_before": sum(1 for p in plan if p["compliant_before"]),
        "compliant_after": sum(1 for p in plan if p["compliant_after"] and not p["invalid"])
                           + sum(1 for p in plan if p["invalid"] and p["compliant_before"]),
    }


def preview(targets: List[Dict[str, Any]], overwrite: bool = False) -> Dict[str, Any]:
    plan = build_plan(targets, overwrite)
    return {"items": plan, "summary": _plan_summary(plan), "preview_token": plan_token(plan), "overwrite": overwrite}


# ── Applying ────────────────────────────────────────────────────────

def _tag_batches(items: List[Dict[str, Any]]) -> Dict[Tuple[Optional[str], str, str], List[Dict[str, Any]]]:
    """Group items by account, region and identical tag payload, so one call tags many ARNs."""
    groups: Dict[Tuple[Optional[str], str, str], List[Dict[str, Any]]] = {}
    for it in items:
        payload = json.dumps(it["_tags"], sort_keys=True)
        account, region = _group_key(it["arn"])
        groups.setdefault((account, region, payload), []).append(it)
    return groups


def _run_tagging(items: List[Dict[str, Any]], untag: bool = False) -> None:
    """items carry `_tags` (dict to set) or `_keys` (list to remove); results written back."""
    for (account, region, payload), group in _tag_batches(items).items():
        client = _tagging(account, region)
        for i in range(0, len(group), _TAG_BATCH):
            batch = group[i:i + _TAG_BATCH]
            arns = [it["arn"] for it in batch]
            try:
                if untag:
                    resp = client.untag_resources(ResourceARNList=arns, TagKeys=json.loads(payload))
                else:
                    resp = client.tag_resources(ResourceARNList=arns, Tags=json.loads(payload))
                failed = resp.get("FailedResourcesMap") or {}
            except Exception as e:
                failed = {a: {"ErrorMessage": str(e)} for a in arns}
            for it in batch:
                if it["arn"] in failed:
                    it["result"] = "FAILED"
                    it["error"] = failed[it["arn"]].get("ErrorMessage") or failed[it["arn"]].get("ErrorCode")


def apply(targets: List[Dict[str, Any]], overwrite: bool, actor: str, kind: str = "bulk_fix",
          description: str = "", preview_token: Optional[str] = None,
          ops: Optional[List[Dict[str, Any]]] = None, request_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Apply tag changes (and optional special ops) and record an undoable change set.
    If `preview_token` is given and the live plan differs from what was previewed, nothing is
    changed and a 409 PLAN_CHANGED is raised.
    """
    plan = build_plan(targets, overwrite) if targets else []
    if preview_token and plan_token(plan) != preview_token:
        raise APIError("Resources changed since the preview. Preview again before applying.",
                       status_code=409, error_code="PLAN_CHANGED")

    items: List[Dict[str, Any]] = []
    to_tag = []
    for p in plan:
        item = {"op": "tags", "arn": p["arn"],
                "before": {k: c["before"] for k, c in p["changes"].items()},
                "after": {k: c["after"] for k, c in p["changes"].items()},
                "skipped": p["skipped"]}
        if p["invalid"]:
            item.update(result="INVALID", error="; ".join(f"{v['tag']}: {v.get('expected', v['type'])}" for v in p["invalid"]))
        elif not p["changes"]:
            item["result"] = "NO_CHANGE"
        else:
            item.update(result="SUCCESS", _tags=item["after"])
            to_tag.append(item)
        items.append(item)
    _run_tagging(to_tag)

    for op in ops or []:
        item = dict(op)
        fn = _OPS.get(item.get("op"))
        try:
            if not fn:
                raise ValueError(f"Unknown op {item.get('op')}")
            fn[0](item)
            item["result"] = "SUCCESS"
        except Exception as e:
            item.update(result="FAILED", error=str(e))
        items.append(item)

    for it in items:
        it.pop("_tags", None)
    return _record(kind, description, actor, items, request_id=request_id)


def _status(items: List[Dict[str, Any]]) -> str:
    done = [i for i in items if i.get("result") in ("SUCCESS", "FAILED")]
    if not done:
        return "NO_CHANGE"
    ok = sum(1 for i in done if i["result"] == "SUCCESS")
    return "APPLIED" if ok == len(done) else ("FAILED" if ok == 0 else "PARTIAL")


def _summary(items: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for i in items:
        out[i.get("result", "UNKNOWN")] = out.get(i.get("result", "UNKNOWN"), 0) + 1
    out["total"] = len(items)
    return out


def _record(kind: str, description: str, actor: str, items: List[Dict[str, Any]],
            undo_of: Optional[str] = None, request_id: Optional[str] = None) -> Dict[str, Any]:
    from src.db import get_connection, init_db, insert_audit_log
    from src.observability.metrics import record_tag_writes
    init_db()
    cs_id = "cs-" + uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    status = _status(items)
    summary = _summary(items)
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO change_sets (id, created_at, actor, kind, description, status, summary_json, items_json, undo_of) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cs_id, now, actor, kind, description, status, json.dumps(summary), json.dumps(items), undo_of))
        if undo_of:
            clean = not any(i.get("result") in ("CONFLICT", "FAILED") or i.get("conflicts") for i in items)
            conn.execute("UPDATE change_sets SET undone_by_changeset = ?, status = ? WHERE id = ?",
                         (cs_id, "UNDONE" if clean else "UNDO_PARTIAL", undo_of))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=CHANGESET_RETENTION_DAYS)).isoformat()
        conn.execute("DELETE FROM change_sets WHERE created_at < ?", (cutoff,))

    action = "TAG_UNDO" if undo_of else f"TAG_{kind.upper()}"
    for it in items:
        if it.get("result") in ("SUCCESS", "FAILED"):
            try:
                insert_audit_log(actor, action, it.get("arn") or it.get("target"), it["result"],
                                 details={"change_set_id": cs_id, "before": it.get("before"), "after": it.get("after"),
                                          "op": it.get("op"), "error": it.get("error")},
                                 request_id=request_id)
            except Exception as e:
                logger.error("Audit log write failed for change set %s: %s", cs_id, e)
    record_tag_writes(summary.get("SUCCESS", 0), summary.get("FAILED", 0))
    logger.info("Change set %s (%s) by %s: %s", cs_id, kind, actor, summary)
    return {"id": cs_id, "created_at": now, "actor": actor, "kind": kind, "description": description,
            "status": status, "summary": summary, "items": items, "undo_of": undo_of}


# ── Reading & undo ──────────────────────────────────────────────────

def get_change_set(cs_id: str) -> Optional[Dict[str, Any]]:
    from src.db import get_connection, init_db
    init_db()
    row = get_connection().execute("SELECT * FROM change_sets WHERE id = ?", (cs_id,)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "created_at": row["created_at"], "actor": row["actor"], "kind": row["kind"],
            "description": row["description"], "status": row["status"],
            "summary": json.loads(row["summary_json"] or "{}"), "items": json.loads(row["items_json"]),
            "undo_of": row["undo_of"], "undone_by": row["undone_by_changeset"]}


def list_change_sets(limit: int = 50) -> List[Dict[str, Any]]:
    from src.db import get_connection, init_db
    init_db()
    rows = get_connection().execute(
        "SELECT id, created_at, actor, kind, description, status, summary_json, undo_of, undone_by_changeset "
        "FROM change_sets ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r["id"], "created_at": r["created_at"], "actor": r["actor"], "kind": r["kind"],
             "description": r["description"], "status": r["status"], "summary": json.loads(r["summary_json"] or "{}"),
             "undo_of": r["undo_of"], "undone_by": r["undone_by_changeset"]} for r in rows]


def undo(cs_id: str, actor: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    cs = get_change_set(cs_id)
    if not cs:
        raise APIError("Change set not found", status_code=404, error_code="NOT_FOUND")
    if cs["undone_by"]:
        raise APIError(f"Change set already undone by {cs['undone_by']}", status_code=409, error_code="ALREADY_UNDONE")
    if cs["undo_of"]:
        raise APIError("An undo cannot itself be undone; apply a new change instead.", status_code=409,
                       error_code="NOT_UNDOABLE")

    tag_items = [i for i in cs["items"] if i.get("op") == "tags" and i.get("result") == "SUCCESS"]
    current = fetch_current_tags([i["arn"] for i in tag_items]) if tag_items else {}
    undo_items: List[Dict[str, Any]] = []
    set_calls: List[Dict[str, Any]] = []
    remove_calls: List[Dict[str, Any]] = []
    for it in tag_items:
        cur = current.get(it["arn"], {})
        restore, remove, conflicts = {}, [], {}
        for k, written in it["after"].items():
            if cur.get(k) != written:
                # Changed by someone else since this change set: leave it alone
                conflicts[k] = {"written": written, "current": cur.get(k)}
            elif it["before"].get(k) is None:
                remove.append(k)
            else:
                restore[k] = it["before"][k]
        if restore:
            set_calls.append({"arn": it["arn"], "_tags": restore, "result": "SUCCESS"})
        if remove:
            remove_calls.append({"arn": it["arn"], "_tags": sorted(remove), "result": "SUCCESS"})
        undo_items.append({
            "op": "tags", "arn": it["arn"],
            "before": {k: cur.get(k) for k in list(restore) + remove},
            "after": dict(restore), "removed": sorted(remove), "conflicts": conflicts,
            "result": "SUCCESS" if (restore or remove) else ("CONFLICT" if conflicts else "NO_CHANGE"),
        })
    _run_tagging(set_calls)
    _run_tagging(remove_calls, untag=True)
    errors = {c["arn"]: c.get("error") for c in set_calls + remove_calls if c["result"] == "FAILED"}
    for item in undo_items:
        if item["arn"] in errors:
            item.update(result="FAILED", error=errors[item["arn"]])

    for op_item in (i for i in cs["items"] if i.get("op") not in ("tags", None) and i.get("result") == "SUCCESS"):
        item = dict(op_item)
        fn = _OPS.get(item["op"])
        try:
            if not fn:
                raise ValueError(f"Unknown op {item['op']}")
            fn[1](item)
            item["result"] = "SUCCESS"
        except Exception as e:
            item.update(result="FAILED", error=str(e))
        undo_items.append(item)

    return _record("undo", f"Undo of {cs_id}", actor, undo_items, undo_of=cs_id, request_id=request_id)
