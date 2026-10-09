import pytest
from unittest.mock import patch, MagicMock
from src.multi_account import CrossAccountManager

@patch('boto3.client')
@patch('boto3.Session')
def test_assume_role_success(mock_session, mock_client):
    mock_sts = MagicMock()
    mock_client.return_value = mock_sts
    
    mock_sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "ASIAXXX",
            "SecretAccessKey": "SECRET",
            "SessionToken": "TOKEN"
        }
    }
    
    mock_session_instance = MagicMock()
    mock_session.return_value = mock_session_instance
    
    session = CrossAccountManager.assume_role("123456789012", "TestRole")
    
    mock_sts.assume_role.assert_called_once_with(
        RoleArn="arn:aws:iam::123456789012:role/TestRole",
        RoleSessionName="TagGovernance",
        DurationSeconds=900
    )
    
    assert session == mock_session_instance

@patch('boto3.client')
def test_assume_role_failure(mock_client):
    mock_sts = MagicMock()
    mock_client.return_value = mock_sts
    
    mock_sts.assume_role.side_effect = Exception("Access Denied")
    
    session = CrossAccountManager.assume_role("123456789012", "TestRole")
    
    assert session is None
