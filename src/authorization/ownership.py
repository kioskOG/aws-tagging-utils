from typing import Dict, Any
from src.authorization.roles import Role, UserIdentity

class OwnershipResolver:
    """
    Evaluates resource ownership based on authoritative governance tags.
    """
    @staticmethod
    def is_owner(identity: UserIdentity, resource_tags: Dict[str, str]) -> bool:
        """Same rules as the owner view: OWNER_MATCH_TAGS (default Owner) and Application,
        matching the user id or email exactly or as the suffix of an SSO session name."""
        from src.insights import owner_identifiers, owner_matches
        ids = owner_identifiers(identity)
        if owner_matches(ids, resource_tags):
            return True
        app = str(resource_tags.get("Application", "")).strip().lower()
        return bool(app) and app in ids
