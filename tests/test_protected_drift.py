"""Protected-tag baselines and drift detection / auto-revert (src.protected_tags)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src import protected_tags
from src.governance.state import MemoryStateStore

ARN = "arn:aws:s3:::payments-data"
EC2 = "arn:aws:ec2:us-east-1:111111111111:instance/i-1"


@pytest.fixture(autouse=True)
def store(monkeypatch):
    s = MemoryStateStore()
    protected_tags.set_store(s)
    monkeypatch.setattr(protected_tags, "protected_keys", lambda: {"DataClassification", "Owner"})
    monkeypatch.setenv("DRIFT_ENABLED", "true")
    monkeypatch.setenv("DRIFT_AUTO_REVERT", "false")
    yield s
    protected_tags.set_store(None)


def event(arn=ARN, changed=("DataClassification",), tags=None, account="111111111111"):
    return {"source": "aws.tag", "detail-type": "Tag Change on Resource", "account": account,
            "region": "us-east-1", "resources": [arn],
            "detail": {"changed-tag-keys": list(changed), "service": "s3", "resource-type": "bucket",
                       "tags": tags if tags is not None else {"DataClassification": "public"}}}


# ── Baselines ───────────────────────────────────────────────────────

def test_record_write_tracks_only_protected_keys():
    protected_tags.record_write(ARN, {"DataClassification": "x", "dataclassification": "y", "Environment": "dev"})
    assert protected_tags.get_baseline(ARN) == {"DataClassification": "x"}
    protected_tags.record_write(ARN, removed=["DataClassification"])
    assert protected_tags.get_baseline(ARN) == {}


def test_record_write_ignores_unprotected_and_store_errors(store):
    protected_tags.record_write(ARN, {"Environment": "dev"})
    assert store.get_resource_state(ARN) is None
    broken = MagicMock()
    broken.get_resource_state.side_effect = RuntimeError("dynamodb down")
    protected_tags.set_store(broken)
    protected_tags.record_write(ARN, {"Owner": "a"})  # must not raise


def test_seed_only_fills_missing_keys():
    protected_tags.record_write(ARN, {"Owner": "authorized"})
    n = protected_tags.seed_baselines([(ARN, {"Owner": "drifted", "DataClassification": "internal"}),
                                       (EC2, {"Environment": "dev"}), (None, {"Owner": "x"})])
    assert n == 1
    assert protected_tags.get_baseline(ARN) == {"Owner": "authorized", "DataClassification": "internal"}


def test_seed_disabled_when_drift_off(monkeypatch):
    monkeypatch.setenv("DRIFT_ENABLED", "false")
    assert protected_tags.seed_baselines([(ARN, {"Owner": "x"})]) == 0


def test_change_set_writes_record_baseline():
    from src import changesets
    from tests.test_platform import FakeTagging
    fake = FakeTagging({EC2: {"Owner": "old"}})
    with patch("src.changesets._tagging", return_value=fake):
        cs = changesets.apply([{"arn": EC2, "tags": {"Owner": "new", "Environment": "dev"}}], True, "admin")
        assert protected_tags.get_baseline(EC2) == {"Owner": "new"}
        changesets.undo(cs["id"], "admin")
    assert protected_tags.get_baseline(EC2) == {"Owner": "old"}


def test_compliance_scan_seeds_baselines():
    from src.cache_manager import save_report
    save_report({"regions": {"us-east-1": {"resources": [{"ResourceARN": ARN, "Tags": {"Owner": "data-team"}}]}}})
    assert protected_tags.get_baseline(ARN) == {"Owner": "data-team"}


# ── Drift events ────────────────────────────────────────────────────

def test_unprotected_change_is_ignored():
    r = protected_tags.handle_tag_change_event(event(changed=["Environment"], tags={"Environment": "x"}))
    assert r["status"] == "NO_PROTECTED_CHANGE"


def test_first_seen_value_is_learned_not_flagged():
    r = protected_tags.handle_tag_change_event(event(tags={"DataClassification": "confidential"}))
    assert r["status"] == "NO_DRIFT" and r["learned"] == ["DataClassification"]
    assert protected_tags.get_baseline(ARN) == {"DataClassification": "confidential"}


def test_detect_mode_reports_without_writing():
    protected_tags.record_write(ARN, {"DataClassification": "confidential"})
    notifier = MagicMock()
    with patch.object(protected_tags, "_revert") as revert, \
            patch("src.governance.audit.AuditLogger.log") as audit:
        r = protected_tags.handle_tag_change_event(event(), notifier=notifier)
    assert r["status"] == "DETECTED"
    assert r["drift"] == {"DataClassification": {"expected": "confidential", "actual": "public"}}
    revert.assert_not_called()
    assert audit.call_args.args[:2] == ("PROTECTED_TAG_DRIFT", "DETECT")
    assert notifier.notify.call_args.args[0] == "HIGH"
    assert protected_tags.get_baseline(ARN) == {"DataClassification": "confidential"}


def test_revert_mode_restores_changed_and_deleted_keys(monkeypatch):
    monkeypatch.setenv("DRIFT_AUTO_REVERT", "true")
    protected_tags.record_write(EC2, {"DataClassification": "confidential", "Owner": "payments"})
    client = MagicMock()
    client.tag_resources.return_value = {"FailedResourcesMap": {}}
    with patch("src.clients.get_tagging_client", return_value=client) as get_client, \
            patch("src.aws_session.session_for", return_value="member-session"):
        r = protected_tags.handle_tag_change_event(
            event(EC2, changed=["DataClassification", "Owner"], tags={"DataClassification": "public"}))
    assert r["status"] == "REVERTED"
    get_client.assert_called_once_with("us-east-1", "member-session")
    client.tag_resources.assert_called_once_with(
        ResourceARNList=[EC2], Tags={"DataClassification": "confidential", "Owner": "payments"})


def test_revert_echo_event_is_a_no_op(monkeypatch):
    monkeypatch.setenv("DRIFT_AUTO_REVERT", "true")
    protected_tags.record_write(ARN, {"DataClassification": "confidential"})
    with patch.object(protected_tags, "_revert") as revert:
        r = protected_tags.handle_tag_change_event(event(tags={"DataClassification": "confidential"}))
    assert r["status"] == "NO_DRIFT"
    revert.assert_not_called()


def test_revert_failure_is_reported(monkeypatch):
    monkeypatch.setenv("DRIFT_AUTO_REVERT", "true")
    protected_tags.record_write(ARN, {"DataClassification": "confidential"})
    notifier = MagicMock()
    with patch.object(protected_tags, "_revert", side_effect=RuntimeError("AccessDenied")):
        r = protected_tags.handle_tag_change_event(event(), notifier=notifier)
    assert r["status"] == "REVERT_FAILED" and r["error"] == "AccessDenied"
    assert "could NOT be reverted" in notifier.notify.call_args.args[1]


def test_exempt_resource_accepts_change(monkeypatch):
    monkeypatch.setenv("DRIFT_AUTO_REVERT", "true")
    protected_tags.record_write(ARN, {"DataClassification": "confidential"})
    exemptions = MagicMock()
    exemptions.is_exempt.return_value = {"id": "ex-1"}
    with patch.object(protected_tags, "_revert") as revert:
        r = protected_tags.handle_tag_change_event(event(), exemption_manager=exemptions)
    assert r["status"] == "EXEMPT"
    revert.assert_not_called()
    assert protected_tags.get_baseline(ARN) == {"DataClassification": "public"}


def test_drift_off_ignores_events(monkeypatch):
    monkeypatch.setenv("DRIFT_ENABLED", "false")
    assert protected_tags.handle_tag_change_event(event())["status"] == "DISABLED"


def test_auto_revert_needs_drift_enabled(monkeypatch):
    monkeypatch.setenv("DRIFT_ENABLED", "false")
    monkeypatch.setenv("DRIFT_AUTO_REVERT", "true")
    assert protected_tags.drift_mode() == "off"


def test_event_without_resource_is_rejected():
    e = event()
    e["resources"] = []
    assert protected_tags.handle_tag_change_event(e)["statusCode"] == 400


def test_enforcement_lambda_routes_tag_change_events():
    from src import enforcement
    protected_tags.record_write(ARN, {"DataClassification": "confidential"})
    with patch.object(enforcement, "notification_provider") as notifier:
        r = enforcement.lambda_handler(event(), None)
    assert r["status"] == "DETECTED"
    notifier.notify.assert_called_once()
