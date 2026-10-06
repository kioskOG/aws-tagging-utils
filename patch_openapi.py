import yaml

with open("config/openapi.yaml", "r") as f:
    spec = yaml.safe_load(f)

# Add responses to components
if 'responses' not in spec['components']:
    spec['components']['responses'] = {}
    
spec['components']['responses']['UnauthorizedError'] = {
    'description': 'Unauthorized (No trusted ALB identity)',
    'content': {'application/json': {'schema': {'$ref': '#/components/schemas/APIError'}}}
}
spec['components']['responses']['ForbiddenError'] = {
    'description': 'Forbidden (Insufficient RBAC permissions)',
    'content': {'application/json': {'schema': {'$ref': '#/components/schemas/APIError'}}}
}

# Add to protected APIs
protected_paths = [
    '/api/read', '/api/write', '/api/gov', '/api/report', '/api/sync',
    '/api/compliance/refresh', '/api/compliance/status', '/api/compliance/summary',
    '/api/dashboard', '/api/schema', '/api/compliance', '/api/finops',
    '/api/finops/refresh', '/api/finops/status', '/api/security', '/api/organization',
    '/api/remediation', '/api/exemptions', '/api/audit', '/api/cicd'
]

for path, operations in spec['paths'].items():
    if path in protected_paths:
        for op in operations.values():
            if 'responses' not in op:
                op['responses'] = {}
            op['responses']['401'] = {'$ref': '#/components/responses/UnauthorizedError'}
            op['responses']['403'] = {'$ref': '#/components/responses/ForbiddenError'}

with open("config/openapi.yaml", "w") as f:
    yaml.dump(spec, f, sort_keys=False)
