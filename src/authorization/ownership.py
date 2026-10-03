from typing import Dict, Any
from src.authorization.roles import Role, UserIdentity

class OwnershipResolver:
    """
    Evaluates resource ownership based on authoritative governance tags.
    """
    @staticmethod
    def is_owner(identity: UserIdentity, resource_tags: Dict[str, str]) -> bool:
        # Simple mock: if user_id matches the 'Owner' or 'Application' tag
        owner_tag = resource_tags.get("Owner", "")
        app_tag = resource_tags.get("Application", "")
        
        if identity.user_id == owner_tag or identity.user_id == app_tag:
            return True
        return False
