from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import json
import os
from src.governance.state import GovernanceStateStore
from src.logging_config import get_logger

logger = get_logger(__name__)

class ExemptionManager:
    def __init__(self, state_store: GovernanceStateStore):
        self.state_store = state_store
        
        # In a real implementation, we would query DynamoDB for exemptions.
        # Since DynamoDB requires a setup, we'll implement a hybrid/mock behavior for testing.
        self._mock_exemptions = []
        
    def load_local_exemptions(self, file_path: str):
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                self._mock_exemptions = json.load(f)

    def is_exempt(self, account_id: str, resource_type: str, resource_id: str, environment: str = None) -> Optional[Dict[str, Any]]:
        """
        Check if a resource is exempt from remediation or termination.
        Returns the exemption record if exempt, None otherwise.
        """
        # First check local mock exemptions (for testability)
        for ex in self._mock_exemptions:
            if not self._is_active(ex):
                continue
            if self._matches(ex, account_id, resource_type, resource_id, environment):
                return ex
                
        # In a full DynamoDB implementation, we would query the table:
        # PK=EXEMPTION#{resource_id} or PK=EXEMPTION#ACCOUNT#{account_id}
        # For simplicity here, we assume if it's not in mock, it's not exempt.
        return None
        
    def _is_active(self, exemption: Dict[str, Any]) -> bool:
        if exemption.get("status") != "ACTIVE":
            return False
        expires_at = exemption.get("expires_at")
        if expires_at:
            try:
                exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp_dt:
                    return False
            except Exception:
                pass
        return True
        
    def _matches(self, exemption: Dict[str, Any], account_id: str, resource_type: str, resource_id: str, environment: str) -> bool:
        match = False
        # Specific resource match
        if exemption.get("resource_id") == resource_id:
            match = True
        # Account match
        elif exemption.get("account_id") == account_id and not exemption.get("resource_id"):
            match = True
        # Resource type match
        elif exemption.get("resource_type") == resource_type and not exemption.get("resource_id"):
            match = True
        # Environment match
        elif environment and exemption.get("environment") == environment and not exemption.get("resource_id"):
            match = True
            
        return match
