import json
import logging
import os
import urllib.request
import threading
import jwt
from typing import Dict, Any, Optional
from src.authorization.roles import UserIdentity
from src.auth.identity import get_role_mapping
from src.errors import APIError

logger = logging.getLogger(__name__)

import time

# Cache for ALB public keys: kid -> (public_key, expires_at)
_KEY_CACHE: Dict[str, tuple[str, float]] = {}
_KEY_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 3600  # 1 hour cache TTL

# By default, use the region of the deployment, fallback to us-east-1
ALB_REGION = os.environ.get("AUTH_AWS_REGION", os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")))

def _get_alb_public_key(kid: str) -> str:
    """Fetch the ALB public key by key ID (kid), with TTL caching to prevent unbounded network calls."""
    now = time.time()
    with _KEY_CACHE_LOCK:
        if kid in _KEY_CACHE:
            pub_key, expires_at = _KEY_CACHE[kid]
            if now < expires_at:
                return pub_key
            
    # Validate kid to prevent SSRF or directory traversal (should be alphanumeric/uuid-like)
    if not kid.isalnum() and "-" not in kid:
        raise APIError("Invalid Key ID format in ALB JWT", status_code=401, error_code="UNAUTHORIZED")
        
    url = f"https://public-keys.auth.elb.{ALB_REGION}.amazonaws.com/{kid}"
    logger.info(f"Fetching ALB OIDC public key from {url}")
    
    try:
        # Prevent hanging indefinitely if AWS metadata/endpoint is unreachable
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5.0) as response:
            public_key = response.read().decode('utf-8')
            with _KEY_CACHE_LOCK:
                _KEY_CACHE[kid] = (public_key, time.time() + _CACHE_TTL_SECONDS)
            return public_key
    except Exception as e:
        logger.error(f"Failed to fetch ALB public key: {e}")
        raise APIError("Failed to verify ALB identity due to key retrieval error.", status_code=401, error_code="UNAUTHORIZED")

def _verify_alb_signature(jwt_token: str) -> Dict[str, Any]:
    """
    Cryptographically verify the ALB JWT signature using PyJWT.
    Returns the verified payload claims.
    """
    try:
        # Get unverified header to extract kid, alg, and signer
        header = jwt.get_unverified_header(jwt_token)
        
        # Verify algorithm
        alg = header.get("alg")
        if alg != "ES256":
            raise APIError(f"Invalid ALB JWT algorithm: {alg}. Expected ES256.", status_code=401, error_code="UNAUTHORIZED")
            
        kid = header.get("kid")
        if not kid:
            raise APIError("Missing 'kid' in ALB JWT header", status_code=401, error_code="UNAUTHORIZED")
            
        # Verify signer matches the configured ALB ARN
        signer = header.get("signer")
        if not signer:
            raise APIError("Missing 'signer' in ALB JWT header", status_code=401, error_code="UNAUTHORIZED")
            
        configured_alb_arn = os.environ.get("AUTH_ALB_ARN")
        if not configured_alb_arn:
            logger.error("AUTH_ALB_ARN is not configured. Cannot verify ALB signature.")
            raise APIError("Server misconfiguration: ALB ARN not set.", status_code=500, error_code="AUTH_MISCONFIGURED")
            
        if signer != configured_alb_arn:
            raise APIError("Untrusted ALB signer ARN", status_code=401, error_code="UNAUTHORIZED")
            
        public_key = _get_alb_public_key(kid)
        
        # Verify token using explicit ES256 algorithm.
        payload = jwt.decode(
            jwt_token, 
            public_key, 
            algorithms=["ES256"], 
            options={"verify_aud": False, "require": ["exp", "sub"]}
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("ALB JWT signature has expired")
        raise APIError("Expired ALB JWT", status_code=401, error_code="UNAUTHORIZED")
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid ALB JWT signature/token: {e}")
        raise APIError("Invalid ALB JWT signature", status_code=401, error_code="UNAUTHORIZED")
    except APIError:
        raise
    except Exception as e:
        logger.error(f"Unexpected error verifying ALB JWT: {e}")
        raise APIError("Failed to verify ALB identity", status_code=401, error_code="UNAUTHORIZED")

def get_alb_identity(headers: Dict[str, str]) -> Optional[UserIdentity]:
    auth_mode = os.environ.get("AUTH_MODE", "alb_oidc")
    
    if auth_mode == "local_dev":
        # Explicit opt-in development mode
        dev_identity = os.environ.get("DEV_AUTH_USER")
        if not dev_identity:
            return None
        # format: user1:Role1,Role2
        parts = dev_identity.split(":", 1)
        user_id = parts[0]
        roles = parts[1].split(",") if len(parts) > 1 else []
        return UserIdentity(user_id=user_id, roles=roles)
        
    if auth_mode != "alb_oidc":
        logger.error(f"Unknown AUTH_MODE configuration: {auth_mode}")
        return None

    # ALB OIDC Mode
    headers_lower = {k.lower(): v for k, v in headers.items()}
    
    jwt_token = headers_lower.get("x-amzn-oidc-data")
    if not jwt_token:
        # Missing identity token entirely. Fail closed.
        return None
        
    try:
        # 1. Verify cryptographic signature and extract trusted claims
        payload = _verify_alb_signature(jwt_token)
        
        # 2. Extract identity subject
        user_id = payload.get("sub")
        if not user_id:
            # We enforce require=["sub"] in jwt.decode, so this shouldn't happen, but safe fallback logic just in case
            raise APIError("Missing 'sub' claim in verified ALB JWT", status_code=401, error_code="UNAUTHORIZED")
            
        # 3. Map groups to roles
        # Note: If corporate OIDC maps groups into another claim name (e.g. 'cognito:groups'), it could be retrieved here.
        # By default, we look for 'groups' as standard mapping.
        groups = payload.get("groups", [])
        if isinstance(groups, str):
            groups = [groups]
            
        role_mapping = get_role_mapping()
        roles = []
        for group in groups:
            if group in role_mapping:
                roles.append(role_mapping[group])
                
        return UserIdentity(user_id=user_id, roles=roles)
        
    except APIError:
        raise
    except Exception as e:
        logger.error(f"Failed to parse ALB OIDC identity: {e}")
        return None
