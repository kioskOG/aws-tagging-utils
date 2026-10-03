import pytest
from unittest.mock import patch, MagicMock
from src.finops.report import FinOpsReportGenerator

def test_finops_calculation_canonical_tag():
    """
    Test that the FinOps report correctly aggregates tagged vs untagged spend 
    from Cost Explorer GroupBy response using a canonical tag.
    """
    mock_schema = MagicMock()
    mock_schema.get_schema.return_value = {
        "CostCenter": MagicMock(finops={"cost_allocation": True})
    }
    
    mock_ce = MagicMock()
    
    # 1. Mock total spend response
    mock_ce.get_cost_and_usage.return_value = {
        'ResultsByTime': [{'Total': {'UnblendedCost': {'Amount': '2000.00'}}}]
    }
    
    # 2. Mock grouped by tag response
    mock_ce.get_cost_by_tag.return_value = {
        'ResultsByTime': [
            {
                'Groups': [
                    # Tagged group 1 (Engineering)
                    {
                        'Keys': ['CostCenter$Engineering'],
                        'Metrics': {'UnblendedCost': {'Amount': '1000.00'}}
                    },
                    # Tagged group 2 (Marketing)
                    {
                        'Keys': ['CostCenter$Marketing'],
                        'Metrics': {'UnblendedCost': {'Amount': '500.00'}}
                    },
                    # Untagged group (Missing/Empty)
                    {
                        'Keys': ['CostCenter$'],
                        'Metrics': {'UnblendedCost': {'Amount': '500.00'}}
                    }
                ]
            }
        ]
    }
    
    generator = FinOpsReportGenerator(mock_ce, mock_schema)
    report = generator.generate_report()
    
    # Total spend is 2000
    assert report["TotalSpend"] == 2000.0
    
    # Tagged spend is 1000 + 500 = 1500
    assert report["TaggedSpend"] == 1500.0
    
    # Untagged spend should be Total - Tagged = 2000 - 1500 = 500
    assert report["UntaggedSpend"] == 500.0
    
    # Allocation percentage should be 1500 / 2000 = 75.0%
    assert report["AllocationPercentage"] == 75.0
    
    # Check warning inclusion
    assert any("CostCenter" in w for w in report["Warnings"])


def test_finops_calculation_empty_or_malformed_response():
    """Test that empty or malformed CE responses are handled gracefully."""
    mock_schema = MagicMock()
    mock_schema.get_schema.return_value = {
        "CostCenter": MagicMock(finops={"cost_allocation": True})
    }
    mock_ce = MagicMock()
    
    # Mock total spend
    mock_ce.get_cost_and_usage.return_value = {
        'ResultsByTime': [{'Total': {'UnblendedCost': {'Amount': '1000.00'}}}]
    }
    
    # Mock empty get_cost_by_tag
    mock_ce.get_cost_by_tag.return_value = {}
    
    generator = FinOpsReportGenerator(mock_ce, mock_schema)
    report = generator.generate_report()
    
    assert report["TotalSpend"] == 1000.0
    assert report["TaggedSpend"] == 0.0
    assert report["UntaggedSpend"] == 1000.0
    assert report["AllocationPercentage"] == 0.0


def test_finops_calculation_zero_spend():
    """Test avoiding division by zero when total spend is 0."""
    mock_schema = MagicMock()
    mock_schema.get_schema.return_value = {
        "CostCenter": MagicMock(finops={"cost_allocation": True})
    }
    mock_ce = MagicMock()
    
    # Mock total spend
    mock_ce.get_cost_and_usage.return_value = {
        'ResultsByTime': [{'Total': {'UnblendedCost': {'Amount': '0.0'}}}]
    }
    mock_ce.get_cost_by_tag.return_value = {
        'ResultsByTime': [{'Groups': []}]
    }
    
    generator = FinOpsReportGenerator(mock_ce, mock_schema)
    report = generator.generate_report()
    
    assert report["TotalSpend"] == 0.0
    assert report["TaggedSpend"] == 0.0
    assert report["UntaggedSpend"] == 0.0
    assert report["AllocationPercentage"] == 0.0


@patch('os.environ.get')
def test_finops_canonical_tag_override(mock_env_get):
    """Test that FINOPS_PRIMARY_TAG environment variable is respected."""
    # Return 'Project' when asked for FINOPS_PRIMARY_TAG
    def mock_get(key, default=None):
        if key == "FINOPS_PRIMARY_TAG":
            return "Project"
        return default
    mock_env_get.side_effect = mock_get
    
    mock_schema = MagicMock()
    # Schema has multiple
    mock_schema.get_schema.return_value = {
        "CostCenter": MagicMock(finops={"cost_allocation": True}),
        "Project": MagicMock(finops={"cost_allocation": True})
    }
    
    mock_ce = MagicMock()
    mock_ce.get_cost_and_usage.return_value = {
        'ResultsByTime': [{'Total': {'UnblendedCost': {'Amount': '100.00'}}}]
    }
    mock_ce.get_cost_by_tag.return_value = {
        'ResultsByTime': [
            {
                'Groups': [
                    {'Keys': ['Project$Alpha'], 'Metrics': {'UnblendedCost': {'Amount': '100.00'}}}
                ]
            }
        ]
    }
    
    generator = FinOpsReportGenerator(mock_ce, mock_schema)
    report = generator.generate_report()
    
    # It should have called get_cost_by_tag with 'Project', not 'CostCenter'
    mock_ce.get_cost_by_tag.assert_called_once_with("Project", mock_ce.get_cost_by_tag.call_args[0][1], mock_ce.get_cost_by_tag.call_args[0][2])
    
    assert report["TaggedSpend"] == 100.0
    assert report["AllocationPercentage"] == 100.0
