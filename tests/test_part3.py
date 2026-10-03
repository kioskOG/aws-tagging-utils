import pytest
import json
from unittest.mock import patch, MagicMock
from src.finops.report import FinOpsReportGenerator
from src.authorization.policy import AuthorizationPolicy
from src.authorization.roles import UserIdentity, Role
from src.cli.validator import main

@patch('src.finops.cost_explorer.CostExplorerClient')
def test_finops_report(mock_ce):
    # Mocking cost explorer
    mock_instance = mock_ce.return_value
    mock_instance.get_cost_and_usage.return_value = {
        'ResultsByTime': [{'Total': {'UnblendedCost': {'Amount': '125400.00'}}}]
    }
    
    mock_schema = MagicMock()
    mock_schema.get_schema.return_value = {
        "CostCenter": MagicMock(finops={"cost_allocation": True})
    }
    
    generator = FinOpsReportGenerator(mock_instance, mock_schema)
    report = generator.generate_report()
    
    assert report["TotalSpend"] == 125400.0
    assert "CostCenter" in report["CostAllocationTags"]

def test_rbac_authorization():
    # Admin can do anything
    admin = UserIdentity("user-1", [Role.PLATFORM_ADMIN])
    assert AuthorizationPolicy.can_modify_tags(admin, {"Owner": "someone_else"}) is True
    assert AuthorizationPolicy.can_view_finops(admin) is True
    assert AuthorizationPolicy.can_manage_protected_tags(admin) is True
    
    # App owner can only modify if they own it
    owner = UserIdentity("team-payments", [Role.APP_OWNER])
    assert AuthorizationPolicy.can_modify_tags(owner, {"Application": "team-payments"}) is True
    assert AuthorizationPolicy.can_modify_tags(owner, {"Application": "team-infra"}) is False
    assert AuthorizationPolicy.can_view_finops(owner) is False
    
    # Viewer can do nothing
    viewer = UserIdentity("user-3", [Role.VIEWER])
    assert AuthorizationPolicy.can_modify_tags(viewer, {"Owner": "user-3"}) is False

@patch('argparse.ArgumentParser.parse_args')
@patch('src.cli.validator.validate_directory')
def test_sarif_output(mock_val_dir, mock_args, capsys):
    mock_args.return_value = MagicMock(
        command="validate", 
        path_or_subcommand=".",
        schema="config/tag-schema.yaml",
        format="sarif",
        output=None
    )
    
    # Mock validation results
    from src.governance.models import ValidationResult, ValidationViolation
    mock_res = ValidationResult(compliant=False, resource="file.tf")
    mock_res.violations.append(ValidationViolation(tag="Owner", type="MISSING_REQUIRED"))
    
    mock_val_dir.return_value = {
        "compliant": [],
        "non_compliant": [mock_res]
    }
    
    # Should exit 1 because of non-compliant resources
    import sys
    sys.argv = ['aws-tagging-utils', 'validate', '.', '--format', 'sarif']
    
    try:
        from src.cli.validator import main
        result = main()
        assert result == 1
    except SystemExit as e:
        assert e.code == 1
        
    captured = capsys.readouterr()
    # Output should be valid JSON. We might have some 'Scanning...' logging, so extract the JSON block
    out_str = captured.out
    json_start = out_str.find('{')
    if json_start >= 0:
        out_str = out_str[json_start:]
    output_json = json.loads(out_str)
    assert output_json["version"] == "2.1.0"
    assert len(output_json["runs"][0]["results"]) == 1
    assert output_json["runs"][0]["results"][0]["ruleId"] == "TAG001"
