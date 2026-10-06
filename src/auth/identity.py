from typing import Dict, Any, List, Optional
import os
import json
import logging
from src.authorization.roles import UserIdentity, Role
from src.errors import APIError

logger = logging.getLogger(__name__)

def get_role_mapping() -> Dict[str, str]:
    """Load SSO Group -> App Role mapping from environment."""
    mapping_str = os.environ.get("AUTH_ROLE_MAPPING", "{}")
    try:
        return json.loads(mapping_str)
    except Exception as e:
        logger.error(f"Failed to parse AUTH_ROLE_MAPPING: {e}")
        return {}
