from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import json
import os
import uuid
from src.governance.state import GovernanceStateStore
from src.logging_config import get_logger

logger = get_logger(__name__)


def parse_expiry(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 date/datetime; values without a timezone are treated as UTC."""
    if value in (None, ""):
        return None
    dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class ExemptionManager:
    def __init__(self, state_store: GovernanceStateStore):
        self.state_store = state_store
        
    def _get_scope_rank(self, exemption: Dict[str, Any]) -> int:
        """
        Determines the specificity rank of the exemption based on defined scopes.
        Returns -1 if the combination is unsupported.
        """
        has_res_id = bool(exemption.get("resource_id"))
        has_res_type = bool(exemption.get("resource_type"))
        has_env = bool(exemption.get("environment"))
        has_acc = bool(exemption.get("account_id"))
        
        # Rank 5: resource_id only
        if has_res_id and not (has_res_type or has_env or has_acc):
            return 5
            
        # Rank 4: resource_type + environment
        if has_res_type and has_env and not (has_res_id or has_acc):
            return 4
            
        # Rank 3: resource_type
        if has_res_type and not (has_res_id or has_env or has_acc):
            return 3
            
        # Rank 2: account_id + environment
        if has_acc and has_env and not (has_res_id or has_res_type):
            return 2
            
        # Rank 1: account_id
        if has_acc and not (has_res_id or has_res_type or has_env):
            return 1
            
        return -1

    def is_valid_scope(self, exemption: Dict[str, Any]) -> bool:
        return self._get_scope_rank(exemption) > 0

    def create_exemption(self, exemption_data: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        from src.errors import APIError
        if not self.is_valid_scope(exemption_data):
            raise APIError("Unsupported exemption scope combination.", status_code=400, error_code="INVALID_EXEMPTION_SCOPE")
        exemption_data = dict(exemption_data)
        if "reason" in exemption_data:
            reason = str(exemption_data.get("reason") or "").strip()
            if len(reason) > 1000:
                raise APIError("Reason must be at most 1000 characters.", status_code=400, error_code="INVALID_REQUEST")
            exemption_data["reason"] = reason
        if exemption_data.get("expires_at"):
            try:
                expiry = parse_expiry(exemption_data["expires_at"])
            except ValueError:
                raise APIError("expires_at must be an ISO-8601 date or datetime.", status_code=400, error_code="INVALID_REQUEST")
            # Normalized to an explicit UTC offset so expiry checks never compare naive/aware datetimes
            exemption_data["expires_at"] = expiry.isoformat()
            
        rank = self._get_scope_rank(exemption_data)
        
        # Check for conflicts (equal specificity with same exact scope)
        active_exemptions = self.list_active_exemptions()
        for active in active_exemptions:
            if self._get_scope_rank(active) == rank:
                # Compare fields exactly
                if (active.get("resource_id") == exemption_data.get("resource_id") and
                    active.get("resource_type") == exemption_data.get("resource_type") and
                    active.get("environment") == exemption_data.get("environment") and
                    active.get("account_id") == exemption_data.get("account_id")):
                    raise APIError("A conflicting exemption with equal specificity already exists.", status_code=409, error_code="EXEMPTION_CONFLICT")
                    
        exemption_id = str(uuid.uuid4())
        record = {
            "id": exemption_id,
            "status": "ACTIVE",
            "created_by": actor_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        for k in ["resource_id", "resource_type", "environment", "account_id", "expires_at", "reason"]:
            if k in exemption_data:
                record[k] = exemption_data[k]
                
        self.state_store.put_exemption(record)
        return record

    def revoke_exemption(self, exemption_id: str) -> None:
        from src.errors import APIError
        existing = self.state_store.get_exemption(exemption_id)
        if not existing:
            raise APIError("Exemption not found", status_code=404, error_code="NOT_FOUND")
        existing["status"] = "REVOKED"
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.state_store.put_exemption(existing)

    def get_exemption(self, exemption_id: str) -> Optional[Dict[str, Any]]:
        return self.state_store.get_exemption(exemption_id)

    def list_active_exemptions(self) -> List[Dict[str, Any]]:
        all_ex = self.state_store.list_exemptions()
        return [ex for ex in all_ex if self._is_active(ex)]

    def is_exempt(self, account_id: str, resource_type: str, resource_id: str, environment: Optional[str] = None,
                  active: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
        """
        Evaluate all active exemptions against the resource deterministic precedence rules.
        Pass `active` (from list_active_exemptions) to evaluate many resources with one lookup.
        """
        active_exemptions = active if active is not None else self.list_active_exemptions()
        
        matching_exemptions = []
        for ex in active_exemptions:
            if self._matches(ex, account_id, resource_type, resource_id, environment):
                matching_exemptions.append(ex)
                
        if not matching_exemptions:
            return None
            
        # Group by rank
        ranked: Dict[int, List[Dict[str, Any]]] = {}
        for ex in matching_exemptions:
            rank = self._get_scope_rank(ex)
            if rank not in ranked:
                ranked[rank] = []
            ranked[rank].append(ex)
            
        # Find highest rank
        highest_rank = max(ranked.keys())
        highest_rank_exemptions = ranked[highest_rank]
        
        if len(highest_rank_exemptions) > 1:
            logger.error(
                "CONFLICTING EXEMPTIONS: Resource %s (acc: %s, type: %s, env: %s) matched %d exemptions at rank %d. Failing closed.",
                resource_id, account_id, resource_type, environment, len(highest_rank_exemptions), highest_rank
            )
            return None
            
        return highest_rank_exemptions[0]
        
    def _is_active(self, exemption: Dict[str, Any]) -> bool:
        if exemption.get("status") != "ACTIVE":
            return False
        expires_at = exemption.get("expires_at")
        if expires_at:
            try:
                exp_dt = parse_expiry(expires_at)
            except ValueError:
                # Unparseable expiry: fail closed rather than exempting forever
                logger.warning("Exemption %s has invalid expires_at %r; treating as expired",
                               exemption.get("id"), expires_at)
                return False
            if exp_dt and datetime.now(timezone.utc) > exp_dt:
                return False
        return True
        
    def _matches(self, exemption: Dict[str, Any], account_id: str, resource_type: str, resource_id: str, environment: Optional[str]) -> bool:
        rank = self._get_scope_rank(exemption)
        if rank == 5:
            return exemption.get("resource_id") == resource_id
        if rank == 4:
            return exemption.get("resource_type") == resource_type and exemption.get("environment") == environment
        if rank == 3:
            return exemption.get("resource_type") == resource_type
        if rank == 2:
            return exemption.get("account_id") == account_id and exemption.get("environment") == environment
        if rank == 1:
            return exemption.get("account_id") == account_id
            
        return False
