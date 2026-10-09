"""
Regression tests for the stability / observability release:
partial tag writes, mandatory tags, refresh failure handling, metrics,
readiness, identity, history/export, exemption expiry, retention, MCP read-only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict
from unittest.mock import MagicMock, patch

import pytest

from src.governance.engine import TagGovernanceEngine
from src.governance.models import TagSchema
from src.governance.schema_provider import SchemaProvider


class _Schema(SchemaProvider):
    def get_schema(self) -> Dict[str, TagSchema]:
        return {
            "Owner": TagSchema(key="Owner", required=True),
            "Environment": TagSchema(key="Environment", required=True, allowed_values=["dev", "prod"], aliases=["env"]),
            "CostCenter": TagSchema(key="CostCenter", pattern="^[0-9]{6}$"),
        }


def _report(resources, regions=("us-east-1",), error=None):
    region = regions[0]
    total = len(resources)
    compliant = sum(1 for r in resources if r["IsCompliant"])
    region_data = {"error": error} if error else {"total": total, "compliant": compliant,
                                                    "non_compliant": total - compliant, "resources": resources}
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {"total_resources": total, "compliant": compliant, "non_compliant": total - compliant,
                    "compliance_score": round(compliant * 100 / total, 2) if total else 0.0},
        "regions": {region: region_data},
    }


VPC = "arn:aws:ec2:us-east-1:111122223333:vpc/vpc-1"
BUCKET = "arn:aws:s3:::bucket-1"


@pytest.fixture
def client():
    from web.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── Governance engine ───────────────────────────────────────────────

def test_partial_evaluation_skips_required_checks():
    engine = TagGovernanceEngine(_Schema())
    result = engine.evaluate({"Environment": "dev"}, partial=True)
    assert result.compliant is True


def test_partial_evaluation_still_validates_values():
    engine = TagGovernanceEngine(_Schema())
    result = engine.evaluate({"Environment": "qa"}, partial=True)
    assert result.compliant is False
    assert result.violations[0].type == "INVALID_VALUE"


def test_extra_required_adds_missing_tag_once():
    engine = TagGovernanceEngine(_Schema())
    result = engine.evaluate({"Owner": "a", "Environment": "dev"}, extra_required=["CostCenter", "Owner"])
    assert [(v.tag, v.type) for v in result.violations] == [("CostCenter", "MISSING_REQUIRED")]


def test_extra_required_respects_aliases():
    engine = TagGovernanceEngine(_Schema())
    result = engine.evaluate({"Owner": "a", "env": "dev"}, extra_required=["Environment"])
    assert result.compliant is True


# ── Tag writer ──────────────────────────────────────────────────────

def test_single_tag_write_is_not_rejected_for_other_required_tags():
    """Fix Tags sends only the missing tag; strict mode used to reject it."""
    from src import tag_writer
    client = MagicMock()
    client.tag_resources.return_value = {"FailedResourcesMap": {}}
    with patch.object(tag_writer, "get_client", return_value=client):
        result = tag_writer.lambda_handler({"arn": VPC, "tags": {"Environment": "dev"}}, None)
    assert result["statusCode"] == 200
    client.tag_resources.assert_called_once_with(ResourceARNList=[VPC], Tags={"Environment": "dev"})


def test_writer_accepts_resource_arn_alias_and_dedupes():
    from src.tag_writer import collect_arns
    assert collect_arns({"resource_arn": VPC, "arn": VPC, "arns": [VPC, BUCKET]}) == [VPC, BUCKET]


def test_writer_rejects_reserved_aws_prefix():
    from src.tag_writer import lambda_handler
    result = lambda_handler({"arn": VPC, "tags": {"aws:cloudformation:stack-name": "x"}}, None)
    assert result["statusCode"] == 400


def test_writer_does_not_mutate_input():
    from src import tag_writer
    client = MagicMock()
    client.tag_resources.return_value = {"FailedResourcesMap": {}}
    event = {"arn": VPC, "arns": [BUCKET], "tags": {"Owner": "x"}}
    with patch.object(tag_writer, "get_client", return_value=client):
        tag_writer.lambda_handler(event, None)
    assert event["arns"] == [BUCKET]


def test_write_validation_error_returns_details(client):
    res = client.post("/api/write", json={"arn": VPC, "tags": {"Environment": "production"}})
    assert res.status_code == 400
    body = res.get_json()
    assert body["error_code"] == "VALIDATION_ERROR"
    assert body["details"][0]["tag"] == "Environment"


# ── Compliance refresh ──────────────────────────────────────────────

def test_refresh_keeps_last_good_scan_when_every_region_fails():
    import src.cache_manager as cm
    from src.cache_manager import background_refresh, get_cached_report, save_report
    save_report(_report([{"ResourceARN": VPC, "IsCompliant": True, "Violations": [], "Tags": {}}]))
    failed = _report([], error="The security token included in the request is invalid.")
    with patch("src.tag_report.generate_report", return_value=failed):
        background_refresh(["us-east-1"])
    assert cm.get_refresh_error().startswith("All regions failed")
    assert get_cached_report()["summary"]["total_resources"] == 1


def test_report_honors_mandatory_tags():
    from src import tag_report
    page = {"ResourceTagMappingList": [{"ResourceARN": VPC, "Tags": [
        {"Key": "Owner", "Value": "a"}, {"Key": "Environment", "Value": "dev"}]}]}
    paginator = MagicMock()
    paginator.paginate.return_value = [page]
    client = MagicMock()
    client.get_paginator.return_value = paginator
    with patch.object(tag_report, "get_client", return_value=client):
        report = tag_report.generate_report(["us-east-1"], ["Owner", "Application"], ["ec2:vpc"])
    res = report["regions"]["us-east-1"]["resources"][0]
    assert res["IsCompliant"] is False
    assert {"tag": "Application", "type": "MISSING_REQUIRED"}.items() <= res["Violations"][0].items()


def test_scan_retention_prunes_old_scans(monkeypatch):
    import src.config
    from src.db import get_scan_history, insert_scan, init_db
    init_db()
    monkeypatch.setattr(src.config, "SCAN_RETENTION", 3)
    for _ in range(5):
        insert_scan(_report([{"ResourceARN": VPC, "IsCompliant": True, "Violations": [], "Tags": {}}]))
    assert len(get_scan_history(100)) == 3


def test_warnings_survive_cache_roundtrip():
    from src.cache_manager import get_cached_report, save_report
    warning = {"tag": "Name", "type": "UNKNOWN_TAG"}
    save_report(_report([{"ResourceARN": VPC, "IsCompliant": True, "Violations": [], "Warnings": [warning],
                          "Tags": {"Name": "x"}}]))
    res = get_cached_report()["regions"]["us-east-1"]["resources"][0]
    assert res["Warnings"] == [warning]


# ── API: health, metrics, identity, history, export, dashboard ──────

def test_ready_endpoint(client):
    res = client.get("/ready")
    assert res.status_code == 200
    assert res.get_json()["checks"]["database"] == "ok"


def test_metrics_endpoint_exposes_prometheus_text(client):
    client.get("/api/schema")
    res = client.get("/metrics")
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    assert "tagging_utils_http_requests_total" in text
    assert 'endpoint="/api/schema"' in text


def test_metrics_token(client, monkeypatch):
    import web.app as webapp
    monkeypatch.setattr(webapp, "METRICS_AUTH_TOKEN", "s3cret")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_me_reports_permissions(client, monkeypatch):
    monkeypatch.setenv("DEV_AUTH_USER", "bob:Viewer")
    body = client.get("/api/me").get_json()
    assert body["user_id"] == "bob"
    assert body["permissions"] == {"modify_tags": False, "view_finops": False,
                                   "manage_exemptions": False, "manage_protected_tags": False}


def test_history_and_csv_export(client):
    from src.cache_manager import save_report
    save_report(_report([
        {"ResourceARN": VPC, "IsCompliant": True, "Violations": [], "Tags": {"Owner": "a"}},
        {"ResourceARN": BUCKET, "IsCompliant": False,
         "Violations": [{"tag": "Owner", "type": "MISSING_REQUIRED"}], "Tags": {}},
    ]))
    scans = client.get("/api/compliance/history").get_json()["scans"]
    assert scans[-1]["compliance_score"] == 50.0
    csv_text = client.get("/api/compliance/export.csv").get_data(as_text=True)
    assert csv_text.splitlines()[0].startswith("arn,account,region")
    assert "bucket-1" in csv_text and "Owner" in csv_text


def test_dashboard_breakdowns(client):
    from src.cache_manager import save_report
    save_report(_report([
        {"ResourceARN": VPC, "IsCompliant": True, "Violations": [], "Tags": {}},
        {"ResourceARN": BUCKET, "IsCompliant": False,
         "Violations": [{"tag": "Owner", "type": "MISSING_REQUIRED"}], "Tags": {}},
    ]))
    body = client.get("/api/dashboard").get_json()
    assert body["top_violations"] == [{"tag": "Owner", "type": "MISSING_REQUIRED", "count": 1}]
    assert body["by_service"][0] == {"name": "s3", "total": 1, "compliant": 0, "compliance_pct": 0.0}


def test_compliance_exemption_annotation(client, monkeypatch):
    import web.app as webapp
    from src.cache_manager import save_report
    from src.governance.state import MemoryStateStore
    monkeypatch.setattr(webapp.exemption_manager, "state_store", MemoryStateStore())
    webapp._exemptions_cache.invalidate()
    save_report(_report([{"ResourceARN": BUCKET, "IsCompliant": False,
                          "Violations": [{"tag": "Owner", "type": "MISSING_REQUIRED"}], "Tags": {}}]))
    webapp.exemption_manager.create_exemption({"resource_id": BUCKET, "reason": "legacy"}, "admin")
    rows = client.get("/api/compliance?include_exemptions=1").get_json()["resources"]
    assert rows[0]["exemption_state"] == "EXEMPT"
    webapp._exemptions_cache.invalidate()


def test_remediation_list_endpoint(client, monkeypatch):
    from src.enforcement import remediation_engine
    from src.governance.state import MemoryStateStore
    store = MemoryStateStore()
    store.create_remediation_action({"action_id": "a1", "status": "PENDING", "created_at": "2026-01-01"})
    monkeypatch.setattr(remediation_engine, "state_store", store)
    body = client.get("/api/remediation").get_json()
    assert body["pending"] == 1 and body["actions"][0]["action_id"] == "a1"


def test_remediation_disabled(client, monkeypatch):
    import web.app as webapp
    monkeypatch.setattr(webapp, "GOVERNANCE_REMEDIATION_ENABLED", False)
    res = client.post("/api/remediation", json={"resource_arn": VPC, "requested_tags": {"Owner": "a"}})
    assert res.status_code == 403


def test_security_headers(client):
    res = client.get("/health")
    assert res.headers["X-Content-Type-Options"] == "nosniff"


# ── Exemptions ──────────────────────────────────────────────────────

def test_naive_expiry_date_is_honored():
    """'2020-01-01' (no timezone) used to crash the comparison and stay active forever."""
    from src.governance.exemptions import ExemptionManager
    from src.governance.state import MemoryStateStore
    mgr = ExemptionManager(MemoryStateStore())
    mgr.create_exemption({"resource_id": "i-1", "expires_at": "2020-01-01"}, "u")
    assert mgr.list_active_exemptions() == []


def test_future_naive_expiry_is_active():
    from src.governance.exemptions import ExemptionManager
    from src.governance.state import MemoryStateStore
    mgr = ExemptionManager(MemoryStateStore())
    future = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
    ex = mgr.create_exemption({"resource_id": "i-1", "expires_at": future}, "u")
    assert ex["expires_at"].endswith("+00:00")
    assert len(mgr.list_active_exemptions()) == 1


def test_invalid_expiry_rejected():
    from src.errors import APIError
    from src.governance.exemptions import ExemptionManager
    from src.governance.state import MemoryStateStore
    with pytest.raises(APIError):
        ExemptionManager(MemoryStateStore()).create_exemption({"resource_id": "i-1", "expires_at": "soon"}, "u")


# ── Sync & MCP ──────────────────────────────────────────────────────

def test_sync_missing_vpc_is_404():
    from src import tag_sync
    with patch.object(tag_sync, "sync_vpc_tags", return_value={"error": "VPC vpc-x not found"}):
        result = tag_sync.lambda_handler({"action": "sync_vpc", "vpc_id": "vpc-x"}, None)
    assert result["statusCode"] == 404


def test_mcp_read_only_blocks_writes(monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "MCP_READ_ONLY", True)
    write = getattr(mcp_server.write_tags, "fn", mcp_server.write_tags)
    result = write(arns=[VPC], tags={"Owner": "a"})
    assert result["statusCode"] == 403


# ── Metrics helpers ─────────────────────────────────────────────────

def test_record_compliance_report_sets_gauges():
    from src.observability import metrics
    metrics.record_compliance_report(_report([
        {"ResourceARN": BUCKET, "IsCompliant": False, "Violations": [{"tag": "Owner", "type": "MISSING_REQUIRED"}]},
    ]))
    assert metrics.REGISTRY.get_sample_value("tagging_utils_compliance_score_percent") == 0.0
    assert metrics.REGISTRY.get_sample_value(
        "tagging_utils_compliance_violations", {"tag": "Owner", "type": "MISSING_REQUIRED"}) == 1.0


def test_aws_client_calls_are_counted():
    from botocore.stub import Stubber
    from src.clients import get_sts_client
    from src.observability import metrics
    client = get_sts_client()
    before = metrics.REGISTRY.get_sample_value(
        "tagging_utils_aws_api_calls_total", {"service": "sts", "operation": "GetCallerIdentity", "outcome": "success"}) or 0
    with Stubber(client) as stub:
        stub.add_response("get_caller_identity", {"Account": "1", "Arn": "arn:aws:iam::1:user/x", "UserId": "x"})
        client.get_caller_identity()
    after = metrics.REGISTRY.get_sample_value(
        "tagging_utils_aws_api_calls_total", {"service": "sts", "operation": "GetCallerIdentity", "outcome": "success"})
    assert after == before + 1
