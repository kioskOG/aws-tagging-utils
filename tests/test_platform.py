"""
Tests for the platform features: full inventory, coverage gaps, multi-account scans,
leaderboards, owner view, bulk change sets (preview/apply/undo), suggestions and propagation.
AWS is replaced by in-memory fakes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict
from unittest.mock import MagicMock, patch

import pytest

A1, A2 = "111111111111", "222222222222"
VPC = f"arn:aws:ec2:us-east-1:{A1}:vpc/vpc-1"
SUBNET = f"arn:aws:ec2:us-east-1:{A1}:subnet/subnet-1"
BUCKET = "arn:aws:s3:::bucket-1"
QUEUE = f"arn:aws:sqs:us-east-1:{A2}:jobs"


class FakeTagging:
    """Minimal in-memory Resource Groups Tagging API."""

    def __init__(self, store: Dict[str, Dict[str, str]]):
        self.store = store
        self.calls = []

    def get_paginator(self, op):
        assert op == "get_resources"
        fake = self

        class P:
            def paginate(self, ResourceARNList=None, **kw):
                arns = ResourceARNList or list(fake.store)
                yield {"ResourceTagMappingList": [
                    {"ResourceARN": a, "Tags": [{"Key": k, "Value": v} for k, v in fake.store[a].items()]}
                    for a in arns if a in fake.store]}
        return P()

    def tag_resources(self, ResourceARNList, Tags):
        self.calls.append(("tag", list(ResourceARNList), dict(Tags)))
        for a in ResourceARNList:
            self.store.setdefault(a, {}).update(Tags)
        return {"FailedResourcesMap": {}}

    def untag_resources(self, ResourceARNList, TagKeys):
        self.calls.append(("untag", list(ResourceARNList), list(TagKeys)))
        for a in ResourceARNList:
            for k in TagKeys:
                self.store.get(a, {}).pop(k, None)
        return {"FailedResourcesMap": {}}


@pytest.fixture
def fake_aws():
    store = {
        VPC: {"Name": "net", "Owner": "platform", "Environment": "dev"},
        SUBNET: {"Name": "net-a"},
        BUCKET: {"Environment": "prod"},
    }
    fake = FakeTagging(store)
    with patch("src.changesets._tagging", return_value=fake):
        yield fake


@pytest.fixture
def client():
    from web.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── Inventory & coverage ────────────────────────────────────────────

def test_resource_explorer_merge_flags_never_tagged(monkeypatch):
    from src import inventory
    monkeypatch.setattr(inventory, "INVENTORY_SOURCE", "resource_explorer")
    monkeypatch.setattr(inventory, "session_for", lambda a: None)
    monkeypatch.setattr(inventory, "tagging_api_resources", lambda r, s, f: {VPC: {"Owner": "a"}})
    monkeypatch.setattr(inventory, "resource_explorer_resources", lambda r, s: {
        VPC: {"type": "ec2:vpc", "tags": None},
        SUBNET: {"type": "ec2:subnet", "tags": None},
    })
    out = inventory.collect("us-east-1")
    by_arn = {r["arn"]: r for r in out["resources"]}
    assert out["source"] == "resource_explorer"
    assert by_arn[VPC]["tags"] == {"Owner": "a"} and not by_arn[VPC]["never_tagged"]
    assert by_arn[SUBNET]["never_tagged"] and by_arn[SUBNET]["tags"] == {}


def test_resource_explorer_failure_falls_back(monkeypatch):
    from src import inventory
    monkeypatch.setattr(inventory, "INVENTORY_SOURCE", "resource_explorer")
    monkeypatch.setattr(inventory, "session_for", lambda a: None)
    monkeypatch.setattr(inventory, "tagging_api_resources", lambda r, s, f: {VPC: {}})

    def boom(r, s):
        raise RuntimeError("no index")
    monkeypatch.setattr(inventory, "resource_explorer_resources", boom)
    out = inventory.collect("us-east-1")
    assert out["source"] == "tagging" and "no index" in out["warning"]
    assert [r["arn"] for r in out["resources"]] == [VPC]


def _inv(*items):
    return {"resources": [{"arn": a, "tags": t, "resource_type": rt, "never_tagged": nt} for a, t, rt, nt in items],
            "source": "resource_explorer", "warning": None}


def test_report_counts_unmapped_types_and_never_tagged():
    from src import tag_report
    inv = _inv((VPC, {"Owner": "a", "Environment": "dev"}, "ec2:vpc", False),
               (SUBNET, {}, "ec2:subnet", True),
               (f"arn:aws:kafka:us-east-1:{A1}:cluster/c/1", {}, "kafka:cluster", True),
               (f"arn:aws:bedrock:us-east-1:{A1}:agent/x", {}, "bedrock:agent", False))
    with patch.object(tag_report, "collect_inventory", return_value=inv):
        report = tag_report.generate_report(["us-east-1"], ["Owner"], accounts=[None])
    cov = report["summary"]["coverage"]
    assert cov["unmapped_types"] == {"kafka:cluster": 1, "bedrock:agent": 1}
    assert cov["never_tagged"] == 1
    assert report["summary"]["total_resources"] == 2
    subnet = [r for r in report["regions"]["us-east-1"]["resources"] if r["ResourceARN"] == SUBNET][0]
    assert subnet["NeverTagged"] and not subnet["IsCompliant"]


def test_multi_account_partial_failure_is_not_a_region_failure():
    from src import tag_report

    def per_account(region, account, types):
        if account == A2:
            raise RuntimeError("AccessDenied: role missing")
        return _inv((VPC, {"Owner": "a", "Environment": "dev"}, "ec2:vpc", False))
    with patch.object(tag_report, "collect_inventory", side_effect=per_account):
        report = tag_report.generate_report(["us-east-1"], ["Owner"], accounts=[A1, A2])
    assert report["summary"]["failed_regions"] == []
    assert report["summary"]["failed_accounts"] == [A2]
    assert report["accounts"][A1]["compliance_score"] == 100.0
    assert "AccessDenied" in report["accounts"][A2]["errors"]["us-east-1"]


def test_coverage_endpoint(client):
    from src.cache_manager import save_report
    save_report({"timestamp": datetime.now(timezone.utc).isoformat(),
                 "summary": {"total_resources": 0, "compliant": 0, "non_compliant": 0, "compliance_score": 0,
                             "coverage": {"inventory_source": "tagging", "never_tagged": 0,
                                          "unmapped_types": {"kafka:cluster": 3}}},
                 "regions": {"us-east-1": {"resources": []}}})
    body = client.get("/api/inventory/coverage").get_json()
    assert body["unmapped_types"] == [{"resource_type": "kafka:cluster", "count": 3}]
    assert "INVENTORY_SOURCE=resource_explorer" in body["hint"]


# ── Leaderboards ────────────────────────────────────────────────────

def _scan(ts, resources):
    c = sum(r["IsCompliant"] for r in resources)
    return {"timestamp": ts.isoformat(), "summary": {"total_resources": len(resources), "compliant": c,
                                                      "non_compliant": len(resources) - c,
                                                      "compliance_score": round(c * 100 / len(resources), 2)},
            "regions": {"us-east-1": {"resources": resources}}}


def _res(arn, team, ok):
    return {"ResourceARN": arn, "IsCompliant": ok, "Violations": [], "Tags": {"Team": team}}


def test_leaderboard_week_over_week(client):
    from src.db import insert_scan, init_db
    init_db()
    now = datetime.now(timezone.utc)
    insert_scan(_scan(now - timedelta(days=8), [_res(VPC, "payments", False), _res(SUBNET, "payments", True),
                                                 _res(QUEUE, "data", True)]))
    insert_scan(_scan(now, [_res(VPC, "payments", True), _res(SUBNET, "payments", True), _res(QUEUE, "data", False)]))
    body = client.get("/api/leaderboard?dimension=team").get_json()
    rows = {r["key"]: r for r in body["rows"]}
    assert rows["payments"]["compliance_pct"] == 100.0 and rows["payments"]["delta"] == 50.0
    assert rows["data"]["delta"] == -100.0
    assert rows["payments"]["rank"] == 1
    assert body["most_improved"][0]["key"] == "payments"
    by_account = client.get("/api/leaderboard?dimension=account").get_json()
    assert {r["key"] for r in by_account["rows"]} == {A1, A2}


# ── Owner view ──────────────────────────────────────────────────────

def test_owner_matches_sso_session_names():
    from src.insights import owner_matches
    tags = {"Owner": "AWSReservedSSO_AWSAdministrator_90eb/Jatin.Sharma@nomupay.com"}
    assert owner_matches(["jatin.sharma@nomupay.com"], tags)
    assert not owner_matches(["sharma@nomupay.com"], tags)
    assert owner_matches(["platform"], {"owner": "Platform"})


def test_my_resources_endpoint(client, monkeypatch):
    import web.app as webapp
    from src.cache_manager import save_report
    from src.governance.state import MemoryStateStore
    monkeypatch.setenv("DEV_AUTH_USER", "jane:ApplicationOwner")
    monkeypatch.setenv("DEV_AUTH_EMAIL", "jane@example.com")
    store = MemoryStateStore()
    monkeypatch.setattr(webapp.exemption_manager, "state_store", store)
    webapp._exemptions_cache.invalidate()
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    webapp.exemption_manager.create_exemption({"resource_id": BUCKET, "reason": "legacy", "expires_at": soon}, "admin")
    save_report(_scan(datetime.now(timezone.utc), [
        {"ResourceARN": BUCKET, "IsCompliant": False, "Violations": [{"tag": "CostCenter", "type": "MISSING_REQUIRED"}],
         "Tags": {"Owner": "SSO_Role/jane@example.com"}},
        {"ResourceARN": VPC, "IsCompliant": True, "Violations": [], "Tags": {"Owner": "someone-else"}},
    ]))
    with patch("src.insights.owner_cost", return_value={"owned_spend": 100.0, "unallocated_spend": 25.0,
                                                        "unallocated_share_pct": 25.0}):
        body = client.get("/api/me/resources").get_json()
    assert [r["id"] for r in body["resources"]] == [BUCKET]
    assert body["summary"]["non_compliant"] == 1
    assert body["expiring_exemptions"][0]["resources"] == [BUCKET]
    assert body["cost"]["unallocated_share_pct"] == 25.0
    # non-admins can't look at other owners
    assert client.get("/api/me/resources?owner=someone-else").status_code == 403
    webapp._exemptions_cache.invalidate()


# ── Change sets ─────────────────────────────────────────────────────

def test_preview_add_change_and_skip(fake_aws):
    from src import changesets
    out = changesets.preview([{"arn": SUBNET, "tags": {"Owner": "platform", "Name": "renamed"}}], overwrite=False)
    item = out["items"][0]
    assert item["changes"] == {"Owner": {"before": None, "after": "platform"}}
    assert item["skipped"] == {"Name": {"current": "net-a", "requested": "renamed"}}
    assert out["summary"]["keys_added"] == 1 and out["summary"]["keys_skipped"] == 1


def test_apply_and_undo_restore_previous_state(fake_aws):
    from src import changesets
    before = {a: dict(t) for a, t in fake_aws.store.items()}
    cs = changesets.apply([{"arn": SUBNET, "tags": {"Owner": "platform"}},
                           {"arn": BUCKET, "tags": {"Environment": "dev", "Owner": "data"}}],
                          overwrite=True, actor="tester")
    assert cs["status"] == "APPLIED"
    assert fake_aws.store[BUCKET] == {"Environment": "dev", "Owner": "data"}
    undo = changesets.undo(cs["id"], "tester")
    assert undo["status"] == "APPLIED"
    assert fake_aws.store == before
    assert changesets.get_change_set(cs["id"])["status"] == "UNDONE"
    with pytest.raises(Exception):
        changesets.undo(cs["id"], "tester")  # already undone


def test_undo_skips_keys_changed_since(fake_aws):
    from src import changesets
    cs = changesets.apply([{"arn": SUBNET, "tags": {"Owner": "platform", "Environment": "dev"}}], False, "t")
    fake_aws.store[SUBNET]["Owner"] = "someone-else"  # changed outside the tool
    undo = changesets.undo(cs["id"], "t")
    item = undo["items"][0]
    assert item["conflicts"] == {"Owner": {"written": "platform", "current": "someone-else"}}
    assert fake_aws.store[SUBNET] == {"Name": "net-a", "Owner": "someone-else"}
    assert changesets.get_change_set(cs["id"])["status"] == "UNDO_PARTIAL"


def test_invalid_values_are_not_applied(fake_aws):
    from src import changesets
    cs = changesets.apply([{"arn": SUBNET, "tags": {"Environment": "production"}}], False, "t")
    assert cs["items"][0]["result"] == "INVALID"
    assert "Environment" not in fake_aws.store[SUBNET]


def test_plan_changed_since_preview(fake_aws):
    from src import changesets
    from src.errors import APIError
    targets = [{"arn": SUBNET, "tags": {"Owner": "platform"}}]
    token = changesets.preview(targets)["preview_token"]
    fake_aws.store[SUBNET]["Owner"] = "platform"   # someone fixed it meanwhile
    with pytest.raises(APIError) as e:
        changesets.apply(targets, False, "t", preview_token=token)
    assert e.value.status_code == 409


def test_bulk_api_roundtrip(client, fake_aws):
    pv = client.post("/api/bulk/preview", json={"arns": [SUBNET], "tags": {"Owner": "platform"}}).get_json()
    assert pv["summary"]["resources_changing"] == 1
    res = client.post("/api/bulk/apply", json={"arns": [SUBNET], "tags": {"Owner": "platform"},
                                               "preview_token": pv["preview_token"]})
    assert res.status_code == 201
    cs_id = res.get_json()["id"]
    assert any(c["id"] == cs_id for c in client.get("/api/changesets").get_json()["change_sets"])
    audit = client.get("/api/audit").get_json()["audit_events"]
    assert audit[0]["details"]["change_set_id"] == cs_id
    assert client.post(f"/api/changesets/{cs_id}/undo").status_code == 201
    assert "Owner" not in fake_aws.store[SUBNET]


def test_bulk_apply_app_owner_must_own_everything(client, fake_aws, monkeypatch):
    monkeypatch.setenv("DEV_AUTH_USER", "platform:ApplicationOwner")
    ok = client.post("/api/bulk/apply", json={"arns": [VPC], "tags": {"CostCenter": "123456"}})
    assert ok.status_code == 201
    denied = client.post("/api/bulk/apply", json={"arns": [VPC, BUCKET], "tags": {"CostCenter": "123456"}})
    assert denied.status_code == 403


# ── Suggestions ─────────────────────────────────────────────────────

def test_suggestions_from_same_stack():
    from src.suggestions import SuggestionIndex
    from src.tag_report import schema_provider
    stack = {"aws:cloudformation:stack-name": "orders"}
    resources = [
        {"ResourceARN": f"arn:aws:sqs:us-east-1:{A1}:q{i}", "Tags": dict(stack, Owner="payments")} for i in range(4)
    ] + [{"ResourceARN": f"arn:aws:sqs:us-east-1:{A1}:q9", "Tags": dict(stack, Owner="data")},
         {"ResourceARN": QUEUE.replace(A2, A1), "Tags": dict(stack)}]
    idx = SuggestionIndex(resources, schema_provider.get_schema())
    sugg = idx.suggest(QUEUE.replace(A2, A1), "Owner")
    assert sugg[0]["value"] == "payments"
    assert "CloudFormation stack" in sugg[0]["reason"]
    assert sugg[0]["confidence"] > sugg[1]["confidence"]


def test_suggestions_respect_allowed_values():
    from src.suggestions import SuggestionIndex
    from src.tag_report import schema_provider
    stack = {"aws:cloudformation:stack-name": "s"}
    resources = [{"ResourceARN": f"arn:aws:sqs:us-east-1:{A1}:q{i}", "Tags": dict(stack, Environment="production")}
                 for i in range(3)] + [{"ResourceARN": f"arn:aws:sqs:us-east-1:{A1}:target", "Tags": dict(stack)}]
    assert SuggestionIndex(resources, schema_provider.get_schema()).suggest(
        f"arn:aws:sqs:us-east-1:{A1}:target", "Environment") == []


# ── Propagation ─────────────────────────────────────────────────────

def _relation(children, parent_tags=None, config=None):
    return {"parent_arn": VPC, "parent_name": "net",
            "parent_tags": parent_tags or {"Name": "net", "Owner": "platform", "Environment": "dev",
                                           "aws:cloudformation:stack-name": "x"},
            "children": children, "config": config or []}


def test_findings_exclude_name_and_aws_tags():
    from src.propagation import findings_for
    f = findings_for("vpc", [_relation([{"arn": SUBNET, "type": "ec2:subnet", "tags": {"Name": "net-a", "Environment": "qa"}}])],
                     "us-east-1")
    assert len(f) == 1
    assert f[0]["missing"] == {"Owner": "platform"}
    assert f[0]["mismatched"] == {"Environment": {"expected": "dev", "actual": "qa"}}


def test_fix_plan_overwrite_and_config_ops():
    from src.propagation import findings_for, fix_plan
    op = {"op": "asg_propagate", "target": "arn:aws:autoscaling:us-east-1:1:autoScalingGroup:x", "region": "us-east-1",
          "asg_name": "x", "tags": {"Owner": "platform"}, "before_flags": {"Owner": False}}
    f = findings_for("asg", [_relation([{"arn": SUBNET, "type": "ec2:instance", "tags": {"Environment": "qa"}}],
                                        config=[{"issue": "PropagateAtLaunch is off", "detail": "", "op": op}])], "us-east-1")
    keep = fix_plan(f, overwrite=False)
    assert keep["targets"] == [{"arn": SUBNET, "tags": {"Owner": "platform"}}]
    assert keep["ops"][0]["op"] == "asg_propagate"
    assert fix_plan(f, overwrite=True)["targets"][0]["tags"] == {"Owner": "platform", "Environment": "dev"}


def test_asg_op_apply_and_undo(fake_aws):
    from src import changesets, propagation  # noqa: F401  (registers ops)
    asc = MagicMock()
    op = {"op": "asg_propagate", "target": "arn:aws:autoscaling:us-east-1:1:autoScalingGroup:x", "region": "us-east-1",
          "asg_name": "x", "tags": {"Owner": "platform"}, "before_flags": {"Owner": False}}
    with patch("src.propagation._op_client", return_value=asc):
        cs = changesets.apply([], False, "t", kind="propagation", ops=[op])
        assert cs["status"] == "APPLIED"
        assert asc.create_or_update_tags.call_args.kwargs["Tags"][0]["PropagateAtLaunch"] is True
        changesets.undo(cs["id"], "t")
        assert asc.create_or_update_tags.call_args.kwargs["Tags"][0]["PropagateAtLaunch"] is False


def test_vpc_sync_no_longer_copies_name(fake_aws):
    """Legacy VPC sync used to copy Name to every child, renaming subnets."""
    from src import propagation, tag_sync
    rel = _relation([{"arn": SUBNET, "type": "ec2:subnet", "tags": {"Name": "net-a"}}])
    with patch.dict(propagation.RULES, {"vpc": {"label": "x", "fn": lambda region, session: [rel]}}), \
            patch("src.propagation.session_for", return_value=None):
        out = tag_sync.sync_vpc_tags("us-east-1", "vpc-1")
    assert out["change_set_id"]
    assert fake_aws.store[SUBNET] == {"Name": "net-a", "Owner": "platform", "Environment": "dev"}


def test_propagation_api_flow(client, fake_aws):
    from src import propagation
    rel = _relation([{"arn": SUBNET, "type": "ec2:subnet", "tags": {"Name": "net-a"}}])
    with patch.dict(propagation.RULES, {"vpc": {"label": "x", "fn": lambda region, session: [rel]}}), \
            patch("src.propagation.session_for", return_value=None):
        propagation.save_run(propagation.check(["vpc"], ["us-east-1"], accounts=[None]))
    body = client.get("/api/propagation").get_json()
    fid = body["run"]["findings"][0]["id"]
    pv = client.post("/api/propagation/preview", json={"finding_ids": [fid]}).get_json()
    assert pv["summary"]["keys_added"] == 2
    res = client.post("/api/propagation/apply", json={"finding_ids": [fid], "preview_token": pv["preview_token"]})
    assert res.status_code == 201
    assert fake_aws.store[SUBNET]["Owner"] == "platform"


def test_sync_endpoint_dry_run_needs_no_write_permission(client, monkeypatch):
    monkeypatch.setenv("DEV_AUTH_USER", "v:Viewer")
    with patch("src.tag_sync.propagate", return_value={"rule": "asg", "findings": []}):
        assert client.post("/api/sync", json={"action": "propagate", "rule": "asg", "parent": "x",
                                              "dry_run": True}).status_code == 200
    assert client.post("/api/sync", json={"action": "propagate", "rule": "asg", "parent": "x"}).status_code == 403


def test_organization_endpoint(client):
    from src.cache_manager import save_report
    rep = _scan(datetime.now(timezone.utc), [_res(VPC, "t", True)])
    rep["summary"]["accounts"] = {A1: {"total": 1, "compliant": 1, "compliance_score": 100.0, "errors": {}}}
    save_report(rep)
    body = client.get("/api/organization").get_json()
    assert body["accounts"][0]["account_id"] == A1


def test_ou_leaderboard_uses_current_directory(client):
    """OUs resolve even when the directory was loaded after the scans."""
    from src.db import get_connection, insert_scan, init_db
    init_db()
    insert_scan(_scan(datetime.now(timezone.utc), [_res(VPC, "t", True), _res(QUEUE, "t", False)]))
    conn = get_connection()
    with conn:
        conn.execute("CREATE TABLE IF NOT EXISTS accounts (account_id TEXT PRIMARY KEY, name TEXT, ou_id TEXT, ou_name TEXT, updated_at TEXT NOT NULL)")
        conn.executemany("INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?)",
                         [(A1, "dev", "ou-1", "Platform", "2026-01-01"), (A2, "prod", "ou-2", "Workloads", "2026-01-01")])
    rows = {r["key"]: r["compliance_pct"] for r in client.get("/api/leaderboard?dimension=ou").get_json()["rows"]}
    assert rows == {"Platform": 100.0, "Workloads": 0.0}
