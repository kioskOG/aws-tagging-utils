# FULL APPLICATION AUDIT

## 1. Repository Discovery & Architecture
### Components:
- **Web UI & API**: Flask application (`web/app.py`).
- **Worker**: SQS Polling Worker (`src/worker.py`).
- **Core Governance Engine**: `src/governance/`, handles exemptions, remediation, validation.
- **Auth**: ALB OIDC & RBAC (`src/auth/`, `src/authorization/`).
- **Persistence**: Hybrid (DynamoDB for Exemptions & Actions in `state.py`, SQLite for Audit/Cache in `db.py`).
- **Infra/Deploy**: Minimal (`deploy/stacksets/`), MISSING Terraform/CDK for core infrastructure.

### Actual Architecture Map
```text
Users
  ↓
ALB / OIDC (Simulated in local_dev, expected in production)
  ↓
Flask API/UI
  ↓
Authorization/RBAC
  ↓
Governance services (Remediation / Exemption / Validation)
  ↓
State (SQLite for Audit, DynamoDB for Actions/Exemptions)
  ↓
AWS APIs (ResourceGroupsTaggingAPI, EC2, CE)

--- Async Remediation ---
EventBridge Scheduler (Not implemented in IaC)
  ↓
SQS Standard (Not implemented in IaC)
  ↓
ECS Fargate Worker (src/worker.py)
  ↓
Governance / Remediation Engine
  ↓
DynamoDB / AWS APIs
```

## 2. Phase Implementation Audit

### Phase 1: Production Foundations
- **SQLite persistence**: PARTIAL. Implemented with WAL, but inherently ephemeral in ECS Fargate without EFS.
- **Cache behavior**: PARTIAL. Mismatched with multi-task deployment (caches are localized per container).
- **Docker**: PASS. Runs as non-root, cleanly separated.
- **OpenAPI**: PASS. Handled correctly.
- **CI/CD**: MISSING. No `.github/workflows` or GitLab CI configuration.

### Phase 2A: Authentication / RBAC
- **ALB OIDC**: PARTIAL. Verification works (ES256, signer), but `_get_alb_public_key` contains a logic flaw enabling SSRF.
- **Authorization**: PASS. Roles and decorators properly enforce constraints.

### Phase 2B: Exemptions
- **Scope & Expiry**: PASS. Correct precedence applied (resource_id > environment > resource_type > account).
- **DynamoDB Persistence**: PASS.
- **Audit**: PASS.

### Phase 2C: Remediation
- **State Machine**: PASS. Enforces strict transitions.
- **Idempotency**: PASS. Conditional writes and explicit keys operate safely.
- **Tag Mutation safety**: PASS.

### Phase 2D-1 & 2D-2: Async Worker
- **SQS Polling**: PASS. Worker processes messages natively.
- **Lease Ownership**: PASS. Uses `DynamoDBStateStore` safely.
- **Poison Messages**: FAIL. Worker skips deletion of malformed messages assuming DLQ exists, but IaC to create SQS+DLQ is entirely missing.

## 3. Infrastructure and External Dependencies
- **Terraform/CloudFormation**: MISSING. Core resources (DynamoDB, ECS, SQS, ALB, Security Groups) are not defined in IaC.
- **Cross-Account Support**: PARTIAL / MOCKED. `src/multi_account.py` provides an `assume_role` stub, but `src/tag_report.py` hard-mocks the organization scan.
- **Security & CI/CD**: Endpoints like `/api/security`, `/api/organization`, `/api/cicd` are mocked (HTTP 501 Not Implemented).

## 4. DynamoDB and SQLite Audit
- **DynamoDB**: The `list_active_exemptions` performs a full table `Scan`. This is a SCALABILITY RISK.
- **SQLite**: Local ephemeral state (`/app/data/app.db`) is incompatible with the multi-task nature of the ECS worker/web separation.
