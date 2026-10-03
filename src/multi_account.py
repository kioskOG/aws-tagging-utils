import boto3
from typing import Optional
from src.config import MULTI_ACCOUNT_ROLE_NAME

class CrossAccountManager:
    """
    Handles STS AssumeRole logic for organization-wide governance.
    """
    
    @staticmethod
    def assume_role(account_id: str, role_name: str = MULTI_ACCOUNT_ROLE_NAME, session_name: str = "TagGovernance") -> Optional[boto3.Session]:
        """
        Assume a role in a target member account and return a configured boto3 Session.
        """
        sts = boto3.client("sts")
        role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
        
        try:
            response = sts.assume_role(
                RoleArn=role_arn,
                RoleSessionName=session_name,
                DurationSeconds=900 # 15 minutes
            )
            credentials = response["Credentials"]
            
            session = boto3.Session(
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"]
            )
            return session
        except Exception as e:
            # Handle cleanly, e.g., if role doesn't exist yet
            print(f"Failed to assume role {role_arn}: {e}")
            return None
