"""
Unit tests for tag_on_create module — identity extraction logic.

These tests validate the core owner_from_user_identity function
which is the most complex pure-logic function in the module.
"""

import pytest

from src.tag_on_create import owner_from_user_identity


class TestOwnerFromUserIdentity:
    """Validate creator extraction for all IAM identity types."""

    def test_iam_user_with_username(self):
        uid = {"type": "IAMUser", "userName": "alice"}
        assert owner_from_user_identity(uid) == "alice"

    def test_iam_user_with_principal_id(self):
        uid = {"type": "IAMUser", "principalId": "AIDA:alice"}
        assert owner_from_user_identity(uid) == "alice"

    def test_iam_user_with_only_principal_id_no_colon(self):
        uid = {"type": "IAMUser", "principalId": "AIDAXYZ"}
        assert owner_from_user_identity(uid) == "AIDAXYZ"

    def test_assumed_role_with_username(self):
        uid = {"type": "AssumedRole", "userName": "deploy-bot"}
        assert owner_from_user_identity(uid) == "deploy-bot"

    def test_assumed_role_with_arn(self):
        uid = {
            "type": "AssumedRole",
            "arn": "arn:aws:sts::123:assumed-role/MyRole/session-name",
        }
        assert owner_from_user_identity(uid) == "MyRole/session-name"

    def test_root_user(self):
        uid = {"type": "Root"}
        assert owner_from_user_identity(uid) == "root"

    def test_aws_service_returns_none(self):
        uid = {"type": "AWSService", "invokedBy": "s3.amazonaws.com"}
        assert owner_from_user_identity(uid) is None

    def test_none_returns_none(self):
        assert owner_from_user_identity(None) is None

    def test_not_a_dict_returns_none(self):
        assert owner_from_user_identity("string") is None

    def test_unknown_type_with_principal_id(self):
        uid = {"type": "SomeNewType", "principalId": "XYZ123"}
        assert owner_from_user_identity(uid) == "XYZ123"

    def test_web_identity_user(self):
        uid = {"type": "WebIdentityUser", "userName": "oidc-user@example.com"}
        assert owner_from_user_identity(uid) == "oidc-user@example.com"

    def test_truncation_at_256_chars(self):
        """Owner values should never exceed 256 characters (AWS tag value limit)."""
        uid = {"type": "IAMUser", "userName": "a" * 500}
        result = owner_from_user_identity(uid)
        assert len(result) == 256
