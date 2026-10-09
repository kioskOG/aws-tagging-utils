import boto3
from boto3.dynamodb.conditions import Attr
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

    @abstractmethod
    def list_exemptions(self) -> list[Dict[str, Any]]:
        pass
        
    @abstractmethod
    def get_exemption(self, exemption_id: str) -> Optional[Dict[str, Any]]:
        pass
        
    @abstractmethod
    def put_exemption(self, exemption: Dict[str, Any]) -> None:
        pass
        
    @abstractmethod
    def delete_exemption(self, exemption_id: str) -> None:
        pass

    @abstractmethod
    def get_remediation_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        pass
        
    @abstractmethod
    def create_remediation_action(self, action: Dict[str, Any]) -> bool:
        pass
        
    @abstractmethod
    def claim_remediation_action(self, action_id: str, current_statuses: list[str], max_attempts: int = 3, worker_id: Optional[str] = None, lease_duration_seconds: int = 300) -> bool:
        pass

    @abstractmethod
    def update_remediation_action(self, action_id: str, updates: Dict[str, Any], expected_status: Optional[str] = None) -> bool:
        pass

    def list_remediation_actions(self, limit: int = 100) -> list[Dict[str, Any]]:
        """Most recent remediation actions first. Stores may override."""
        return []


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

    def list_exemptions(self) -> list[Dict[str, Any]]:
        try:
            from src.errors import APIError
            table = self._get_client()
            response = table.scan(FilterExpression=Attr("PK").begins_with("EXEMPTION#"))
            items = response.get("Items", [])
            # Handle pagination if necessary
            while "LastEvaluatedKey" in response:
                response = table.scan(
                    FilterExpression=Attr("PK").begins_with("EXEMPTION#"),
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
                items.extend(response.get("Items", []))
            return items
        except Exception as e:
            logger.error("Failed to list exemptions from DynamoDB: %s", e)
            from src.errors import map_boto_error, APIError
            status, code, msg = map_boto_error(e)
            raise APIError(msg, status_code=status, error_code=code)

    def get_exemption(self, exemption_id: str) -> Optional[Dict[str, Any]]:
        try:
            table = self._get_client()
            response = table.get_item(Key={"PK": f"EXEMPTION#{exemption_id}"})
            return response.get("Item")
        except Exception as e:
            logger.error("Failed to get exemption from DynamoDB: %s", e)
            from src.errors import map_boto_error, APIError
            status, code, msg = map_boto_error(e)
            raise APIError(msg, status_code=status, error_code=code)

    def put_exemption(self, exemption: Dict[str, Any]) -> None:
        try:
            table = self._get_client()
            item = dict(exemption)
            if "id" in item:
                item["PK"] = f"EXEMPTION#{item['id']}"
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            table.put_item(Item=item)
        except Exception as e:
            logger.error("Failed to put exemption to DynamoDB: %s", e)
            from src.errors import map_boto_error, APIError
            status, code, msg = map_boto_error(e)
            raise APIError(msg, status_code=status, error_code=code)

    def delete_exemption(self, exemption_id: str) -> None:
        try:
            table = self._get_client()
            table.delete_item(Key={"PK": f"EXEMPTION#{exemption_id}"})
        except Exception as e:
            logger.error("Failed to delete exemption from DynamoDB: %s", e)
            from src.errors import map_boto_error, APIError
            status, code, msg = map_boto_error(e)
            raise APIError(msg, status_code=status, error_code=code)

    def list_remediation_actions(self, limit: int = 100) -> list[Dict[str, Any]]:
        try:
            from boto3.dynamodb.conditions import Attr
            table = self._get_client()
            items: list[Dict[str, Any]] = []
            kwargs: Dict[str, Any] = {"FilterExpression": Attr("PK").begins_with("ACTION#")}
            while True:
                response = table.scan(**kwargs)
                items.extend(response.get("Items", []))
                if "LastEvaluatedKey" not in response:
                    break
                kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            items.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
            return items[:limit]
        except Exception as e:
            logger.error("Failed to list remediation actions: %s", e)
            from src.errors import map_boto_error, APIError
            status, code, msg = map_boto_error(e)
            raise APIError(msg, status_code=status, error_code=code)

    def get_remediation_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        try:
            table = self._get_client()
            response = table.get_item(Key={"PK": f"ACTION#{action_id}"})
            return response.get("Item")
        except Exception as e:
            logger.error("Failed to get remediation action: %s", e)
            return None

    def create_remediation_action(self, action: Dict[str, Any]) -> bool:
        try:
            table = self._get_client()
            item = dict(action)
            item["PK"] = f"ACTION#{item['action_id']}"
            table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(PK)"
            )
            return True
        except self.client.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        except Exception as e:
            logger.error("Failed to create remediation action: %s", e)
            raise

    def claim_remediation_action(self, action_id: str, current_statuses: list[str], max_attempts: int = 3, worker_id: Optional[str] = None, lease_duration_seconds: int = 300) -> bool:
        try:
            table = self._get_client()
            now_dt = datetime.now(timezone.utc)
            now = now_dt.isoformat()
            
            from boto3.dynamodb.conditions import Attr
            status_conditions = [Attr("status").eq(s) for s in current_statuses]
            
            if worker_id:
                # If worker_id is provided, we can also claim if it's IN_PROGRESS but the lease has expired
                status_conditions.append(Attr("status").eq("IN_PROGRESS") & Attr("lease_until").lt(now))

            if len(status_conditions) > 1:
                cond: Any = status_conditions[0]
                for c in status_conditions[1:]:
                    cond = cond | c
            else:
                cond = status_conditions[0]
                
            # Add attempt count condition
            attempt_cond = Attr("attempt_count").not_exists() | Attr("attempt_count").lt(max_attempts)
            cond = cond & attempt_cond
                
            update_expr = "SET #s = :new_status, updated_at = :now, attempt_count = if_not_exists(attempt_count, :zero) + :one"
            expr_names = {"#s": "status"}
            expr_values = {
                ":new_status": "IN_PROGRESS",
                ":now": now,
                ":zero": 0,
                ":one": 1
            }

            if worker_id:
                from datetime import timedelta
                lease_until = (now_dt + timedelta(seconds=lease_duration_seconds)).isoformat()
                update_expr += ", worker_id = :worker_id, lease_until = :lease_until, claimed_at = :now"
                expr_values[":worker_id"] = worker_id
                expr_values[":lease_until"] = lease_until
                
            table.update_item(
                Key={"PK": f"ACTION#{action_id}"},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
                ConditionExpression=cond
            )
            return True
        except self.client.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        except Exception as e:
            logger.error("Failed to claim remediation action: %s", e)
            return False

    def update_remediation_action(self, action_id: str, updates: Dict[str, Any], expected_status: Optional[str] = None) -> bool:
        # Tighten state transitions
        if "status" in updates:
            new_status = updates["status"]
            if expected_status == "IN_PROGRESS" and new_status not in ("COMPLETED", "FAILED", "FAILED_RETRYABLE"):
                logger.error(f"Invalid transition from IN_PROGRESS to {new_status}")
                return False
                
        try:
            table = self._get_client()
            
            update_parts = ["updated_at = :now"]
            expr_names = {}
            expr_values = {":now": datetime.now(timezone.utc).isoformat()}
            
            for k, v in updates.items():
                update_parts.append(f"#{k} = :{k}")
                expr_names[f"#{k}"] = k
                expr_values[f":{k}"] = v
                
            update_expr = "SET " + ", ".join(update_parts)
            
            kwargs = {
                "Key": {"PK": f"ACTION#{action_id}"},
                "UpdateExpression": update_expr,
                "ExpressionAttributeValues": expr_values
            }
            if expr_names:
                kwargs["ExpressionAttributeNames"] = expr_names
                
            from boto3.dynamodb.conditions import Attr
            if expected_status:
                kwargs["ConditionExpression"] = Attr("status").eq(expected_status)
                
            table.update_item(**kwargs)
            return True
        except self.client.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        except Exception as e:
            logger.error("Failed to update remediation action: %s", e)
            return False


# Memory state store for testing
class MemoryStateStore(GovernanceStateStore):
    def __init__(self):
        self.store = {}
        
    def get_resource_state(self, resource_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get(f"RES#{resource_id}")
        
    def put_resource_state(self, state: Dict[str, Any]) -> None:
        if "resource_id" in state:
            self.store[f"RES#{state['resource_id']}"] = dict(state)

    def list_exemptions(self) -> list[Dict[str, Any]]:
        return [v for k, v in self.store.items() if k.startswith("EXEMPTION#")]
        
    def get_exemption(self, exemption_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get(f"EXEMPTION#{exemption_id}")
        
    def put_exemption(self, exemption: Dict[str, Any]) -> None:
        if "id" in exemption:
            self.store[f"EXEMPTION#{exemption['id']}"] = dict(exemption)
            
    def delete_exemption(self, exemption_id: str) -> None:
        key = f"EXEMPTION#{exemption_id}"
        if key in self.store:
            del self.store[key]

    def list_remediation_actions(self, limit: int = 100) -> list[Dict[str, Any]]:
        items = [v for k, v in self.store.items() if k.startswith("ACTION#")]
        items.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
        return items[:limit]

    def get_remediation_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get(f"ACTION#{action_id}")
        
    def create_remediation_action(self, action: Dict[str, Any]) -> bool:
        key = f"ACTION#{action['action_id']}"
        if key in self.store:
            return False
        self.store[key] = dict(action)
        return True
        
    def claim_remediation_action(self, action_id: str, current_statuses: list[str], max_attempts: int = 3, worker_id: Optional[str] = None, lease_duration_seconds: int = 300) -> bool:
        key = f"ACTION#{action_id}"
        item = self.store.get(key)
        if not item:
            return False
        
        attempt_count = item.get("attempt_count", 0)
        status = item.get("status")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        
        can_claim = False
        if status in current_statuses:
            can_claim = True
        elif worker_id and status == "IN_PROGRESS":
            lease_until = item.get("lease_until")
            if lease_until and lease_until < now:
                can_claim = True
                
        if can_claim and attempt_count < max_attempts:
            item["status"] = "IN_PROGRESS"
            item["attempt_count"] = attempt_count + 1
            item["updated_at"] = now
            if worker_id:
                from datetime import timedelta
                item["worker_id"] = worker_id
                item["claimed_at"] = now
                item["lease_until"] = (now_dt + timedelta(seconds=lease_duration_seconds)).isoformat()
            return True
        return False

    def update_remediation_action(self, action_id: str, updates: Dict[str, Any], expected_status: Optional[str] = None) -> bool:
        if "status" in updates:
            new_status = updates["status"]
            if expected_status == "IN_PROGRESS" and new_status not in ("COMPLETED", "FAILED", "FAILED_RETRYABLE"):
                return False
                
        key = f"ACTION#{action_id}"
        item = self.store.get(key)
        if not item:
            return False
        if expected_status and item.get("status") != expected_status:
            return False
        item.update(updates)
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
        return True
