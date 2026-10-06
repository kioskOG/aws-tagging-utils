import re

with open("src/governance/state.py", "r") as f:
    content = f.read()

# Add to GovernanceStateStore
abstract_methods = """
    @abstractmethod
    def get_remediation_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        pass
        
    @abstractmethod
    def create_remediation_action(self, action: Dict[str, Any]) -> bool:
        pass
        
    @abstractmethod
    def claim_remediation_action(self, action_id: str, current_statuses: list[str]) -> bool:
        pass

    @abstractmethod
    def update_remediation_action(self, action_id: str, updates: Dict[str, Any], expected_status: str = None) -> bool:
        pass
"""
content = content.replace("    def delete_exemption(self, exemption_id: str) -> None:\n        pass", "    def delete_exemption(self, exemption_id: str) -> None:\n        pass\n" + abstract_methods)

# Add to DynamoDBStateStore
dynamo_methods = """
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

    def claim_remediation_action(self, action_id: str, current_statuses: list[str]) -> bool:
        try:
            table = self._get_client()
            now = datetime.now(timezone.utc).isoformat()
            
            # Construct condition expression for multiple allowed current statuses
            from boto3.dynamodb.conditions import Or, Attr
            status_conditions = [Attr("status").eq(s) for s in current_statuses]
            if len(status_conditions) > 1:
                cond = status_conditions[0]
                for c in status_conditions[1:]:
                    cond = cond | c
            else:
                cond = status_conditions[0]
                
            table.update_item(
                Key={"PK": f"ACTION#{action_id}"},
                UpdateExpression="SET #s = :new_status, updated_at = :now",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":new_status": "IN_PROGRESS",
                    ":now": now
                },
                ConditionExpression=cond
            )
            return True
        except self.client.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        except Exception as e:
            logger.error("Failed to claim remediation action: %s", e)
            return False

    def update_remediation_action(self, action_id: str, updates: Dict[str, Any], expected_status: str = None) -> bool:
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
"""
content = content.replace("        except Exception as e:\n            logger.error(\"Failed to delete exemption from DynamoDB: %s\", e)\n            from src.errors import map_boto_error, APIError\n            status, code, msg = map_boto_error(e)\n            raise APIError(msg, status_code=status, error_code=code)", "        except Exception as e:\n            logger.error(\"Failed to delete exemption from DynamoDB: %s\", e)\n            from src.errors import map_boto_error, APIError\n            status, code, msg = map_boto_error(e)\n            raise APIError(msg, status_code=status, error_code=code)\n" + dynamo_methods)

# Add to MemoryStateStore
memory_methods = """
    def get_remediation_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get(f"ACTION#{action_id}")
        
    def create_remediation_action(self, action: Dict[str, Any]) -> bool:
        key = f"ACTION#{action['action_id']}"
        if key in self.store:
            return False
        self.store[key] = dict(action)
        return True
        
    def claim_remediation_action(self, action_id: str, current_statuses: list[str]) -> bool:
        key = f"ACTION#{action_id}"
        item = self.store.get(key)
        if not item:
            return False
        if item.get("status") in current_statuses:
            item["status"] = "IN_PROGRESS"
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            return True
        return False

    def update_remediation_action(self, action_id: str, updates: Dict[str, Any], expected_status: str = None) -> bool:
        key = f"ACTION#{action_id}"
        item = self.store.get(key)
        if not item:
            return False
        if expected_status and item.get("status") != expected_status:
            return False
        item.update(updates)
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
        return True
"""
content += memory_methods

with open("src/governance/state.py", "w") as f:
    f.write(content)
