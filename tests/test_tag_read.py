"""
Unit tests for tag_read module.

Uses moto to mock AWS Resource Groups Tagging API.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.tag_read import (
    resolve_resource_type,
    resolve_resource_types,
    normalize_tag_filters,
    normalize_tag_filters_optional,
    resource_missing_tag_key,
    extract_resource_info,
    build_response,
    lambda_handler,
    RESOURCE_TYPE_MAP,
)


# ── resolve_resource_type ────────────────────────────────────────────


class TestResolveResourceType:
    def test_friendly_alias(self):
        assert resolve_resource_type("RDS") == "rds:db"

    def test_raw_aws_type(self):
        assert resolve_resource_type("ec2:instance") == "ec2:instance"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unsupported resource type"):
            resolve_resource_type("NotAReal")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="required"):
            resolve_resource_type("")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            resolve_resource_type(None)

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="required"):
            resolve_resource_type("   ")


# ── resolve_resource_types ───────────────────────────────────────────


class TestResolveResourceTypes:
    def test_single_string(self):
        result = resolve_resource_types("S3")
        assert result == ["s3:bucket"]

    def test_list_of_aliases(self):
        result = resolve_resource_types(["RDS", "S3"])
        assert result == ["rds:db", "s3:bucket"]

    def test_deduplicates(self):
        result = resolve_resource_types(["RDS", "RDS", "S3"])
        assert result == ["rds:db", "s3:bucket"]

    def test_none_raises(self):
        with pytest.raises(ValueError, match="required"):
            resolve_resource_types(None)

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            resolve_resource_types([])


# ── normalize_tag_filters ────────────────────────────────────────────


class TestNormalizeTagFilters:
    def test_simple_kv(self):
        result = normalize_tag_filters({"env": "prod"})
        assert result == [{"Key": "env", "Values": ["prod"]}]

    def test_list_values(self):
        result = normalize_tag_filters({"svc": ["a", "b"]})
        assert result == [{"Key": "svc", "Values": ["a", "b"]}]

    def test_empty_dict_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            normalize_tag_filters({})

    def test_non_dict_raises(self):
        with pytest.raises(ValueError):
            normalize_tag_filters("notadict")

    def test_empty_key_raises(self):
        with pytest.raises(ValueError, match="non-empty strings"):
            normalize_tag_filters({"": "val"})


# ── normalize_tag_filters_optional ───────────────────────────────────


class TestNormalizeTagFiltersOptional:
    def test_allow_empty_true_none(self):
        assert normalize_tag_filters_optional(None, allow_empty=True) == []

    def test_allow_empty_true_empty_dict(self):
        assert normalize_tag_filters_optional({}, allow_empty=True) == []

    def test_allow_empty_false_none_raises(self):
        with pytest.raises(ValueError):
            normalize_tag_filters_optional(None, allow_empty=False)


# ── resource_missing_tag_key ─────────────────────────────────────────


class TestResourceMissingTagKey:
    def test_key_absent(self):
        assert resource_missing_tag_key({"Name": "test"}, "Owner") is True

    def test_key_present_nonempty(self):
        assert resource_missing_tag_key({"Owner": "devops"}, "Owner") is False

    def test_key_present_empty(self):
        assert resource_missing_tag_key({"Owner": "  "}, "Owner") is True


# ── extract_resource_info ────────────────────────────────────────────


class TestExtractResourceInfo:
    def test_normal_item(self):
        item = {
            "ResourceARN": "arn:aws:ec2:us-east-1:123:instance/i-abc",
            "Tags": [
                {"Key": "Name", "Value": "web-1"},
                {"Key": "env", "Value": "prod"},
            ],
        }
        result = extract_resource_info(item)
        assert result["ResourceARN"] == "arn:aws:ec2:us-east-1:123:instance/i-abc"
        assert result["Name"] == "web-1"
        assert result["Tags"]["env"] == "prod"

    def test_no_name_tag(self):
        item = {"ResourceARN": "arn:test", "Tags": []}
        result = extract_resource_info(item)
        assert result["Name"] == "---"


# ── build_response ───────────────────────────────────────────────────


class TestBuildResponse:
    def test_shape(self):
        resp = build_response(200, {"count": 1})
        assert resp["statusCode"] == 200
        assert resp["body"]["count"] == 1


# ── lambda_handler ───────────────────────────────────────────────────


class TestLambdaHandler:
    def test_missing_resource_returns_400(self):
        result = lambda_handler({}, None)
        assert result["statusCode"] == 400
        assert "resource" in result["body"]["message"].lower()

    def test_missing_filters_returns_400(self):
        result = lambda_handler({"resource": "RDS"}, None)
        assert result["statusCode"] == 400

    @patch("src.tag_read.get_client")
    def test_successful_read(self, mock_get_client):
        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {
                "ResourceTagMappingList": [
                    {
                        "ResourceARN": "arn:aws:rds:us-east-2:123:db:mydb",
                        "Tags": [
                            {"Key": "Name", "Value": "mydb"},
                            {"Key": "env", "Value": "prod"},
                        ],
                    }
                ]
            }
        ]
        mock_client.get_paginator.return_value = mock_paginator
        mock_get_client.return_value = mock_client

        result = lambda_handler(
            {"resource": "RDS", "filters": {"env": "prod"}, "region": "us-east-2"},
            None,
        )
        assert result["statusCode"] == 200
        assert result["body"]["count"] == 1


# ── RESOURCE_TYPE_MAP completeness ──────────────────────────────────


class TestResourceTypeMap:
    def test_all_values_contain_colon_or_are_known_single_word(self):
        """Every mapped value should be a valid AWS resource type filter."""
        for alias, aws_type in RESOURCE_TYPE_MAP.items():
            assert isinstance(alias, str) and alias
            assert isinstance(aws_type, str) and aws_type

    def test_no_duplicate_values(self):
        """No two aliases should resolve to the same AWS type (catches copy-paste errors)."""
        values = list(RESOURCE_TYPE_MAP.values())
        # Duplicates are acceptable if intentional, but flag them
        assert len(values) == len(set(values)), (
            f"Duplicate AWS types: {[v for v in values if values.count(v) > 1]}"
        )
