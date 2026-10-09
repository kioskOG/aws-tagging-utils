import pytest
from typing import Dict
from src.governance.models import TagSchema
from src.governance.engine import TagGovernanceEngine
from src.governance.schema_provider import SchemaProvider


class MockSchemaProvider(SchemaProvider):
    def get_schema(self) -> Dict[str, TagSchema]:
        return {
            "Owner": TagSchema(key="Owner", required=True),
            "CostCenter": TagSchema(key="CostCenter", required=True, pattern="^[0-9]{6}$", aliases=["costcenter", "Cost-Center"]),
            "Environment": TagSchema(key="Environment", required=True, allowed_values=["dev", "prod"], aliases=["env"]),
        }

@pytest.fixture
def engine():
    return TagGovernanceEngine(MockSchemaProvider(), unknown_tags_behavior="warn", enable_normalization=True)

def test_valid_tags(engine):
    tags = {"Owner": "team-a", "CostCenter": "123456", "Environment": "dev"}
    result = engine.evaluate(tags)
    assert result.compliant is True
    assert len(result.violations) == 0

def test_missing_required(engine):
    tags = {"Owner": "team-a", "Environment": "dev"} # Missing CostCenter
    result = engine.evaluate(tags)
    assert result.compliant is False
    assert any(v.type == "MISSING_REQUIRED" and v.tag == "CostCenter" for v in result.violations)

def test_invalid_enum(engine):
    tags = {"Owner": "team-a", "CostCenter": "123456", "Environment": "qa"} # qa not in allowed_values
    result = engine.evaluate(tags)
    assert result.compliant is False
    assert any(v.type == "INVALID_VALUE" and v.tag == "Environment" for v in result.violations)

def test_invalid_regex(engine):
    tags = {"Owner": "team-a", "CostCenter": "1234", "Environment": "dev"} # 4 digits instead of 6
    result = engine.evaluate(tags)
    assert result.compliant is False
    assert any(v.type == "INVALID_FORMAT" and v.tag == "CostCenter" for v in result.violations)

def test_unknown_tag(engine):
    strict_engine = TagGovernanceEngine(MockSchemaProvider(), unknown_tags_behavior="deny", enable_normalization=True)
    tags = {"Owner": "team-a", "CostCenter": "123456", "Environment": "dev", "ExtraTag": "value"}
    result = strict_engine.evaluate(tags)
    assert result.compliant is False
    assert any(v.type == "UNKNOWN_TAG" and v.tag == "ExtraTag" for v in result.violations)

def test_unknown_tag_warn_mode_stays_compliant(engine):
    # e.g. a VPC with the console "Name" tag must not fail compliance in warn mode
    tags = {"Owner": "team-a", "CostCenter": "123456", "Environment": "dev", "Name": "netbird-dev"}
    result = engine.evaluate(tags)
    assert result.compliant is True
    assert result.violations == []
    assert any(w.type == "UNKNOWN_TAG" and w.tag == "Name" for w in result.warnings)

def test_aws_reserved_tags_never_unknown():
    strict_engine = TagGovernanceEngine(MockSchemaProvider(), unknown_tags_behavior="reject", enable_normalization=True)
    tags = {"Owner": "team-a", "CostCenter": "123456", "Environment": "dev", "aws:cloudformation:stack-name": "s"}
    result = strict_engine.evaluate(tags)
    assert result.compliant is True
    assert result.warnings == []

def test_normalization_alias(engine):
    tags = {"Owner": "team-a", "costcenter": "123456", "env": "prod"} 
    result = engine.evaluate(tags)
    assert result.compliant is True
    # Verify the output normalized tags
    assert "CostCenter" in result.normalized_tags
    assert "Environment" in result.normalized_tags
    assert result.normalized_tags["CostCenter"] == "123456"

def test_normalization_conflict(engine):
    tags = {"Owner": "team-a", "CostCenter": "123456", "costcenter": "654321", "env": "prod"}
    result = engine.evaluate(tags)
    assert result.compliant is False
    assert any(v.type == "NORMALIZATION_CONFLICT" and v.tag == "CostCenter" for v in result.violations)
