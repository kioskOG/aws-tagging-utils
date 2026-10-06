# SECURITY FINDINGS

## 1. SSRF in ALB OIDC Public Key Retrieval
**ID:** SEC-001
**Severity:** HIGH
**Category:** Authentication / Input Validation
**Location:** `src/auth/alb_oidc.py:34-35`
**Evidence:** 
```python
if not kid.isalnum() and "-" not in kid:
    raise APIError("Invalid Key ID format...")
```
**Impact:** A maliciously crafted `kid` in an unverified JWT (e.g., `foo-bar/../`) bypassing the check because `"-" not in kid` returns `False`, collapsing the `and` condition to `False`. This allows directory traversal and Server-Side Request Forgery (SSRF) against AWS endpoints.
**Recommendation:** Change the logic to explicitly use a strict Regex match (e.g., `^[a-zA-Z0-9\-]+$`).
**Blocks Production:** YES

## 2. Missing ExternalId in Cross-Account Trust Policy
**ID:** SEC-002
**Severity:** MEDIUM
**Category:** IAM / Cross-Account
**Location:** `deploy/stacksets/governance-role.yaml:12`
**Evidence:** The `AssumeRolePolicyDocument` allows root of the Management Account to assume the role without enforcing an `ExternalId`.
**Impact:** While contained within the organization's Management Account, it breaks best practices for strict cross-account role assumption protection.
**Recommendation:** Enforce `sts:ExternalId` in the condition block matching the central account's unique identifier.
**Blocks Production:** NO

## 3. SQLite Ephemeral Disk Isolation
**ID:** SEC-003
**Severity:** MEDIUM
**Category:** Data Durability / Architecture
**Location:** `Dockerfile:45` & `src/db.py`
**Evidence:** Audit logs, FinOps caching, and historical scan data are persisted in `/app/data/app.db` (SQLite). In ECS Fargate, this disk is ephemeral and isolated per container task.
**Impact:** Web containers and Worker containers cannot share audit logs or caches. A horizontal scale-out will fragment data silently.
**Recommendation:** Migrate Audit Logging and Caches to DynamoDB/Redis, or utilize EFS (Elastic File System) if SQLite is strictly required.
**Blocks Production:** YES

## 4. Poison Message Indefinite Loop (Missing DLQ)
**ID:** SEC-004
**Severity:** MEDIUM
**Category:** Availability / Denial of Service
**Location:** `src/worker.py:181` & Missing IaC
**Evidence:** The worker explicitly ignores malformed SQS messages (`# We do NOT delete malformed messages. Let DLQ handle them.`), but there is no IaC to define the DLQ or queue retention.
**Impact:** A single malformed message will be continuously re-delivered to the worker until the default queue retention expires, consuming worker cycles and preventing valid remediation tasks.
**Recommendation:** Ensure Terraform/IaC defines a Dead Letter Queue (DLQ) with a low `maxReceiveCount` (e.g., 3-5).
**Blocks Production:** YES
