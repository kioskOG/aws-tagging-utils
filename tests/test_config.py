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
