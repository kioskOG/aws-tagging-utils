import pytest
from flask import Flask, jsonify, request
from src.errors import register_error_handlers
from src.auth.decorators import require_auth, require_permission, require_tag_modify_permission
from src.authorization.roles import UserIdentity, Role
import os
import jwt
import time
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

app = Flask(__name__)
register_error_handlers(app)

@app.get("/test/auth")
@require_auth
def endpoint_auth():
    return jsonify({"status": "ok"})

@app.get("/test/finops")
@require_permission(lambda identity: Role.FINOPS in identity.roles)
def endpoint_finops():
    return jsonify({"status": "ok"})

@app.post("/test/write")
@require_tag_modify_permission()
def endpoint_write():
    return jsonify({"status": "ok"})

# ES256 Key Generation for Tests
test_private_key = ec.generate_private_key(ec.SECP256R1())
test_public_key = test_private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
).decode('utf-8')

wrong_private_key = ec.generate_private_key(ec.SECP256R1())

def generate_es256_token(payload, headers, use_wrong_key=False):
    key = wrong_private_key if use_wrong_key else test_private_key
    return jwt.encode(payload, key, algorithm="ES256", headers=headers)

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local_dev")
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_unauthenticated(client, monkeypatch):
    """No identity -> 401"""
    monkeypatch.delenv("DEV_AUTH_USER", raising=False)
    resp = client.get("/test/auth")
    assert resp.status_code == 401

def test_authenticated_success(client, monkeypatch):
    monkeypatch.setenv("DEV_AUTH_USER", "user-1:Viewer")
    resp = client.get("/test/auth")
    assert resp.status_code == 200

def test_authenticated_without_role(client, monkeypatch):
    monkeypatch.setenv("DEV_AUTH_USER", "user-1:Viewer")
    resp = client.get("/test/finops")
    assert resp.status_code == 403

def test_authenticated_with_role(client, monkeypatch):
    monkeypatch.setenv("DEV_AUTH_USER", "user-1:FinOps")
    resp = client.get("/test/finops")
    assert resp.status_code == 200

def test_api_write_unauthorized(client, monkeypatch):
    monkeypatch.setenv("DEV_AUTH_USER", "user-1:Viewer")
    resp = client.post("/test/write", json={"tags": {"Owner": "team-a"}})
    assert resp.status_code == 403

def test_api_write_authorized(client, monkeypatch):
    monkeypatch.setenv("DEV_AUTH_USER", "user-1:TagOperator")
    resp = client.post("/test/write", json={"tags": {"Owner": "team-a"}})
    assert resp.status_code == 200

def test_alb_mode_malformed_token(client, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "alb_oidc")
    resp = client.get("/test/auth", headers={"x-amzn-oidc-data": "spoofed-jwt"})
    assert resp.status_code == 401

def test_alb_mode_spoofed_identity_no_data(client, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "alb_oidc")
    resp = client.get("/test/auth", headers={"X-User": "platform-admin", "X-Role": "PlatformAdmin", "X-Groups": "admins"})
    assert resp.status_code == 401

def test_alb_mode_identity_header_only(client, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "alb_oidc")
    resp = client.get("/test/auth", headers={"x-amzn-oidc-identity": "user-1"})
    assert resp.status_code == 401

def test_alb_mode_rs256_rejected(client, monkeypatch):
    """RS256 token -> 401"""
    monkeypatch.setenv("AUTH_MODE", "alb_oidc")
    monkeypatch.setenv("AUTH_ALB_ARN", "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123")
    
    import src.auth.alb_oidc as alb_oidc
    monkeypatch.setattr(alb_oidc, "_get_alb_public_key", lambda kid: "dummy-key")
    
    # Generate RS256 token (not ES256)
    from cryptography.hazmat.primitives.asymmetric import rsa
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode({"sub": "user-1", "exp": int(time.time())+1000}, rsa_key, algorithm="RS256", headers={"kid": "test-kid", "signer": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123"})
    
    resp = client.get("/test/auth", headers={"x-amzn-oidc-data": token})
    assert resp.status_code == 401
    
def test_alb_mode_hs256_rejected(client, monkeypatch):
    """HS256 token -> 401"""
    monkeypatch.setenv("AUTH_MODE", "alb_oidc")
    monkeypatch.setenv("AUTH_ALB_ARN", "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123")
    
    token = jwt.encode({"sub": "user-1", "exp": int(time.time())+1000}, "secret", algorithm="HS256", headers={"kid": "test-kid", "signer": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123"})
    resp = client.get("/test/auth", headers={"x-amzn-oidc-data": token})
    assert resp.status_code == 401

def test_alb_mode_wrong_signer(client, monkeypatch):
    """ES256 token with wrong signer -> 401"""
    monkeypatch.setenv("AUTH_MODE", "alb_oidc")
    monkeypatch.setenv("AUTH_ALB_ARN", "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123")
    
    import src.auth.alb_oidc as alb_oidc
    monkeypatch.setattr(alb_oidc, "_get_alb_public_key", lambda kid: test_public_key)
    
    headers = {"kid": "test-kid", "signer": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/WRONG-ALB/999"}
    payload = {"sub": "user-1", "exp": int(time.time()) + 1000}
    token = generate_es256_token(payload, headers)
    
    resp = client.get("/test/auth", headers={"x-amzn-oidc-data": token})
    assert resp.status_code == 401

def test_alb_mode_missing_signer(client, monkeypatch):
    """ES256 token with missing signer -> 401"""
    monkeypatch.setenv("AUTH_MODE", "alb_oidc")
    monkeypatch.setenv("AUTH_ALB_ARN", "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123")
    
    import src.auth.alb_oidc as alb_oidc
    monkeypatch.setattr(alb_oidc, "_get_alb_public_key", lambda kid: test_public_key)
    
    headers = {"kid": "test-kid"} # No signer
    payload = {"sub": "user-1", "exp": int(time.time()) + 1000}
    token = generate_es256_token(payload, headers)
    
    resp = client.get("/test/auth", headers={"x-amzn-oidc-data": token})
    assert resp.status_code == 401

def test_alb_mode_wrong_key(client, monkeypatch):
    """ES256 token signed by wrong key -> 401"""
    monkeypatch.setenv("AUTH_MODE", "alb_oidc")
    monkeypatch.setenv("AUTH_ALB_ARN", "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123")
    
    import src.auth.alb_oidc as alb_oidc
    monkeypatch.setattr(alb_oidc, "_get_alb_public_key", lambda kid: test_public_key)
    
    headers = {"kid": "test-kid", "signer": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123"}
    payload = {"sub": "user-1", "exp": int(time.time()) + 1000}
    token = generate_es256_token(payload, headers, use_wrong_key=True)
    
    resp = client.get("/test/auth", headers={"x-amzn-oidc-data": token})
    assert resp.status_code == 401

def test_alb_mode_expired_token(client, monkeypatch):
    """Expired ES256 token -> 401"""
    monkeypatch.setenv("AUTH_MODE", "alb_oidc")
    monkeypatch.setenv("AUTH_ALB_ARN", "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123")
    
    import src.auth.alb_oidc as alb_oidc
    monkeypatch.setattr(alb_oidc, "_get_alb_public_key", lambda kid: test_public_key)
    
    headers = {"kid": "test-kid", "signer": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123"}
    payload = {"sub": "user-1", "exp": int(time.time()) - 1000} # Expired
    token = generate_es256_token(payload, headers)
    
    resp = client.get("/test/auth", headers={"x-amzn-oidc-data": token})
    assert resp.status_code == 401

def test_alb_mode_missing_sub(client, monkeypatch):
    """ES256 token with missing sub -> 401"""
    monkeypatch.setenv("AUTH_MODE", "alb_oidc")
    monkeypatch.setenv("AUTH_ALB_ARN", "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123")
    
    import src.auth.alb_oidc as alb_oidc
    monkeypatch.setattr(alb_oidc, "_get_alb_public_key", lambda kid: test_public_key)
    
    headers = {"kid": "test-kid", "signer": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123"}
    payload = {"exp": int(time.time()) + 1000} # Missing sub
    token = generate_es256_token(payload, headers)
    
    resp = client.get("/test/auth", headers={"x-amzn-oidc-data": token})
    assert resp.status_code == 401

def test_alb_mode_valid_identity_es256(client, monkeypatch):
    """Valid ES256 token + correct ALB signer -> authenticated"""
    monkeypatch.setenv("AUTH_MODE", "alb_oidc")
    monkeypatch.setenv("AUTH_ALB_ARN", "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123")
    monkeypatch.setenv("AUTH_ROLE_MAPPING", '{"engineers": "TagOperator"}')
    
    import src.auth.alb_oidc as alb_oidc
    monkeypatch.setattr(alb_oidc, "_get_alb_public_key", lambda kid: test_public_key)
    
    headers = {"kid": "test-kid", "signer": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/my-alb/123"}
    payload = {"sub": "user-123", "groups": ["engineers"], "exp": int(time.time()) + 1000}
    token = generate_es256_token(payload, headers)
    
    resp = client.post("/test/write", headers={"x-amzn-oidc-data": token}, json={"tags": {"Owner": "me"}})
    assert resp.status_code == 200

def test_production_default_auth_mode(client, monkeypatch):
    """Production/default AUTH_MODE cannot silently enter local_dev"""
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("DEV_AUTH_USER", "user-1:PlatformAdmin")
    # Should default to alb_oidc and reject
    resp = client.get("/test/auth")
    assert resp.status_code == 401
