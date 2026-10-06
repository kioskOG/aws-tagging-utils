from functools import wraps
from typing import Callable, Any
from flask import request, g
from src.auth.alb_oidc import get_alb_identity
from src.errors import APIError
import logging

logger = logging.getLogger(__name__)

def require_auth(f: Callable) -> Callable:
    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        identity = get_alb_identity(dict(request.headers))
        if not identity:
            raise APIError("Unauthorized. ALB OIDC identity required.", status_code=401, error_code="UNAUTHORIZED")
        g.identity = identity
        return f(*args, **kwargs)
    return decorated_function

def require_permission(check_fn: Callable) -> Callable:
    """
    check_fn should be a function that takes the identity (and optionally the payload/tags) 
    and returns a boolean.
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args: Any, **kwargs: Any) -> Any:
            identity = get_alb_identity(dict(request.headers))
            if not identity:
                raise APIError("Unauthorized. ALB OIDC identity required.", status_code=401, error_code="UNAUTHORIZED")
            
            g.identity = identity
            
            # Simple permission checks that don't need request payloads
            if not check_fn(identity):
                raise APIError("Forbidden. Insufficient permissions.", status_code=403, error_code="FORBIDDEN")
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# For endpoints like /api/write where payload is needed
def require_tag_modify_permission() -> Callable:
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args: Any, **kwargs: Any) -> Any:
            identity = get_alb_identity(dict(request.headers))
            if not identity:
                raise APIError("Unauthorized. ALB OIDC identity required.", status_code=401, error_code="UNAUTHORIZED")
            
            g.identity = identity
            
            payload = request.get_json(force=True, silent=True) or {}
            tags = payload.get("tags", {})
            
            from src.authorization.policy import AuthorizationPolicy
            if not AuthorizationPolicy.can_modify_tags(identity, tags):
                raise APIError("Forbidden. Insufficient permissions to modify these tags.", status_code=403, error_code="FORBIDDEN")
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator
