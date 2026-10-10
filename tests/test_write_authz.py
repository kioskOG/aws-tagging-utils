"""
Resource-level authorization on every tag-writing endpoint:
- ApplicationOwners may only write to resources they own *now* (live tags, not the request);
- protected tags need SecurityAdmin or PlatformAdmin;
- the tag-on-create endpoint needs write permission and only runs scans.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.test_platform import SUBNET, VPC, FakeTagging

OK = {"statusCode": 200, "body": {"tagged_count": 1}}


@pytest.fixture
def store():
    data = {VPC: {"Owner": "platform"}, SUBNET: {"Owner": "jane@example.com"}}
    fake = FakeTagging(data)
    with patch("src.changesets._tagging", return_value=fake):
        yield data


@pytest.fixture
def client():
    from web.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def as_user(monkeypatch, spec, email=None):
    monkeypatch.setenv("AUTH_MODE", "local_dev")
    monkeypatch.setenv("DEV_AUTH_USER", spec)
    if email:
        monkeypatch.setenv("DEV_AUTH_EMAIL", email)
    else:
        monkeypatch.delenv("DEV_AUTH_EMAIL", raising=False)


def test_app_owner_cannot_claim_someone_elses_resource(client, store, monkeypatch):
    """Putting your own name in the requested Owner tag must not grant write access."""
    as_user(monkeypatch, "jane:ApplicationOwner", "jane@example.com")
    with patch("web.app.write_handler", return_value=OK) as write:
        resp = client.post("/api/write", json={"arns": [VPC], "tags": {"Owner": "jane@example.com"}})
    assert resp.status_code == 403
    write.assert_not_called()


def test_app_owner_can_write_own_resource(client, store, monkeypatch):
    as_user(monkeypatch, "jane:ApplicationOwner", "jane@example.com")
    with patch("web.app.write_handler", return_value=OK) as write:
        resp = client.post("/api/write", json={"arns": [SUBNET], "tags": {"Environment": "dev"}})
    assert resp.status_code == 200
    write.assert_called_once()


def test_app_owner_remediation_checks_live_tags(client, store, monkeypatch):
    as_user(monkeypatch, "jane:ApplicationOwner", "jane@example.com")
    monkeypatch.setattr("web.app.GOVERNANCE_REMEDIATION_ENABLED", True)
    with patch("src.enforcement.remediation_engine.process_sync") as run:
        resp = client.post("/api/remediation", json={"resource_arn": VPC, "requested_tags": {"Owner": "jane@example.com"}})
    assert resp.status_code == 403
    run.assert_not_called()


def test_viewer_cannot_run_tag_on_create(client, monkeypatch):
    as_user(monkeypatch, "v:Viewer")
    with patch("web.app.gov_handler") as gov:
        resp = client.post("/api/gov", json={"action": "scan", "regions": ["us-east-1"]})
    assert resp.status_code == 403
    gov.assert_not_called()


def test_tag_on_create_rejects_raw_events(client, monkeypatch):
    """Raw CloudTrail events come from EventBridge; through the API the creator could be forged."""
    as_user(monkeypatch, "op:TagOperator")
    event = {"detail": {"eventName": "CreateBucket", "userIdentity": {"arn": "arn:aws:iam::1:user/forged"}}}
    with patch("web.app.gov_handler") as gov:
        resp = client.post("/api/gov", json=event)
    assert resp.status_code == 400
    gov.assert_not_called()


@pytest.fixture
def protected_owner(monkeypatch):
    monkeypatch.setattr("src.protected_tags.protected_keys", lambda: {"Owner"})


@pytest.mark.parametrize("spec,status", [("op:TagOperator", 403), ("sec:SecurityAdmin,TagOperator", 200),
                                         ("admin:PlatformAdmin", 200)])
def test_protected_tags_need_security_or_platform_admin(client, store, protected_owner, monkeypatch, spec, status):
    as_user(monkeypatch, spec)
    with patch("web.app.write_handler", return_value=OK):
        resp = client.post("/api/write", json={"arns": [VPC], "tags": {"owner": "x"}})
    assert resp.status_code == status


def test_protected_tags_blocked_in_bulk_apply(client, store, protected_owner, monkeypatch):
    as_user(monkeypatch, "op:TagOperator")
    resp = client.post("/api/bulk/apply", json={"arns": [VPC], "tags": {"Owner": "x"}})
    assert resp.status_code == 403
    assert store[VPC]["Owner"] == "platform"


def test_unprotected_write_by_operator_still_allowed(client, store, protected_owner, monkeypatch):
    as_user(monkeypatch, "op:TagOperator")
    with patch("web.app.write_handler", return_value=OK):
        resp = client.post("/api/write", json={"arns": [VPC], "tags": {"Environment": "dev"}})
    assert resp.status_code == 200
