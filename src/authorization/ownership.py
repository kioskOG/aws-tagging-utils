from typing import Dict
from src.authorization.roles import UserIdentity

class OwnershipResolver:
    """
    Evaluates resource ownership based on authoritative governance tags.
    """
    @staticmethod
    def is_owner(identity: UserIdentity, resource_tags: Dict[str, str]) -> bool:
        """Same rule as the owner view (src.insights.is_owned)."""
        from src.insights import owner_identifiers, is_owned
        return is_owned(owner_identifiers(identity), resource_tags)
