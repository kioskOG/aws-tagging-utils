from typing import Dict, Any, List

class Role:
    VIEWER = "Viewer"
    TAG_OPERATOR = "TagOperator"
    APP_OWNER = "ApplicationOwner"
    FINOPS = "FinOps"
    SECURITY_ADMIN = "SecurityAdmin"
    PLATFORM_ADMIN = "PlatformAdmin"

class UserIdentity:
    def __init__(self, user_id: str, roles: List[str]):
        self.user_id = user_id
        self.roles = roles
