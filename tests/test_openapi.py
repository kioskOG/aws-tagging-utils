import pytest
from web.app import app
import json

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_openapi_json(client):
    response = client.get('/api/openapi.json')
    assert response.status_code == 200
    
    # Prove valid JSON is returned
    data = response.get_json()
    assert data is not None
    
    # Prove spec declares OpenAPI 3.x
    assert data.get('openapi', '').startswith('3.')
    
    # Prove documented routes correspond to actual registered API routes
    registered_routes = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint not in ('static', 'index'):
            registered_routes.add(rule.rule)
            
    documented_routes = set(data.get('paths', {}).keys())
    
    # Every documented route must exist in the app
    for doc_route in documented_routes:
        assert doc_route in registered_routes, f"Documented route {doc_route} not in actual app routes"

def test_api_docs_reachable(client):
    response = client.get('/api/docs')
    assert response.status_code == 200
    assert b"SwaggerUIBundle" in response.data

def test_existing_apis_unchanged(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    
def test_existing_error_behavior(client):
    response = client.get('/api/security')
    assert response.status_code == 501
    
    data = response.get_json()
    assert "error_code" in data
    assert "message" in data
    assert "request_id" in data
