# Run & Test Guide — aws-tagging-utils (all features)

A step-by-step guide to running every feature of this project locally (or in Docker), the environment variables each one uses, and how to test it.

> Verified on branch `advance-1` (v0.4.0): `pytest -m "not integration"` → **282 passed**; the app runs under gunicorn, and the bulk fix → apply → undo flow was driven end to end in a real browser against an in-memory tagging API.

---

## 0. What's in the box

| # | Feature | Entry point | Needs AWS? | Extra AWS resources |
|---|---------|-------------|-----------|---------------------|
| 1 | Web UI + REST API | `web/app.py` (Flask, port 5050) | Partly | — |
| 2 | **TagRead**: find resources by type and tag | `POST /api/read`, MCP `read_tags` | Yes | — |
| 3 | **TagWriter**: add or update tags (+ audit log) | `POST /api/write`, MCP `write_tags` | Yes | — |
| 4 | **TagOnCreate / Governance scan**: auto-tag `Owner` | `POST /api/gov`, MCP `apply_governance` | Yes | CloudTrail (for event mode) |
| 5 | **TagReport / Compliance dashboard** | `POST /api/report`, `/api/compliance/*`, `/api/dashboard` | Yes | optional S3 bucket |
| 6 | **TagSync**: copy VPC tags to its children | `POST /api/sync`, MCP `sync_tags` | Yes | a VPC |
| 7 | **Governance engine** (schema, normalization) | `config/tag-schema.yaml`, `GET /api/schema` | No | — |
| 8 | **CI/CD CLI validator** (IaC tag lint, SARIF) | `./aws-tagging-utils validate` | No | — |
| 9 | **Config Rule / SCP generators** | `./aws-tagging-utils generate config\|scp` | No | — |
| 10 | **Exemptions** | `/api/exemptions` | Yes | DynamoDB table |
| 11 | **Remediation** (sync, idempotent) | `/api/remediation` | Yes | DynamoDB table, optional SNS |
| 12 | **Remediation worker** (async) | `python -m src.worker` | Yes | SQS queue + DynamoDB |
| 13 | **Enforcement Lambda** (EventBridge) | `src/enforcement.py:lambda_handler` | Yes | EventBridge, DynamoDB |
| 14 | **FinOps** (Cost Explorer) | `/api/finops*`, `./aws-tagging-utils finops report` | Yes | Cost Explorer enabled |
| 15 | **Audit log** (SQLite) | `GET /api/audit` | No | — |
| 16 | **Auth & RBAC** (local dev / ALB OIDC) | every `/api/*` route | No / ALB | ALB + IdP for prod |
| 17 | **MCP server** (for Claude / AI clients) | `mcp_server.py` | Yes | — |
| 18 | **Multi-account** (StackSets) | `deploy/stacksets/*.yaml` | Yes | AWS Organizations |
| 19 | OpenAPI docs | `GET /api/docs` | No | — |
| 20 | **Metrics** (Prometheus + CloudWatch EMF) | `GET /metrics` | No | Prometheus / CloudWatch |
| 21 | **Compliance trend & CSV export** | `/api/compliance/history`, `/api/compliance/export.csv` | No | — |
| 22 | Health / readiness probes | `GET /health`, `GET /ready` | No | — |
| 23 | **Full inventory** (Resource Explorer) + coverage gaps | `INVENTORY_SOURCE`, `/api/inventory/coverage` | Yes | Resource Explorer index |
| 24 | **Bulk fix** with preview, undo & value suggestions | `/api/bulk/*`, `/api/changesets*`, `/api/suggestions` | Yes | — |
| 25 | **Multi-account scans** + **leaderboards** | `COMPLIANCE_ACCOUNTS`, `/api/leaderboard`, `/api/organization` | Yes | Org + StackSet role |
| 26 | **My Resources** (owner view) | `/api/me/resources` | Partly | Cost Explorer for cost |
| 27 | **Tag propagation** checks & fixes (10 parent types) | `/api/propagation*`, `/api/sync` | Yes | — |

---

## 1. Prerequisites

- Python **3.10+** (Docker image uses 3.12)
- [`uv`](https://docs.astral.sh/uv/) (recommended, since `uv.lock` exists) **or** `pip`
- Docker + Docker Compose (optional)
- AWS CLI v2 with credentials for a **non-production** account (see `aws_setup.md` for IAM)
- `curl` and `jq` for testing

Check that the AWS identity works:

```bash
aws sts get-caller-identity
```

---

## 2. Install

```bash
git clone <repo> && cd aws-tagging-utils

# Option A — uv (recommended)
uv sync --extra dev

# Option B — pip
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"
```

> Run the commands below with `uv run <cmd>` (option A) or inside the activated venv (option B).

---

## 3. Configure `.env`

```bash
cp .env.example .env
```

### Roles for `DEV_AUTH_USER`

Role names are **case-sensitive** (`admin` is not a role; it gives 403 on every write). `.env.example` now ships `admin:PlatformAdmin`. Roles from `src/authorization/roles.py`:

| Role | Can do |
|------|--------|
| `Viewer` | Read-only endpoints (any authenticated user) |
| `TagOperator` | Write or modify tags, remediation |
| `ApplicationOwner` | Modify tags **only** on resources they own (Owner tag matches) |
| `FinOps` | `/api/finops*` |
| `SecurityAdmin` | Create or revoke exemptions, protected tags |
| `PlatformAdmin` | Everything |

```dotenv
DEV_AUTH_USER=admin:PlatformAdmin
```

### Minimal local `.env` (running outside Docker)

```dotenv
AWS_DEFAULT_REGION=ap-south-1
AUTH_MODE=local_dev
DEV_AUTH_USER=admin:PlatformAdmin
SQLITE_DB_PATH=./app.db            # /app/data/app.db is for Docker only
GOVERNANCE_SCHEMA_PATH=config/tag-schema.yaml
MANDATORY_TAGS=Owner,Environment
LOG_FORMAT=text
LOG_LEVEL=INFO
```

Load it in your shell before running anything locally (Flask does not read `.env` automatically unless `python-dotenv` is installed):

```bash
set -a; source .env; set +a
```

---

## 4. Environment variable reference

### 4.1 Core / AWS

| Variable | Default | Used by | Purpose |
|----------|---------|---------|---------|
| `AWS_DEFAULT_REGION` | `us-east-2` (falls back to `AWS_REGION`) | everything | Default region for all boto3 clients |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` | — | boto3 | Only when not using a profile or instance role |
| `AWS_PROFILE` | — | boto3 | Pick a named profile from `~/.aws` |
| `BOTO_MAX_RETRIES` | `5` | `src/clients.py` | boto3 retry attempts |
| `BOTO_RETRY_MODE` | `adaptive` | `src/clients.py` | `standard` / `adaptive` / `legacy` |
| `LOG_LEVEL` | `INFO` | logging | `DEBUG`, `INFO`, `WARNING`… |
| `LOG_FORMAT` | `json` | logging | `json` (CloudWatch) or `text` (local) |

### 4.2 Auth & RBAC

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUTH_MODE` | **`alb_oidc`** (in code) | `local_dev` = trust `DEV_AUTH_USER`; `alb_oidc` = verify the ALB `x-amzn-oidc-data` JWT (ES256) |
| `DEV_AUTH_USER` | — | `user:Role1,Role2`, used only in `local_dev`. If it's unset, every request gets 401 |
| `AUTH_ALB_ARN` | — | **Required** in `alb_oidc`; the JWT `signer` must equal this ARN, otherwise 500 `AUTH_MISCONFIGURED` |
| `AUTH_AWS_REGION` | `AWS_REGION` → `AWS_DEFAULT_REGION` → `us-east-1` | Region used to fetch ALB public keys |
| `AUTH_ROLE_MAPPING` | `{}` | JSON map of IdP group to app role, e.g. `{"aws-admins":"PlatformAdmin","finance":"FinOps"}` (from the JWT `groups` claim) |

### 4.3 Governance schema

| Variable | Default | Purpose |
|----------|---------|---------|
| `GOVERNANCE_SCHEMA_PATH` | `config/tag-schema.yaml` | Tag rules: required, allowed_values, regex pattern, aliases |
| `MANDATORY_TAGS` | `Owner` | Comma list used by compliance reports when the request doesn't pass `mandatory_tags` |
| `OWNER_TAG_KEY` | `Owner` | Tag key that TagOnCreate writes |
| `GOVERNANCE_UNKNOWN_TAGS` | `warn` | `warn` / `reject` tags that are not in the schema |
| `GOVERNANCE_STRICT_MODE` | `true` | Strict schema evaluation |
| `GOVERNANCE_NORMALIZATION` | `true` | Rewrite aliases (`env` → `Environment`, etc.) |

### 4.4 State, remediation, notifications

| Variable | Default | Purpose |
|----------|---------|---------|
| `GOVERNANCE_DYNAMODB_TABLE` | `TagGovernanceState` | Stores exemptions, remediation actions and resource state. Always used for exemptions/remediation (there is no SQLite fallback) |
| `GOVERNANCE_SNS_TOPIC_ARN` | — | Optional SNS topic for violation notifications (skipped with a warning if unset) |
| `GOVERNANCE_REMEDIATION_ENABLED` | `true` | `false` = report-only: `POST /api/remediation` → 403, enforcement Lambda records `REPORT_ONLY` |
| `GOVERNANCE_GRACE_PERIOD_DAYS` | `7` | Grace period before remediation |
| `MAX_REMEDIATION_ATTEMPTS` | `3` | After this many failures, the action becomes `FAILED` / `MAX_RETRIES_EXHAUSTED` |
| `SQLITE_DB_PATH` | `./app.db` | Audit log, compliance scans, FinOps snapshots |
| `COMPLIANCE_CACHE_TTL_SECONDS` | `300` | Age at which the compliance cache counts as stale |
| `COMPLIANCE_CACHE_FILE` | `./.compliance_cache.json` | Legacy JSON cache, migrated into SQLite once |
| `COMPLIANCE_REGIONS` | `AWS_DEFAULT_REGION` | Regions for background/scheduled scans: comma list or `all` |
| `COMPLIANCE_SCAN_INTERVAL_SECONDS` | `0` (off) | Server-side scheduler: every N s, scan if the cache is stale. Keeps metrics fresh with nobody on the UI |
| `SCAN_RETENTION` | `30` | Completed scans kept in SQLite (trend chart); older ones are pruned |
| `REPORT_BUCKET` | — | Optional S3 bucket for report export |

### 4.5 Worker / SQS

| Variable | Default | Purpose |
|----------|---------|---------|
| `SQS_QUEUE_URL` | — | **Required**; the worker exits immediately without it |
| `SQS_WAIT_TIME_SECONDS` | `20` | Long-poll wait |
| `SQS_VISIBILITY_TIMEOUT_SECONDS` | `900` | Visibility timeout per message |
| `SQS_MAX_MESSAGES` | `10` | Batch size (1–10) |
| `ECS_TASK_ID` | hostname | Worker ID used in logs and leases |

### 4.6 FinOps

| Variable | Default | Purpose |
|----------|---------|---------|
| `FINOPS_ENABLED` | `true` | `false` → `/api/finops*` return 404 `FEATURE_DISABLED`, the UI hides spend |
| `FINOPS_CACHE_TTL_SECONDS` | `86400` | FinOps snapshot staleness (24h) |
| `FINOPS_PRIMARY_TAG` | first schema tag with `finops.cost_allocation` | Tag used to compute "TaggedSpend" |

### 4.7 Performance

| Variable | Default | Purpose |
|----------|---------|---------|
| `TAG_API_BATCH_SIZE` | `20` | ARNs per `TagResources` call (AWS max is 20) |
| `TAG_LOOKUP_RETRIES` | `10` | TagOnCreate retries while the new resource becomes visible |
| `TAG_LOOKUP_DELAY_SEC` | `1.5` | Delay between those retries |
| `MULTI_ACCOUNT_ROLE_NAME` | `AWSOrganizationTagGovernanceRole` | Role assumed in member accounts |

### 4.8 Observability, MCP & app

| Variable | Default | Purpose |
|----------|---------|---------|
| `OBSERVABILITY_ENABLED` | `true` | Serve Prometheus metrics at `GET /metrics` |
| `METRICS_AUTH_TOKEN` | — | If set, `/metrics` requires `Authorization: Bearer <token>` |
| `METRICS_EMF_ENABLED` | `false` | Emit CloudWatch EMF metric lines on stdout (Lambda/ECS) |
| `METRICS_NAMESPACE` | `TagGovernance` | CloudWatch namespace for EMF |
| `MCP_READ_ONLY` | `false` | Refuse MCP write tools (`write_tags`, `apply_governance`, `sync_tags`) |
| `LOG_STREAM` | `stdout` | `stderr` for stdio protocols (set automatically by the MCP server) |
| `GUNICORN_THREADS` | `8` | Docker image: request threads (single worker by design) |
| `APP_VERSION` | `0.3.0` | Shown in the UI and `/ready` |

### 4.9 Reserved flags (accepted but not implemented)

`RBAC_ENABLED` (RBAC is **always** enforced), `FINOPS_AUTO_ACTIVATE_COST_TAGS`, `DRIFT_ENABLED`, `DRIFT_AUTO_REVERT`, `WORKER_ENABLED`, `WORKER_SHUTDOWN_TIMEOUT_SECONDS`, `GOVERNANCE_TERMINATION_ENABLED` (resources are never terminated).

---

## 5. Step 1 — Run the offline tests (no AWS needed)

```bash
uv run pytest -m "not integration" -v          # 226 tests, ~6s
uv run pytest -m "not integration" --cov=src --cov-report=term-missing
make lint typecheck                             # ruff + mypy
```

Integration tests (real AWS, use a sandbox account):

```bash
uv run pytest -m integration -v
```

---

## 6. Step 2 — Provision optional AWS resources

You only need these for features 10–14. Use a sandbox account.

```bash
REGION=ap-south-1

# DynamoDB — exemptions + remediation state (partition key "PK", String)
aws dynamodb create-table --region $REGION \
  --table-name TagGovernanceState \
  --attribute-definitions AttributeName=PK,AttributeType=S \
  --key-schema AttributeName=PK,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

# SNS — notifications (optional)
aws sns create-topic --region $REGION --name tag-governance-alerts
aws sns subscribe --region $REGION --protocol email \
  --notification-endpoint you@example.com \
  --topic-arn arn:aws:sns:$REGION:<ACCOUNT_ID>:tag-governance-alerts

# SQS — worker queue + DLQ
aws sqs create-queue --region $REGION --queue-name tag-governance-dlq
DLQ_ARN=$(aws sqs get-queue-attributes --region $REGION \
  --queue-url $(aws sqs get-queue-url --region $REGION --queue-name tag-governance-dlq --query QueueUrl --output text) \
  --attribute-names QueueArn --query Attributes.QueueArn --output text)
aws sqs create-queue --region $REGION --queue-name tag-governance-queue \
  --attributes "{\"VisibilityTimeout\":\"900\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"5\\\"}\"}"

# S3 — report export (optional)
aws s3 mb s3://<your-unique>-tag-reports --region $REGION
```

Add them to `.env`:

```dotenv
GOVERNANCE_DYNAMODB_TABLE=TagGovernanceState
GOVERNANCE_SNS_TOPIC_ARN=arn:aws:sns:ap-south-1:<ACCOUNT_ID>:tag-governance-alerts
SQS_QUEUE_URL=https://sqs.ap-south-1.amazonaws.com/<ACCOUNT_ID>/tag-governance-queue
REPORT_BUCKET=<your-unique>-tag-reports
```

Create a couple of test resources so there's something to read and tag:

```bash
aws ec2 create-vpc --region $REGION --cidr-block 10.99.0.0/16 \
  --tag-specifications 'ResourceType=vpc,Tags=[{Key=Owner,Value=platform},{Key=Environment,Value=dev}]'
aws s3 mb s3://<your-unique>-untagged-test --region $REGION   # intentionally untagged
```

---

## 7. Step 3 — Start the web server

```bash
set -a; source .env; set +a
make run-web      # Flask dev server with auto-reload
make run-prod     # gunicorn — what the Docker image runs
```

> Run **one** gunicorn worker (use threads for concurrency). The refresh lock, caches and scheduler are per-process; several workers would run duplicate scans.

On startup, `validate_config()` logs one of: `VALID`, `OPTIONAL CONFIGURATION MISSING`, `INVALID / MISSING AWS CREDENTIALS`, `AWS ACCESS DENIED`. The app still starts with no credentials, but AWS-backed endpoints will fail.

- UI: http://127.0.0.1:5050/
- Swagger: http://127.0.0.1:5050/api/docs

```bash
export API=http://127.0.0.1:5050
curl -s $API/health                        # {"status":"ok"}  (liveness, no auth)
curl -s $API/ready | jq                    # readiness: DB + schema + AWS state at startup
curl -s $API/api/me | jq                   # who am I, roles, permissions, enabled features
curl -s $API/api/meta/resource-types | jq '.aliases | length'
```

---

## 8. Step 4 — Test each feature

### 8.1 TagRead
**Env:** `AWS_DEFAULT_REGION`. **Role:** any.

```bash
# VPCs tagged Environment=dev
curl -s -X POST $API/api/read -H 'Content-Type: application/json' \
  -d '{"resource":"VPC","filters":{"Environment":"dev"},"region":"ap-south-1"}' | jq

# S3 buckets MISSING the Owner tag
curl -s -X POST $API/api/read -H 'Content-Type: application/json' \
  -d '{"resource":"S3","missing_tag":"Owner"}' | jq

# Several types across all regions
curl -s -X POST $API/api/read -H 'Content-Type: application/json' \
  -d '{"resources":["EC2Instance","S3"],"regions":"all"}' | jq '.body // .' | head -50
```
✅ **Expect:** the VPC you created appears in the first call and the untagged bucket in the second.

### 8.2 TagWriter + audit log
**Env:** `TAG_API_BATCH_SIZE`, `SQLITE_DB_PATH`. **Role:** `TagOperator` / `PlatformAdmin` (or `ApplicationOwner` on resources they own).

```bash
curl -s -X POST $API/api/write -H 'Content-Type: application/json' \
  -d '{"arns":["arn:aws:s3:::<your-unique>-untagged-test"],"tags":{"Owner":"platform","Environment":"dev"}}' | jq

aws s3api get-bucket-tagging --bucket <your-unique>-untagged-test
curl -s "$API/api/audit?limit=5" | jq '.audit_events[0]'
```
✅ **Expect:** 200 (or 207 for partial success), the tags are visible in AWS, and a `TAG_WRITE` / `SUCCESS` audit row.

A write is a **partial update**: only the tags you send are validated (allowed values, regex), and other tags on the resource are untouched, so you can add one tag without re-sending the required ones. An invalid value returns 400 with the reason:

```bash
curl -s -X POST $API/api/write -H 'Content-Type: application/json' \
  -d '{"arn":"arn:aws:s3:::<your-unique>-untagged-test","tags":{"Environment":"production"}}' | jq
# {"error_code":"VALIDATION_ERROR", "details":[{"tag":"Environment","type":"INVALID_VALUE","expected":"One of: dev, qa, staging, prod, sandbox","actual":"production"}], ...}
```

🔒 **RBAC test:** restart with `DEV_AUTH_USER=bob:Viewer` and repeat → **403 FORBIDDEN**.

### 8.3 Governance scan (TagOnCreate)
**Env:** `OWNER_TAG_KEY`, `TAG_LOOKUP_RETRIES`, `TAG_LOOKUP_DELAY_SEC`.

```bash
curl -s -X POST $API/api/gov -H 'Content-Type: application/json' \
  -d '{"action":"scan","regions":["ap-south-1"]}' | jq
```
✅ **Expect:** fully untagged resources found and given an `Owner` tag (looked up from CloudTrail creator info).

**Event mode (unit test):** `uv run pytest tests/test_tag_on_create.py -v`. In AWS, deploy `src/tag_on_create.py` as a Lambda behind an EventBridge rule on CloudTrail `RunInstances`, `CreateBucket`, etc. (see `DEPLOYMENT.md`).

### 8.4 Compliance report & dashboard
**Env:** `MANDATORY_TAGS`, `COMPLIANCE_CACHE_TTL_SECONDS`, `REPORT_BUCKET`, `SQLITE_DB_PATH`.

```bash
# Synchronous full report (saved into the cache)
curl -s -X POST $API/api/report -H 'Content-Type: application/json' \
  -d '{"regions":["ap-south-1"],"mandatory_tags":["Owner","Environment"]}' | jq '.summary'

# Async refresh -> poll -> read
curl -s -X POST $API/api/compliance/refresh -H 'Content-Type: application/json' -d '{"regions":["ap-south-1"]}'
curl -s $API/api/compliance/status | jq     # wait until is_refreshing=false
curl -s $API/api/compliance/summary | jq
curl -s $API/api/dashboard | jq
curl -s $API/api/compliance | jq '.resources[] | select(.status=="NON_COMPLIANT")' | head -40
```
✅ **Expect:** `compliance_pct` and counts appear, and a second refresh while one is running returns `already_refreshing`. Results survive a server restart (SQLite).

How a resource is judged: every schema tag with `required: true` **plus** every key in `MANDATORY_TAGS` (or the request's `mandatory_tags`) must be present; values must match `allowed_values` / `pattern`. Tags not in the schema are **warnings** in `warn` mode and don't fail the resource; `aws:*` tags are ignored.

If **every** region fails (e.g. expired credentials), the refresh reports `last_refresh_error` and **keeps the last good scan** instead of saving an empty one.

```bash
curl -s $API/api/compliance/history | jq '.scans[] | {timestamp, compliance_score}'   # trend
curl -s -o compliance.csv $API/api/compliance/export.csv                              # spreadsheet
curl -s "$API/api/compliance?include_exemptions=1" | jq '.resources[0]'              # + exemption state
```

> Limitation: the Resource Groups Tagging API only returns resources that are or were tagged. A resource that **never** had any tag is invisible to scans (see ROADMAP.md: AWS Config / Resource Explorer).

### 8.5 TagSync (VPC → children)
**Env:** `AWS_DEFAULT_REGION`.

```bash
VPC_ID=$(aws ec2 describe-vpcs --region ap-south-1 --filters Name=cidr,Values=10.99.0.0/16 --query 'Vpcs[0].VpcId' --output text)
aws ec2 create-subnet --region ap-south-1 --vpc-id $VPC_ID --cidr-block 10.99.1.0/24

curl -s -X POST $API/api/sync -H 'Content-Type: application/json' \
  -d "{\"action\":\"sync_vpc\",\"vpc_id\":\"$VPC_ID\",\"region\":\"ap-south-1\"}" | jq
aws ec2 describe-subnets --region ap-south-1 --filters Name=vpc-id,Values=$VPC_ID --query 'Subnets[].Tags'
```
✅ **Expect:** the subnet, the default security group and the route table now carry `Owner` and `Environment`.

### 8.6 Governance schema & normalization
**Env:** `GOVERNANCE_SCHEMA_PATH`, `GOVERNANCE_UNKNOWN_TAGS`, `GOVERNANCE_NORMALIZATION`, `GOVERNANCE_STRICT_MODE`.

```bash
curl -s $API/api/schema | jq
uv run pytest tests/test_governance.py -v
```
Try it: add a tag rule to `config/tag-schema.yaml`, restart, and confirm it appears in `/api/schema` and in the CLI results (8.7).

### 8.7 CI/CD CLI validator (offline)
**Env:** `GOVERNANCE_SCHEMA_PATH`, `GOVERNANCE_UNKNOWN_TAGS`, `GOVERNANCE_NORMALIZATION`.

```bash
mkdir -p /tmp/tfdemo && cat > /tmp/tfdemo/main.tf <<'EOF'
resource "aws_instance" "bad" {
  ami = "ami-123"
  tags = { CostCenter = "1234", env = "prod" }
}
resource "aws_s3_bucket" "good" {
  bucket = "x"
  tags = { Owner = "platform", Environment = "dev", CostCenter = "123456" }
}
EOF

./aws-tagging-utils validate /tmp/tfdemo; echo "exit=$?"
./aws-tagging-utils validate /tmp/tfdemo --format json
./aws-tagging-utils validate /tmp/tfdemo --format sarif --output /tmp/tags.sarif
```
✅ **Expect (verified):** `bad` reports `MISSING_REQUIRED Owner` and `INVALID_FORMAT CostCenter` (`env` is normalized to `Environment`, so it isn't flagged), `good` passes, and **exit=1**. If everything is compliant, exit=0. Scans `.tf`/`.tf.json` (Terraform) and `.yaml`/`.yml`/`.json` (CloudFormation).

### 8.8 AWS Config rule / SCP generators (offline)
```bash
./aws-tagging-utils generate config | jq
./aws-tagging-utils generate scp | jq
```
✅ **Expect:** a CloudFormation template with one `REQUIRED_TAGS` AWS Config rule per required tag, and an SCP that denies `ec2:RunInstances` / `s3:CreateBucket` without the required tags, both derived from the schema.

> `./aws-tagging-utils drift` and `./aws-tagging-utils compliance` are **stubs** today and only print a placeholder message.

### 8.9 Exemptions
**Env:** `GOVERNANCE_DYNAMODB_TABLE` (table must exist). **Role:** `SecurityAdmin` / `PlatformAdmin` to create or revoke.

```bash
EX=$(curl -s -X POST $API/api/exemptions -H 'Content-Type: application/json' -d '{
  "resource_id":"arn:aws:s3:::<your-unique>-untagged-test",
  "reason":"legacy bucket, migration ticket OPS-123",
  "expires_at":"2026-12-31T00:00:00+00:00"}')
echo $EX | jq; EX_ID=$(echo $EX | jq -r .id)

curl -s $API/api/exemptions | jq
curl -s $API/api/exemptions/$EX_ID | jq
# Same scope again -> 409 EXEMPTION_CONFLICT
curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/api/exemptions -H 'Content-Type: application/json' \
  -d '{"resource_id":"arn:aws:s3:::<your-unique>-untagged-test","reason":"dup"}'
```
`expires_at` accepts a date (`2026-12-31`) or a datetime; values without a timezone are treated as UTC and stored with an explicit offset. Unparseable values are rejected with 400.

Only these scope combinations are valid (any other mix returns 400 `INVALID_EXEMPTION_SCOPE`). They are listed most specific first, and the most specific one wins:
`resource_id` › `resource_type`+`environment` › `resource_type` › `account_id`+`environment` › `account_id`. Keep `$EX_ID` for 8.10, then revoke:

```bash
curl -s -X DELETE $API/api/exemptions/$EX_ID | jq
```

### 8.10 Remediation (synchronous, idempotent)
**Env:** `GOVERNANCE_DYNAMODB_TABLE`, `MAX_REMEDIATION_ATTEMPTS`, `GOVERNANCE_SNS_TOPIC_ARN`. **Role:** `TagOperator` / `PlatformAdmin`.

```bash
R=$(curl -s -X POST $API/api/remediation -H 'Content-Type: application/json' -d '{
  "resource_arn":"arn:aws:s3:::<your-unique>-untagged-test",
  "requested_tags":{"Owner":"platform","Environment":"dev"},
  "idempotency_key":"demo-001"}')
echo $R | jq                                        # status COMPLETED (HTTP 201)
curl -s $API/api/remediation/demo-001 | jq

# Same key, different tags -> 409 IDEMPOTENCY_MISMATCH
curl -s -X POST $API/api/remediation -H 'Content-Type: application/json' -d '{
  "resource_arn":"arn:aws:s3:::<your-unique>-untagged-test",
  "requested_tags":{"Owner":"someone-else"},"idempotency_key":"demo-001"}' | jq
```
✅ **Also test:** with an active exemption on the resource (8.9), a new request returns `SKIPPED_EXEMPT`. Check `/api/audit` for `REMEDIATE_STARTED` / `REMEDIATE_FINISHED` rows.

Unit tests: `uv run pytest tests/test_remediation.py tests/test_exemptions.py tests/test_exemption_api.py -v`

### 8.11 Remediation worker (async via SQS)
**Env:** `SQS_QUEUE_URL`, `SQS_WAIT_TIME_SECONDS`, `SQS_VISIBILITY_TIMEOUT_SECONDS`, `SQS_MAX_MESSAGES`, `GOVERNANCE_DYNAMODB_TABLE`, `ECS_TASK_ID`.

```bash
# Terminal 1
set -a; source .env; set +a
uv run python -m src.worker

# Terminal 2 — health-check job
aws sqs send-message --queue-url $SQS_QUEUE_URL --message-body '{
  "version":"1","job_id":"hc-1","job_type":"WORKER_HEALTH_CHECK",
  "correlation_id":"c-1","payload":{}}'

# REMEDIATION job — action_id must exist in DynamoDB with status PENDING or FAILED_RETRYABLE
aws sqs send-message --queue-url $SQS_QUEUE_URL --message-body '{
  "version":"1","job_id":"j-2","job_type":"REMEDIATION",
  "correlation_id":"c-2","payload":{"action_id":"<action_id>"}}'

# Malformed message -> NOT deleted, ends up in the DLQ after maxReceiveCount
aws sqs send-message --queue-url $SQS_QUEUE_URL --message-body 'not json'
```
✅ **Expect:** logs show `Message received` → `Handler succeeded` and the message is deleted. Ctrl-C shuts down gracefully after the current message.

Unit tests: `uv run pytest tests/test_worker.py tests/test_worker_remediation.py -v`

### 8.12 Enforcement Lambda (EventBridge)
**Env:** `GOVERNANCE_SCHEMA_PATH`, `GOVERNANCE_DYNAMODB_TABLE`, `GOVERNANCE_SNS_TOPIC_ARN`.

Local invocation with a sample CloudTrail event:

```bash
uv run python - <<'EOF'
from src.enforcement import lambda_handler
evt = {"detail-type":"AWS API Call via CloudTrail","account":"<ACCOUNT_ID>","region":"ap-south-1",
       "detail":{"eventName":"CreateBucket","awsRegion":"ap-south-1",
                 "requestParameters":{"bucketName":"<your-unique>-untagged-test"}}}
print(lambda_handler(evt, None))
EOF
```
✅ **Expect:** non-compliant resources go through `process_sync` (remediation), compliant ones return `COMPLIANT`, and unsupported events return 400. `aws.tag` "Tag Change on Resource" events are accepted but are **placeholders** for now.

### 8.13 FinOps (Cost Explorer)
**Env:** `FINOPS_CACHE_TTL_SECONDS`, `FINOPS_PRIMARY_TAG`. **Role:** `FinOps` / `PlatformAdmin`. **IAM:** `ce:GetCostAndUsage`. Cost Explorer must be enabled, and **each CE API call costs $0.01**.

```bash
curl -s -X POST $API/api/finops/refresh | jq     # {"status":"started"} 202
curl -s $API/api/finops/status | jq
curl -s $API/api/finops | jq                     # 202 "not_ready" until the first snapshot exists
./aws-tagging-utils finops report | jq           # CLI equivalent
```
To make a tag count toward spend allocation, mark it in the schema:
```yaml
  CostCenter:
    required: false
    pattern: "^[0-9]{6}$"
    finops:
      cost_allocation: true
```
and activate it as a cost-allocation tag in the Billing console (it takes about 24h before data appears).

🔒 **RBAC test:** `DEV_AUTH_USER=bob:Viewer` → 403 on `/api/finops`.

### 8.14 Web UI walkthrough
Open http://127.0.0.1:5050/ (tabs are deep-linkable: `/#compliance`, `/#enforcement`, …).

| Where | What to try | Expect |
|-------|-------------|--------|
| Header | — | Your user and roles from the server (`/api/me`). Buttons your role can't use are disabled |
| Dashboard | Hover the trend line and bars | Tooltip per scan / violation / service; **View as table** shows the numbers |
| Dashboard | Break credentials, click **Refresh** | Banner/status shows the error; the last good scan stays; no refresh loop |
| Compliance | Filter by status, service, region, issue type, free text | Exact matches across all pages (not just the visible page) |
| Compliance | **Fix Tags** on a resource with a missing or invalid tag | Modal pre-fills invalid values; schema `allowed_values` become a dropdown; regex checked before submit; success triggers a re-scan |
| Compliance | **Exempt** | Reason + expiry (default 30 days) required; row shows "Exempt until …" after reload |
| Compliance | **Export CSV** | Downloads the latest scan |
| Enforcement | Remediation / Exemptions / Audit tables | Real data from `/api/remediation`, `/api/exemptions`, `/api/audit`; **Revoke** asks for confirmation then revokes |
| Schema | — | Required (incl. `MANDATORY_TAGS`), allowed values, regex and aliases per tag |

### 8.15 Platform features (v0.4)

#### Full inventory & coverage gaps
**Env:** `INVENTORY_SOURCE`, `RESOURCE_EXPLORER_REGION`, `RESOURCE_EXPLORER_VIEW_ARN`, `COMPLIANCE_SCOPE`.
**IAM:** `resource-explorer-2:ListResources`.

```bash
# One-time: turn on Resource Explorer (aggregator index in your home region + a default view)
aws resource-explorer-2 create-index --region ap-south-1 --type AGGREGATOR
aws resource-explorer-2 create-view --region ap-south-1 --view-name all --included-properties Name=tags
aws resource-explorer-2 associate-default-view --region ap-south-1 --view-arn <view-arn>

# .env
INVENTORY_SOURCE=resource_explorer
RESOURCE_EXPLORER_REGION=ap-south-1
```
Refresh, then:
```bash
curl -s $API/api/inventory/coverage | jq '{never_tagged, unmapped_resources, unmapped_types: .unmapped_types[:5]}'
```
✅ **Expect:** `never_tagged` > 0 when you have resources without any tag (they're now NON_COMPLIANT and filterable via **Issue → Never tagged**); `unmapped_types` lists services the scan saw but doesn't evaluate (e.g. `kafka:cluster`, `bedrock:agent`). The Dashboard shows an **Inventory coverage** card; the full list is under **Schema → Discovered but not evaluated**. `COMPLIANCE_SCOPE=all` evaluates every type. If Resource Explorer isn't set up in a region, that region falls back to the tagging API with a warning.

#### Bulk fix with preview, undo and suggestions
**Env:** `MAX_BULK_RESOURCES`, `CHANGESET_RETENTION_DAYS`. **Role:** PlatformAdmin / TagOperator (ApplicationOwner: only resources they own).

UI: **Compliance** → tick rows or **Select all filtered** → **Bulk fix…** → keys missing on the selection are pre-filled with 💡 suggestions → **Preview** → **Apply** → **Changes** tab → **Undo**.

```bash
P=$(curl -s -X POST $API/api/bulk/preview -H 'Content-Type: application/json' \
  -d '{"arns":["<arn1>","<arn2>"],"tags":{"Owner":"platform"}}')
echo $P | jq '.summary'                      # resources_changing, keys_added, compliant_before → after
CS=$(curl -s -X POST $API/api/bulk/apply -H 'Content-Type: application/json' \
  -d "{\"arns\":[\"<arn1>\",\"<arn2>\"],\"tags\":{\"Owner\":\"platform\"},\"preview_token\":$(echo $P | jq .preview_token)}" | jq -r .id)
curl -s -X POST $API/api/changesets/$CS/undo | jq '.summary'
curl -s -X POST $API/api/suggestions -H 'Content-Type: application/json' -d '{"arns":["<arn>"],"keys":["Owner"]}' | jq
```
✅ **Expect:** only the listed keys change (existing values are kept unless `"overwrite": true`); invalid values are reported, not written; if resources changed after the preview, apply returns **409 PLAN_CHANGED**; undo restores previous values and removes added keys, but **leaves alone any key someone changed since** (reported as a conflict). Every resource change is in the audit log with its change set id.

#### Multi-account scans & leaderboards
**Env:** `COMPLIANCE_ACCOUNTS`, `MULTI_ACCOUNT_ROLE_NAME`, `MULTI_ACCOUNT_EXTERNAL_ID`, `TEAM_TAG_KEY`, `HISTORY_RETENTION_DAYS`.
**IAM (central account):** `sts:AssumeRole` on the member role, `organizations:ListAccounts`, `organizations:ListParents`, `organizations:DescribeOrganizationalUnit`.

1. Deploy `deploy/stacksets/governance-role.yaml` to member accounts (section 13). It now carries every read/tag permission the tool uses; `AllowTagWrites=false` makes it read-only.
2. `.env`: `COMPLIANCE_ACCOUNTS=all` (or `111111111111,222222222222`).
3. Refresh. Then:
```bash
curl -s "$API/api/leaderboard?dimension=team"    | jq '.rows[] | {rank, name, compliance_pct, delta}'
curl -s "$API/api/leaderboard?dimension=account" | jq '.rows[] | {name, ou, compliance_pct}'
curl -s "$API/api/leaderboard?dimension=ou"      | jq
curl -s $API/api/organization | jq '.accounts[] | {name, ou, compliance_pct, errors}'
```
✅ **Expect:** one row per team (`Team` tag, falling back to `Owner`), account, OU or service; `delta` = change vs the newest scan ≥ 7 days older (null until one exists). An account whose role can't be assumed shows its error in **Leaderboard → Accounts scanned** without failing the other accounts.

#### My Resources (owner view)
**Env:** `OWNER_MATCH_TAGS`, `DEV_AUTH_EMAIL` (local), `EXEMPTION_EXPIRY_WARNING_DAYS`, `OWNER_COST_CACHE_SECONDS`.
A resource is yours when its `Owner` equals your user id/email or ends with `/<email>` (AWS SSO session names like `AWSReservedSSO_Admin_x/jane@example.com`).
```bash
curl -s $API/api/me/resources | jq '{summary, cost, expiring: [.expiring_exemptions[] | {days_left, reason}]}'
curl -s "$API/api/me/resources?owner=payments" | jq '.summary'     # admins: view as another owner
```
✅ **Expect:** your resources, their violations, exemptions expiring within 14 days, and month-to-date **owned vs unallocated spend** (resources with your Owner tag but no cost-allocation tag). Cost needs `ce:GetCostAndUsage` and the Owner tag activated as a cost allocation tag; otherwise the UI explains why it's N/A. UI: **My Resources → Fix all mine…** opens the bulk fix for your non-compliant resources.

#### Tag propagation checks (10 parent types)
**Env:** `PROPAGATE_KEYS`, `PROPAGATE_EXCLUDE_KEYS`.

| Rule | Parent → children | Config check |
|------|-------------------|--------------|
| `vpc` | VPC → subnets, SGs, route tables, IGW/NAT, NACLs, endpoints | — |
| `ec2_instance` | instance → EBS volumes, ENIs | — |
| `ebs_volume` | volume → snapshots | — |
| `asg` | Auto Scaling group → instances | `PropagateAtLaunch` off |
| `ecs_service` | ECS service → tasks | `propagateTags=NONE` |
| `cloudformation` | stack → resources it created | — |
| `rds_cluster` | cluster → DB instances | — |
| `eks_cluster` | cluster → managed node groups | — |
| `elbv2` | ALB/NLB → target groups | — |
| `lambda` | function → `/aws/lambda/<name>` log group | — |

UI: **Propagation** → pick rules → **Run check** → filter → select → **Preview fix…** → **Apply** (undoable).
```bash
curl -s -X POST $API/api/propagation/run -H 'Content-Type: application/json' -d '{"rules":["ec2_instance","asg"]}'
curl -s $API/api/propagation | jq '.run.summary'
```
✅ **Expect:** findings list missing / mismatched keys per child, and config findings for ASGs/ECS services whose propagation is off (the fix switches it on; undo switches it back). `Name` and `aws:*` tags are never propagated.

#### Propagate from one parent (Tag Tools → Tag Propagator, API, MCP)
```bash
curl -s -X POST $API/api/sync -H 'Content-Type: application/json' \
  -d '{"action":"propagate","rule":"asg","parent":"eks-nodes","region":"ap-south-1","dry_run":true}' | jq '.preview.summary, .config_changes'
curl -s -X POST $API/api/sync -H 'Content-Type: application/json' \
  -d '{"action":"propagate","rule":"asg","parent":"eks-nodes","region":"ap-south-1"}' | jq '{change_set_id, status}'
```
The legacy `{"action":"sync_vpc","vpc_id":...}` still works, and **no longer copies the VPC's `Name` onto every subnet/SG/route table** (it used to rename them all).

### 8.16 Endpoints that intentionally return 501
`/api/security` and `/api/cicd` are not implemented yet and return `501 NOT_IMPLEMENTED`.

---

## 9. MCP server (Claude Desktop / Claude Code)

**Env:** same AWS and governance variables as above.

```bash
set -a; source .env; set +a
uv run python mcp_server.py        # stdio transport
```

Register it with Claude Code:
```bash
claude mcp add aws-tagging -- uv --directory "$PWD" run python mcp_server.py
```
or in Claude Desktop `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "aws-tagging-utils": {
      "command": "uv",
      "args": ["--directory", "/ABS/PATH/aws-tagging-utils", "run", "python", "mcp_server.py"],
      "env": { "AWS_DEFAULT_REGION": "ap-south-1", "AWS_PROFILE": "sandbox" }
    }
  }
}
```

Tools: `list_resource_types`, `read_tags`, `write_tags`, `apply_governance`, `get_tag_report`, `sync_tags` (any of the 10 propagation rules, with `dry_run`), `check_tag_propagation`, `preview_tag_changes`, `apply_tag_changes`, `undo_tag_changes`, `suggest_tag_values`, `compliance_leaderboard`.

Logs go to **stderr** (stdout is the MCP JSON-RPC channel). Set `MCP_READ_ONLY=true` to expose only the read tools.

**Test prompts:**
- "List the resource types you support."
- "Find S3 buckets in ap-south-1 missing the Owner tag."
- "Give me a compliance report for ap-south-1 with mandatory tags Owner and Environment."
- "Sync tags from VPC `<vpc-id>` to its children."

Or inspect it interactively with `npx @modelcontextprotocol/inspector uv run python mcp_server.py`.

> ⚠️ The MCP server does **not** apply RBAC. Anyone who can talk to it can write tags with the AWS identity it runs as. Give that identity least privilege, or run with `MCP_READ_ONLY=true`.

---

## 10. Production auth — ALB OIDC

```dotenv
AUTH_MODE=alb_oidc
AUTH_ALB_ARN=arn:aws:elasticloadbalancing:ap-south-1:<ACCOUNT_ID>:loadbalancer/app/tag-gov/abc123
AUTH_AWS_REGION=ap-south-1
AUTH_ROLE_MAPPING={"aws-platform-admins":"PlatformAdmin","finops-team":"FinOps","sec-team":"SecurityAdmin","devs":"Viewer"}
```

1. ALB HTTPS listener → rule action `authenticate-oidc` (or Cognito) → forward to the target group on port 5050.
2. Your IdP must put the user's groups in a `groups` claim.
3. Restrict the target's security group so it is reachable **only from the ALB**, because the header is trusted only after its signature is verified.

**Test:**
```bash
curl -s -o /dev/null -w '%{http_code}\n' http://<target-ip>:5050/api/dashboard   # 401 (no JWT)
# Through the ALB in a browser: login redirect → dashboard loads
```
Unit tests cover forged, expired, HS256 and wrong-signer tokens: `uv run pytest tests/test_auth.py -v`.

---

## 11. Docker

```bash
# .env must exist; inside Docker keep SQLITE_DB_PATH=/app/data/app.db
make build
docker compose up -d web           # UI/API on :5050, SQLite in volume sqlite_data
docker compose ps                  # wait for "healthy"
curl -s localhost:5050/health
docker compose logs -f web

docker compose run --rm mcp        # MCP server (stdio)
docker compose run --rm web python -m src.worker            # worker
docker compose run --rm web python -m src.cli.validator validate config/   # CLI
docker compose down                # add -v to wipe SQLite
```
`~/.aws` is mounted read-only. Set `AWS_PROFILE` in `.env` if you don't use `default`.

EC2 deployment: `scripts/ec2_bootstrap.sh` and `docs/EC2_DEPLOYMENT.md`.

---

## 12. Metrics & monitoring

`GET /metrics` serves Prometheus text format (set `METRICS_AUTH_TOKEN` to require a bearer token).

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `tagging_utils_compliance_score_percent` | gauge | — | Score of the latest scan |
| `tagging_utils_compliance_resources` | gauge | region, resource_type, status | Resources in the latest scan |
| `tagging_utils_compliance_violations` | gauge | tag, type | Violations in the latest scan |
| `tagging_utils_compliance_last_scan_timestamp_seconds` | gauge | — | When the latest scan completed |
| `tagging_utils_compliance_scan_duration_seconds` | histogram | — | Scan duration |
| `tagging_utils_compliance_scans_total` | counter | outcome (`success`/`partial`/`failed`) | Scans run |
| `tagging_utils_compliance_region_errors_total` | counter | region | Regions that failed during scans |
| `tagging_utils_aws_api_calls_total` | counter | service, operation, outcome | Every AWS call (outcome = `success` or the error code, e.g. `ThrottlingException`) |
| `tagging_utils_aws_api_call_duration_seconds` | histogram | service, operation | AWS latency incl. retries |
| `tagging_utils_http_requests_total` | counter | method, endpoint, status | API traffic (route templates, bounded cardinality) |
| `tagging_utils_http_request_duration_seconds` | histogram | method, endpoint | API latency |
| `tagging_utils_tag_writes_total` | counter | outcome | Resources tagged / failed |
| `tagging_utils_remediation_actions_total` | counter | status | Remediation results |
| `tagging_utils_auto_tagged_resources_total` | counter | — | Owner tags applied by TagOnCreate |
| `tagging_utils_exemption_changes_total` | counter | action | Exemptions created / revoked |
| `tagging_utils_finops_spend_usd` | gauge | kind | Spend from the latest FinOps snapshot |
| `tagging_utils_worker_messages_total` | counter | job_type, outcome | SQS worker jobs |
| `tagging_utils_build_info` | info | version, python | Build metadata |

Prometheus scrape config:
```yaml
scrape_configs:
  - job_name: tagging-utils
    metrics_path: /metrics
    authorization: { credentials: change-me }     # only if METRICS_AUTH_TOKEN is set
    static_configs: [{ targets: ["tagging-utils:5050"] }]
```

Suggested alerts:
```yaml
groups:
  - name: tagging-utils
    rules:
      - alert: TagComplianceLow
        expr: tagging_utils_compliance_score_percent < 80
        for: 1h
      - alert: TagComplianceScanStale
        expr: time() - tagging_utils_compliance_last_scan_timestamp_seconds > 6 * 3600
      - alert: TagComplianceScanFailing
        expr: increase(tagging_utils_compliance_scans_total{outcome="failed"}[1h]) > 0
      - alert: AwsThrottling
        expr: sum(rate(tagging_utils_aws_api_calls_total{outcome=~".*Throttl.*"}[5m])) > 0.1
```

For fresh gauges without anyone opening the UI, set `COMPLIANCE_SCAN_INTERVAL_SECONDS=900` and `COMPLIANCE_REGIONS`.
For Lambda/ECS without Prometheus, set `METRICS_EMF_ENABLED=true`: scans, tag writes, remediations and auto-tagging emit CloudWatch metrics under `METRICS_NAMESPACE`.

**Test:**
```bash
curl -s $API/metrics | grep -E '^tagging_utils_(compliance_score|compliance_violations|aws_api_calls_total)'
```

---

## 13. Multi-account (AWS Organizations)

**Env:** `MULTI_ACCOUNT_ROLE_NAME` (must match the role created by the StackSet).

```bash
# From the management / delegated-admin account
for T in governance-role event-forwarder; do
  aws cloudformation create-stack-set --stack-set-name tag-gov-$T \
    --template-body file://deploy/stacksets/$T.yaml \
    --parameters ParameterKey=ManagementAccountId,ParameterValue=<MGMT_ACCOUNT_ID> \
    --capabilities CAPABILITY_NAMED_IAM \
    --permission-model SERVICE_MANAGED --auto-deployment Enabled=true,RetainStacksOnAccountRemoval=false
  aws cloudformation create-stack-instances --stack-set-name tag-gov-$T \
    --deployment-targets OrganizationalUnitIds=<ou-xxxx> --regions ap-south-1
done
```
**Test:**
```bash
aws sts assume-role --role-arn arn:aws:iam::<MEMBER_ID>:role/AWSOrganizationTagGovernanceRole --role-session-name t
uv run pytest tests/test_multi_account.py -v
```
The central default event bus also needs a resource policy that lets member accounts call `events:PutEvents`. See `docs/MULTI_ACCOUNT.md`.

> The governance role template only grants `tag:GetResources`, `tag:TagResources` and `ec2:DescribeRegions`. Add per-service tagging permissions (e.g. `s3:PutBucketTagging`, `ec2:CreateTags`) or writes in member accounts will fail.

---

## 14. End-to-end checklist

| ✔ | Check | Command |
|---|-------|---------|
| ☐ | Unit tests green | `uv run pytest -m "not integration"` |
| ☐ | Lint/types | `make lint typecheck` |
| ☐ | Health / readiness | `curl $API/health`, `curl $API/ready` |
| ☐ | Identity & permissions | `curl $API/api/me` |
| ☐ | Metrics scrape | `curl $API/metrics` (section 12) |
| ☐ | Trend + CSV export | 8.4 |
| ☐ | Read | 8.1 |
| ☐ | Write + audit row | 8.2 |
| ☐ | Viewer gets 403 on write | 8.2 RBAC |
| ☐ | Governance scan | 8.3 |
| ☐ | Compliance refresh + dashboard | 8.4 |
| ☐ | VPC sync | 8.5 |
| ☐ | CLI validate exit codes + SARIF | 8.7 |
| ☐ | Config/SCP generation | 8.8 |
| ☐ | Exemption create/conflict/revoke | 8.9 |
| ☐ | Remediation + idempotency 409 + exempt skip | 8.10 |
| ☐ | Worker processes SQS job | 8.11 |
| ☐ | Enforcement Lambda local invoke | 8.12 |
| ☐ | FinOps snapshot | 8.13 |
| ☐ | MCP tools via Claude | 9 |
| ☐ | Docker healthy | 11 |

---

## 15. Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `401 Unauthorized. ALB OIDC identity required.` locally | `AUTH_MODE` not set (the code defaults to `alb_oidc`) or `DEV_AUTH_USER` empty. Did you `source .env`? |
| `403 Forbidden` on write/finops/exemptions | Role name wrong or case-mismatched, e.g. `admin:admin`. Use `PlatformAdmin` etc. |
| `500 AUTH_MISCONFIGURED` | `AUTH_MODE=alb_oidc` without `AUTH_ALB_ARN` |
| Startup log `INVALID / MISSING AWS CREDENTIALS`, or API `503 INVALID_CREDENTIALS` | The **server's** AWS credentials are missing/expired (e.g. SSO session); `aws sso login` / `aws sts get-caller-identity` |
| Refresh says "All regions failed to scan" | Same as above. The last good scan is kept on purpose |
| Resource with tags shows NON_COMPLIANT | Check the **Issues** column: a `MANDATORY_TAGS` key, an invalid value, or `GOVERNANCE_UNKNOWN_TAGS=reject` with a tag not in the schema |
| Exemptions/remediation 500 `ResourceNotFoundException` | DynamoDB table missing (section 6) or wrong region |
| `/api/finops` stays 202 | First snapshot still running, or CE disabled / no `ce:GetCostAndUsage`; check `/api/finops/status` → `last_refresh_error` |
| Worker exits at once | `SQS_QUEUE_URL` not set |
| `sqlite3.OperationalError: unable to open database` locally | `SQLITE_DB_PATH=/app/data/...` is a Docker path; use `./app.db` |
| Dashboard empty | No scan yet; `POST /api/compliance/refresh` |

---

## 16. Cleanup

```bash
aws dynamodb delete-table --table-name TagGovernanceState --region ap-south-1
aws sqs delete-queue --queue-url $SQS_QUEUE_URL
aws sns delete-topic --topic-arn $GOVERNANCE_SNS_TOPIC_ARN
aws s3 rb s3://<your-unique>-untagged-test --force
aws s3 rb s3://<your-unique>-tag-reports --force
# delete the test subnet then VPC 10.99.0.0/16
docker compose down -v
```
