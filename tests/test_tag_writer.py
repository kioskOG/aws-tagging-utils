"""
Unit tests for tag_writer module.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.tag_writer import (
    build_response,
    get_region_from_arn,
    lambda_handler,
)


class TestGetRegionFromArn:
    def test_standard_arn(self):
        assert get_region_from_arn("arn:aws:ec2:us-east-1:123:instance/i-abc") == "us-east-1"

    def test_s3_global_arn(self):
        # S3 ARNs don't have a region component
        assert get_region_from_arn("arn:aws:s3:::my-bucket") is None

    def test_empty_string(self):
        assert get_region_from_arn("") is None

    def test_none(self):
        assert get_region_from_arn(None) is None


class TestBuildResponse:
    def test_shape(self):
        resp = build_response(200, {"message": "ok"})
        assert resp["statusCode"] == 200
        assert resp["body"]["message"] == "ok"


class TestLambdaHandler:
    def test_missing_arn_returns_400(self):
        result = lambda_handler({"tags": {"env": "prod"}}, None)
        assert result["statusCode"] == 400

    def test_missing_tags_returns_400(self):
        result = lambda_handler(
            {"arn": "arn:aws:ec2:us-east-1:123:instance/i-abc"}, None
        )
        assert result["statusCode"] == 400

    def test_empty_tags_returns_400(self):
        result = lambda_handler(
            {"arn": "arn:aws:ec2:us-east-1:123:instance/i-abc", "tags": {}}, None
        )
        assert result["statusCode"] == 400

    @patch("src.tag_writer.get_client")
    def test_successful_tag(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.tag_resources.return_value = {"FailedResourcesMap": {}}
        mock_get_client.return_value = mock_client

        result = lambda_handler(
            {
                "arns": ["arn:aws:ec2:us-east-1:123:instance/i-abc"],
                "tags": {"env": "prod"},
            },
            None,
        )
        assert result["statusCode"] == 200
        assert result["body"]["count"] == 1

    @patch("src.tag_writer.get_client")
    def test_partial_failure_returns_207(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.tag_resources.return_value = {
            "FailedResourcesMap": {
                "arn:aws:ec2:us-east-1:123:instance/i-abc": {
                    "ErrorCode": "InternalServiceException"
                }
            }
        }
        mock_get_client.return_value = mock_client

        result = lambda_handler(
            {
                "arns": ["arn:aws:ec2:us-east-1:123:instance/i-abc"],
                "tags": {"env": "prod"},
            },
            None,
        )
        assert result["statusCode"] == 207

    def test_single_arn_field(self):
        """The 'arn' (singular) field should also work."""
        with patch("src.tag_writer.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.tag_resources.return_value = {"FailedResourcesMap": {}}
            mock_get_client.return_value = mock_client

            result = lambda_handler(
                {
                    "arn": "arn:aws:ec2:us-east-1:123:instance/i-abc",
                    "tags": {"env": "prod"},
                },
                None,
            )
            assert result["statusCode"] == 200
