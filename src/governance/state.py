import boto3
import json
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from src.config import DEFAULT_REGION, GOVERNANCE_DYNAMODB_TABLE
from src.logging_config import get_logger

logger = get_logger(__name__)

class GovernanceStateStore(ABC):
    @abstractmethod
    def get_resource_state(self, resource_id: str) -> Optional[Dict[str, Any]]:
        pass
        
    @abstractmethod
    def put_resource_state(self, state: Dict[str, Any]) -> None:
        pass


class DynamoDBStateStore(GovernanceStateStore):
    def __init__(self, table_name: str = GOVERNANCE_DYNAMODB_TABLE, region: str = DEFAULT_REGION):
        self.table_name = table_name
        self.region = region
        self.client = None
        
    def _get_client(self):
        if not self.client:
            self.client = boto3.resource('dynamodb', region_name=self.region)
            self.table = self.client.Table(self.table_name)
        return self.table
        
    def get_resource_state(self, resource_id: str) -> Optional[Dict[str, Any]]:
        try:
            table = self._get_client()
            response = table.get_item(Key={"PK": f"RES#{resource_id}"})
            item = response.get("Item")
            return item
        except Exception as e:
            logger.error("Failed to get resource state from DynamoDB: %s", e)
            return None

    def put_resource_state(self, state: Dict[str, Any]) -> None:
        try:
            table = self._get_client()
            # Construct item ensuring it has PK
            item = dict(state)
            if "resource_id" in item:
                item["PK"] = f"RES#{item['resource_id']}"
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            
            # Simple put_item for now, could use condition expressions for idempotency
            table.put_item(Item=item)
        except Exception as e:
            logger.error("Failed to put resource state to DynamoDB: %s", e)

# Memory state store for testing
class MemoryStateStore(GovernanceStateStore):
    def __init__(self):
        self.store = {}
        
    def get_resource_state(self, resource_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get(resource_id)
        
    def put_resource_state(self, state: Dict[str, Any]) -> None:
        if "resource_id" in state:
            self.store[state["resource_id"]] = dict(state)
