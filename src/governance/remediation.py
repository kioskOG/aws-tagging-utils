import os
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import hashlib
import json

from src.governance.models import ValidationResult
from src.governance.state import GovernanceStateStore
from src.governance.exemptions import ExemptionManager
from src.governance.audit import AuditLogger
from src.governance.notifications import NotificationProvider
from src.config import GOVERNANCE_GRACE_PERIOD_DAYS, MAX_REMEDIATION_ATTEMPTS
from src.logging_config import get_logger

logger = get_logger(__name__)

class RemediationEngine:
    def __init__(self, state_store: GovernanceStateStore, exemption_manager: ExemptionManager, notification_provider: Optional[NotificationProvider] = None):
        self.state_store = state_store
        self.exemption_manager = exemption_manager
        self.notification_provider = notification_provider

    def _extract_type(self, arn: str) -> str:
        parts = arn.split(":")
        return parts[2] if len(parts) > 2 else "unknown"

    def _extract_account(self, arn: str) -> str:
        parts = arn.split(":")
        return parts[4] if len(parts) > 4 else "unknown"

    def process_sync(self, request_data: Dict[str, Any], actor: str, request_id: str) -> Dict[str, Any]:
        """
        Main entry point for synchronous API-driven remediation.
        """
        # 1. Validate
        arn = request_data.get("resource_arn")
        tags = request_data.get("requested_tags")
        if not arn or not isinstance(tags, dict):
            from src.errors import APIError
            raise APIError("resource_arn and requested_tags are required", status_code=400, error_code="INVALID_REQUEST")
            
        resource_type = self._extract_type(arn)
        account_id = self._extract_account(arn)
        
        # Calculate canonical request fingerprint
        canon_req = json.dumps({
            "resource_arn": arn,
            "resource_type": resource_type,
            "requested_tags": tags
        }, sort_keys=True)
        request_fingerprint = hashlib.sha256(canon_req.encode()).hexdigest()
        
        # Determine idempotency key and logical action id
        idem_key = request_data.get("idempotency_key")
        if not idem_key:
            idem_key = request_fingerprint
            
        action_id = idem_key # 1:1 mapping as instructed for Phase 2C
        
        now = datetime.now(timezone.utc).isoformat()
        
        # 2. Exemption Check
        exemption = self.exemption_manager.is_exempt(account_id, resource_type, arn)
        status = "SKIPPED_EXEMPT" if exemption else "PENDING"
        
        action_data = {
            "action_id": action_id,
            "idempotency_key": idem_key,
            "resource_arn": arn,
            "resource_type": resource_type,
            "account_id": account_id,
            "requested_tags": tags,
            "actor": actor,
            "status": status,
            "attempt_count": 0,
            "request_fingerprint": request_fingerprint,
            "created_at": now,
            "updated_at": now,
            "request_id": request_id
        }
        if exemption:
            action_data["exemption_id"] = exemption.get("id")
            
        # 3. Create or resolve action
        created = self.state_store.create_remediation_action(action_data)
        if not created:
            # Action exists
            existing = self.state_store.get_remediation_action(action_id)
            if not existing:
                from src.errors import APIError
                raise APIError("Failed to resolve existing remediation action", status_code=500, error_code="INTERNAL_ERROR")
            
            if existing.get("request_fingerprint") and existing.get("request_fingerprint") != request_fingerprint:
                from src.errors import APIError
                raise APIError("Idempotency key mismatch", status_code=409, error_code="IDEMPOTENCY_MISMATCH")
            
            # If already processing or terminal, return it
            if existing.get("status") in ("IN_PROGRESS", "COMPLETED", "SKIPPED_EXEMPT", "FAILED"):
                # But wait, if it's FAILED, we might want to let them retry if they provide a new idem_key? 
                # If they use the same idem_key, they get the existing FAILED state.
                # Only FAILED_RETRYABLE can be picked up, but wait, the prompt says "Future Phase 2D will consume FAILED_RETRYABLE actions."
                # If it's FAILED_RETRYABLE, we CAN retry it now synchronously if they requested it again.
                if existing.get("status") != "FAILED_RETRYABLE":
                    # If it's FAILED (permanent), they should make a new request with a new idempotency key if they fixed the issue (e.g. valid tags).
                    # Actually, if it's derived idempotency key, fixing the tags changes the idempotency key!
                    return existing
            
            action_data = existing
            
        if action_data["status"] == "SKIPPED_EXEMPT":
            from src.db import insert_audit_log
            ex_id = exemption.get("id") if exemption else None
            insert_audit_log(actor, "REMEDIATE_SKIPPED", arn, "SUCCESS", details={"exemption_id": ex_id}, request_id=request_id)
            return action_data
            
        # 4. Claim action
        claimed = self.state_store.claim_remediation_action(action_id, current_statuses=["PENDING", "FAILED_RETRYABLE"], max_attempts=MAX_REMEDIATION_ATTEMPTS)
        if not claimed:
            # Someone else claimed it, or status changed, or exhausted max attempts
            existing_after_claim = self.state_store.get_remediation_action(action_id) or action_data
            if existing_after_claim.get("status") == "FAILED_RETRYABLE" and existing_after_claim.get("attempt_count", 0) >= MAX_REMEDIATION_ATTEMPTS:
                # Transition to FAILED due to exhaustion
                from src.db import insert_audit_log
                if self.state_store.update_remediation_action(action_id, {"status": "FAILED", "last_error_code": "MAX_RETRIES_EXHAUSTED"}, expected_status="FAILED_RETRYABLE"):
                    insert_audit_log(actor, "REMEDIATE_FINISHED", arn, "FAILED", details={"action_id": action_id, "error_code": "MAX_RETRIES_EXHAUSTED"}, request_id=request_id)
                return self.state_store.get_remediation_action(action_id) or existing_after_claim
            return existing_after_claim
            
        from src.db import insert_audit_log
        insert_audit_log(actor, "REMEDIATE_STARTED", arn, "SUCCESS", details={"action_id": action_id}, request_id=request_id)
        
        # 5. Execute Tag Mutation
        new_status, error_code = self._execute_tag_mutation(tags, arn)
            
        # 6. Update action state
        # Note: attempt_count was atomically incremented during claim_remediation_action
        updates = {
            "status": new_status,
            "last_attempt_at": datetime.now(timezone.utc).isoformat()
        }
        if error_code:
            updates["last_error_code"] = error_code
            
        self.state_store.update_remediation_action(action_id, updates, expected_status="IN_PROGRESS")
        
        # 7. Audit Outcome
        insert_audit_log(actor, "REMEDIATE_FINISHED", arn, new_status, details={"action_id": action_id, "error_code": error_code}, request_id=request_id)
        from src.observability.metrics import record_remediation
        record_remediation(new_status)
        
        return self.state_store.get_remediation_action(action_id) or updates

    def _execute_tag_mutation(self, tags: Dict[str, Any], arn: str) -> tuple[str, Optional[str]]:
        from src.tag_writer import tag_resources, governance_engine
        from src.config import DEFAULT_REGION, GOVERNANCE_STRICT_MODE
        from botocore.exceptions import BotoCoreError, ClientError
        
        new_status = "FAILED"
        error_code = None
        try:
            # validate tags (engine)
            val_res = governance_engine.evaluate(tags, arn)
            if not val_res.compliant and GOVERNANCE_STRICT_MODE:
                raise ValueError("Tag validation failed in strict mode")
                    
            tags_to_apply = val_res.normalized_tags
            
            # tag_resources returns {"tagged_count": X, "failed_resources": {...}}
            result = tag_resources([arn], tags_to_apply, DEFAULT_REGION)
            
            if arn in result.get("failed_resources", {}):
                err = result["failed_resources"][arn]
                error_code = err.get("ErrorCode", "Unknown")
                # Classify error
                if error_code in ("Throttling", "ThrottlingException", "TooManyRequestsException", "InternalError"):
                    new_status = "FAILED_RETRYABLE"
                elif error_code in ("AccessDenied", "AccessDeniedException"):
                    new_status = "FAILED"
                else:
                    new_status = "FAILED"
            else:
                new_status = "COMPLETED"
                
        except ValueError as e:
            error_code = "VALIDATION_FAILED"
            new_status = "FAILED"
        except (ClientError, BotoCoreError) as e:
            from src.errors import map_boto_error
            status_code, mapped_code, msg = map_boto_error(e)
            error_code = mapped_code
            if mapped_code in ("THROTTLED", "AWS_SERVICE_ERROR"):
                new_status = "FAILED_RETRYABLE"
            else:
                new_status = "FAILED"
        except Exception as e:
            error_code = "INTERNAL_ERROR"
            new_status = "FAILED"
            
        return new_status, error_code

    def process_async(self, action_id: str, worker_id: str, correlation_id: str) -> bool:
        """
        Processes a remediation action asynchronously.
        Returns True if the message should be deleted from SQS (success or terminal state).
        Returns False if the message should be kept in SQS (transient error, failed lease, etc).
        """
        action = self.state_store.get_remediation_action(action_id)
        if not action:
            logger.error(f"Remediation action {action_id} not found in state store")
            return False

        status = action.get("status")
        
        if status in ("COMPLETED", "FAILED", "SKIPPED_EXEMPT"):
            logger.info(f"Action {action_id} is already in terminal state: {status}")
            return True
            
        arn = action.get("resource_arn")
        tags = action.get("requested_tags")
        actor = action.get("actor", "system")
        
        claimed = self.state_store.claim_remediation_action(
            action_id, 
            current_statuses=["PENDING", "FAILED_RETRYABLE"], 
            max_attempts=MAX_REMEDIATION_ATTEMPTS,
            worker_id=worker_id,
            lease_duration_seconds=300
        )
        
        if not claimed:
            action_after = self.state_store.get_remediation_action(action_id)
            if action_after:
                if action_after.get("status") == "FAILED_RETRYABLE" and action_after.get("attempt_count", 0) >= MAX_REMEDIATION_ATTEMPTS:
                    from src.db import insert_audit_log
                    if self.state_store.update_remediation_action(action_id, {"status": "FAILED", "last_error_code": "MAX_RETRIES_EXHAUSTED"}, expected_status="FAILED_RETRYABLE"):
                        insert_audit_log(actor, "REMEDIATE_FINISHED", arn, "FAILED", details={"action_id": action_id, "error_code": "MAX_RETRIES_EXHAUSTED", "worker_id": worker_id}, request_id=correlation_id)
                    return True
                if action_after.get("status") in ("COMPLETED", "FAILED", "SKIPPED_EXEMPT"):
                    return True
            
            logger.info(f"Failed to claim lease for action {action_id}")
            return False
            
        from src.db import insert_audit_log
        insert_audit_log(actor, "REMEDIATE_STARTED", arn, "SUCCESS", details={"action_id": action_id, "worker_id": worker_id}, request_id=correlation_id)
        
        new_status, error_code = self._execute_tag_mutation(tags, arn)
        
        updates = {
            "status": new_status,
            "last_attempt_at": datetime.now(timezone.utc).isoformat()
        }
        if error_code:
            updates["last_error_code"] = error_code
            
        self.state_store.update_remediation_action(action_id, updates, expected_status="IN_PROGRESS")
        
        insert_audit_log(actor, "REMEDIATE_FINISHED", arn, new_status, details={"action_id": action_id, "error_code": error_code, "worker_id": worker_id}, request_id=correlation_id)
        
        if new_status == "FAILED_RETRYABLE":
            return False
        return True
