import os
from abc import ABC, abstractmethod
from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta

from src.governance.models import ValidationResult
from src.governance.state import GovernanceStateStore
from src.governance.exemptions import ExemptionManager
from src.governance.audit import AuditLogger
from src.governance.notifications import NotificationProvider
from src.config import GOVERNANCE_GRACE_PERIOD_DAYS, GOVERNANCE_TERMINATION_ENABLED
from src.logging_config import get_logger

logger = get_logger(__name__)

class RemediationAction(ABC):
    @abstractmethod
    def validate(self, context: Dict[str, Any]) -> bool:
        pass
        
    @abstractmethod
    def execute(self, context: Dict[str, Any]) -> bool:
        pass


class NormalizeTagsAction(RemediationAction):
    def validate(self, context: Dict[str, Any]) -> bool:
        return "normalized_tags" in context and context.get("current_tags") != context.get("normalized_tags")
        
    def execute(self, context: Dict[str, Any]) -> bool:
        # In a real implementation, this would call TagWriter to update the resource's tags
        logger.info("Executing NormalizeTagsAction for %s", context.get("resource_id"))
        AuditLogger.log("TAG_REMEDIATED", "NORMALIZE_TAG", context.get("account_id"), context.get("region"), context.get("resource_id"), context.get("resource_type"), "SUCCESS")
        return True


class NotifyOwnerAction(RemediationAction):
    def __init__(self, provider: NotificationProvider):
        self.provider = provider
        
    def validate(self, context: Dict[str, Any]) -> bool:
        return self.provider is not None
        
    def execute(self, context: Dict[str, Any]) -> bool:
        logger.info("Executing NotifyOwnerAction for %s", context.get("resource_id"))
        success = self.provider.notify(
            "WARNING", 
            "Resource is non-compliant with tagging governance policies", 
            context
        )
        AuditLogger.log("OWNER_NOTIFIED", "SEND_WARNING", context.get("account_id"), context.get("region"), context.get("resource_id"), context.get("resource_type"), "SUCCESS" if success else "FAILED")
        return success


class RevertProtectedTagAction(RemediationAction):
    def execute(self, account_id: str, region: str, resource_type: str, resource_id: str, tags: Dict[str, str], val_res: ValidationResult) -> Dict[str, Any]:
        """
        Reverts an unauthorized change to a protected tag.
        In a full implementation, it would determine the expected value (from state, schema, or CMDB) and revert.
        """
        from src.config import DRIFT_AUTO_REVERT
        
        if not DRIFT_AUTO_REVERT:
            logger.info("DRIFT_AUTO_REVERT is false. Skipping reversion of protected tag for %s", resource_id)
            return {"action": "REVERT_SKIPPED"}
            
        logger.info("Reverting protected tags for %s", resource_id)
        # Mocking the boto3 call to revert the tag
        # In reality we would call ec2/s3 tag API to set the value back to normal
        return {"action": "REVERT_EXECUTED", "resource_id": resource_id}

class TerminateResourceAction(RemediationAction):
    def validate(self, context: Dict[str, Any]) -> bool:
        if not GOVERNANCE_TERMINATION_ENABLED:
            logger.info("Termination action blocked: GOVERNANCE_TERMINATION_ENABLED is False")
            return False
            
        state = context.get("state", {})
        if not state:
            return False
            
        # Verify grace period expired
        remediation_deadline = state.get("remediation_deadline")
        if not remediation_deadline:
            return False
            
        try:
            deadline_dt = datetime.fromisoformat(remediation_deadline.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) <= deadline_dt:
                logger.info("Termination action blocked: grace period not expired")
                return False
        except Exception:
            return False
            
        # Needs to verify resource is still non-compliant
        val_res = context.get("validation_result")
        if val_res and val_res.compliant:
            return False
            
        return True
        
    def execute(self, context: Dict[str, Any]) -> bool:
        logger.critical("Executing TerminateResourceAction for %s (Implementation Stub)", context.get("resource_id"))
        AuditLogger.log("RESOURCE_TERMINATED", "TERMINATE", context.get("account_id"), context.get("region"), context.get("resource_id"), context.get("resource_type"), "SUCCESS")
        return True


class RemediationEngine:
    def __init__(self, state_store: GovernanceStateStore, exemption_manager: ExemptionManager, notification_provider: NotificationProvider = None):
        self.state_store = state_store
        self.exemption_manager = exemption_manager
        self.notification_provider = notification_provider
        
    def process(self, account_id: str, region: str, resource_type: str, resource_id: str, tags: Dict[str, str], validation_result: ValidationResult) -> Dict[str, Any]:
        """
        Main entry point for evaluating remediation workflow on a resource.
        """
        # 1. Exemption Check
        exemption = self.exemption_manager.is_exempt(account_id, resource_type, resource_id)
        if exemption:
            logger.info("Resource %s is exempt. Reason: %s", resource_id, exemption.get("reason"))
            # Update state with exemption
            self._update_state(account_id, region, resource_type, resource_id, "EXEMPT", exemption_id=exemption.get("id"))
            return {"status": "EXEMPT", "exemption": exemption}
            
        if validation_result.compliant:
            logger.info("Resource %s is compliant", resource_id)
            self._update_state(account_id, region, resource_type, resource_id, "COMPLIANT")
            return {"status": "COMPLIANT"}
            
        # Resource is Non-Compliant
        # 2. Check current state
        state = self.state_store.get_resource_state(resource_id)
        
        if not state or state.get("status") == "COMPLIANT":
            # First time detection or regression
            now = datetime.now(timezone.utc)
            deadline = now + timedelta(days=GOVERNANCE_GRACE_PERIOD_DAYS)
            
            state = self._update_state(
                account_id, region, resource_type, resource_id, "NON_COMPLIANT", 
                first_detected=now.isoformat(), 
                deadline=deadline.isoformat(),
                violations=[v.to_dict() for v in validation_result.violations]
            )
            
            AuditLogger.log("TAG_VALIDATION_FAILED", "DETECT", account_id, region, resource_id, resource_type, "SUCCESS", details={"violations": state.get("violations")})
            
            # Action: Notify Owner
            if self.notification_provider:
                action = NotifyOwnerAction(self.notification_provider)
                context = {"account_id": account_id, "region": region, "resource_id": resource_id, "resource_type": resource_type, "deadline": state.get("remediation_deadline")}
                if action.validate(context):
                    action.execute(context)
                    
            # Action: Normalize Tags (Safe)
            norm_action = NormalizeTagsAction()
            context = {"account_id": account_id, "region": region, "resource_id": resource_id, "resource_type": resource_type, "current_tags": tags, "normalized_tags": validation_result.normalized_tags}
            if norm_action.validate(context):
                norm_action.execute(context)
                
            return {"status": "NON_COMPLIANT_DETECTED", "deadline": state.get("remediation_deadline")}
            
        # Already tracked as NON_COMPLIANT
        deadline_str = state.get("remediation_deadline")
        try:
            deadline = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > deadline:
                # Deadline expired
                AuditLogger.log("RESOURCE_TERMINATION_ELIGIBLE", "DEADLINE_EXPIRED", account_id, region, resource_id, resource_type, "SUCCESS")
                
                term_action = TerminateResourceAction()
                context = {"account_id": account_id, "region": region, "resource_id": resource_id, "resource_type": resource_type, "state": state, "validation_result": validation_result}
                if term_action.validate(context):
                    term_action.execute(context)
                    self._update_state(account_id, region, resource_type, resource_id, "TERMINATED")
                    return {"status": "TERMINATED"}
                else:
                    return {"status": "TERMINATION_BLOCKED"}
                    
            else:
                # Within grace period, just wait
                return {"status": "GRACE_PERIOD_ACTIVE", "deadline": deadline_str}
        except Exception as e:
            logger.error("Failed to process deadline logic: %s", e)
            return {"status": "ERROR", "message": str(e)}

    def _update_state(self, account_id: str, region: str, resource_type: str, resource_id: str, status: str, first_detected: str = None, deadline: str = None, exemption_id: str = None, violations: List[Dict] = None) -> Dict[str, Any]:
        state = self.state_store.get_resource_state(resource_id) or {}
        
        state.update({
            "resource_id": resource_id,
            "resource_type": resource_type,
            "account_id": account_id,
            "region": region,
            "status": status,
            "last_seen_at": datetime.now(timezone.utc).isoformat()
        })
        
        if first_detected:
            state["first_detected_at"] = first_detected
        if deadline:
            state["remediation_deadline"] = deadline
        if exemption_id:
            state["exemption_id"] = exemption_id
        if violations is not None:
            state["violations"] = violations
            
        self.state_store.put_resource_state(state)
        return state
