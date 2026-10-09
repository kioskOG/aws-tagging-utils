import pytest
from src.governance.models import TagSchema
from src.cli.generators import ConfigRuleGenerator, SCPGenerator

@pytest.fixture
def sample_schema():
    return {
        "Owner": TagSchema(key="Owner", required=True),
        "Environment": TagSchema(key="Environment", required=True, allowed_values=["dev", "prod"]),
        "CostCenter": TagSchema(key="CostCenter", required=False, pattern="^[0-9]{6}$")
    }

def test_config_rule_generator(sample_schema):
    generator = ConfigRuleGenerator(sample_schema)
    template = generator.generate()
    
    assert template["AWSTemplateFormatVersion"] == "2010-09-09"
    resources = template["Resources"]
    
    # CostCenter is not required, so it shouldn't generate a required rule
    assert "RequiredTagOwner" in resources
    assert "RequiredTagEnvironment" in resources
    assert "RequiredTagCostCenter" not in resources
    
    owner_rule = resources["RequiredTagOwner"]
    assert owner_rule["Type"] == "AWS::Config::ConfigRule"
    assert owner_rule["Properties"]["InputParameters"]["tag1Key"] == "Owner"

def test_scp_generator(sample_schema):
    generator = SCPGenerator(sample_schema)
    policy = generator.generate()
    
    assert policy["Version"] == "2012-10-17"
    assert len(policy["Statement"]) == 1
    
    statement = policy["Statement"][0]
    assert statement["Effect"] == "Deny"
    
    # Check conditions
    conditions = statement["Condition"]["Null"]
    assert "aws:RequestTag/Owner" in conditions
    assert "aws:RequestTag/Environment" in conditions
    assert "aws:RequestTag/CostCenter" not in conditions
