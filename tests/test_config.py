"""
Unit tests for the centralized config module.
"""

import os
from unittest.mock import patch

import pytest


class TestConfig:
    """Validate that config reads environment variables correctly."""

    def test_default_region_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            # Re-import to pick up cleared env
            import importlib
            import src.config
            importlib.reload(src.config)
            assert src.config.DEFAULT_REGION == "us-east-2"

    def test_default_region_from_env(self):
        with patch.dict(os.environ, {"AWS_DEFAULT_REGION": "eu-west-1"}, clear=True):
            import importlib
            import src.config
            importlib.reload(src.config)
            assert src.config.DEFAULT_REGION == "eu-west-1"

    def test_mandatory_tags_parsing(self):
        with patch.dict(os.environ, {"MANDATORY_TAGS": "Owner, CostCenter, env"}, clear=True):
            import importlib
            import src.config
            importlib.reload(src.config)
            assert src.config.MANDATORY_TAGS == ["Owner", "CostCenter", "env"]

    def test_boto_retry_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            import importlib
            import src.config
            importlib.reload(src.config)
            assert src.config.BOTO_MAX_RETRIES == 5
            assert src.config.BOTO_RETRY_MODE == "adaptive"

    def test_validate_config_valid(self):
        from src.config import validate_config
        with patch("boto3.client") as mock_boto, \
             patch("src.config.REPORT_BUCKET", "my-bucket"), \
             patch("src.config.GOVERNANCE_SNS_TOPIC_ARN", "arn:sns"):
             
            mock_sts = mock_boto.return_value
            mock_sts.get_caller_identity.return_value = {"Arn": "arn:aws:iam::123:user/test"}
            
            state = validate_config()
            assert state == "VALID"
            mock_boto.assert_called_once()
            args, kwargs = mock_boto.call_args
            assert args[0] == "sts"
            assert "config" in kwargs # Verify config is passed for timeouts

    def test_validate_config_missing_credentials(self, caplog):
        from src.config import validate_config
        from botocore.exceptions import NoCredentialsError
        with patch("boto3.client", side_effect=NoCredentialsError()), \
             patch("src.config.REPORT_BUCKET", "my-bucket"), \
             patch("src.config.GOVERNANCE_SNS_TOPIC_ARN", "arn:sns"):
             
            state = validate_config()
            assert state == "INVALID / MISSING AWS CREDENTIALS"
            assert "INVALID / MISSING AWS CREDENTIALS" in caplog.text

    def test_validate_config_access_denied(self, caplog):
        from src.config import validate_config
        from botocore.exceptions import ClientError
        error_response = {'Error': {'Code': 'AccessDeniedException'}}
        with patch("boto3.client") as mock_boto, \
             patch("src.config.REPORT_BUCKET", "my-bucket"), \
             patch("src.config.GOVERNANCE_SNS_TOPIC_ARN", "arn:sns"):
             
            mock_sts = mock_boto.return_value
            mock_sts.get_caller_identity.side_effect = ClientError(error_response, "GetCallerIdentity")
            
            state = validate_config()
            assert state == "AWS ACCESS DENIED"
            assert "AWS ACCESS DENIED" in caplog.text

    def test_validate_config_service_error(self, caplog):
        from src.config import validate_config
        with patch("boto3.client", side_effect=Exception("Secret password is ABC")):
            state = validate_config()
            assert state == "AWS SERVICE ERROR"
            assert "AWS SERVICE ERROR" in caplog.text
            assert "ABC" not in caplog.text # No secret values in logs

    def test_validate_config_missing_optionals(self, caplog):
        from src.config import validate_config
        with patch("boto3.client") as mock_boto, \
             patch("src.config.REPORT_BUCKET", ""), \
             patch("src.config.GOVERNANCE_SNS_TOPIC_ARN", ""):
             
            mock_sts = mock_boto.return_value
            mock_sts.get_caller_identity.return_value = {"Arn": "arn"}
            
            state = validate_config()
            assert state == "OPTIONAL CONFIGURATION MISSING"
            assert "OPTIONAL CONFIGURATION MISSING" in caplog.text
            assert "REPORT_BUCKET" in caplog.text
            assert "GOVERNANCE_SNS_TOPIC_ARN" in caplog.text

