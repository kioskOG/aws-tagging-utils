from typing import Dict, Any
from src.authorization.roles import Role, UserIdentity
from src.authorization.ownership import OwnershipResolver

class AuthorizationPolicy:
    """
    Abstract RBAC domain logic that can be consumed by a future API.
    """
    @staticmethod
    def can_modify_tags(identity: UserIdentity, resource_tags: Dict[str, str]) -> bool:
        if Role.PLATFORM_ADMIN in identity.roles or Role.TAG_OPERATOR in identity.roles:
            return True
            
        if Role.APP_OWNER in identity.roles and OwnershipResolver.is_owner(identity, resource_tags):
            return True
            
        return False

    @staticmethod
    def can_write_any_tags(identity: UserIdentity) -> bool:
        """Holds a role that may write tags on at least some resources."""
        return any(r in identity.roles for r in (Role.PLATFORM_ADMIN, Role.TAG_OPERATOR, Role.APP_OWNER))

    @staticmethod
    def can_write_all_resources(identity: UserIdentity) -> bool:
        return Role.PLATFORM_ADMIN in identity.roles or Role.TAG_OPERATOR in identity.roles

    @staticmethod
    def can_view_finops(identity: UserIdentity) -> bool:
        return Role.FINOPS in identity.roles or Role.PLATFORM_ADMIN in identity.roles
        
    @staticmethod
    def can_manage_protected_tags(identity: UserIdentity) -> bool:
        return Role.SECURITY_ADMIN in identity.roles or Role.PLATFORM_ADMIN in identity.roles

    @staticmethod
    def can_manage_exemptions(identity: UserIdentity) -> bool:
        return Role.SECURITY_ADMIN in identity.roles or Role.PLATFORM_ADMIN in identity.roles
