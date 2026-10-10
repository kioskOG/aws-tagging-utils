# EC2 Docker Deployment Guide — aws-tagging-utils

> **Goal:** Deploy the aws-tagging-utils Flask application + MCP server on a single EC2 instance using Docker Compose, and validate it end-to-end against real AWS resources.

---

## Prerequisites

| What | Minimum |
|---|---|
| EC2 instance type | `t3.small` (2 vCPU, 2 GB RAM) |
| OS | Amazon Linux 2023 **or** Ubuntu 22.04 LTS |
| AMI | Latest Amazon Linux 2023 or Ubuntu 22.04 |
| Storage | 20 GB gp3 root volume |
| Security Group — inbound | Port 22 (SSH, your IP only), Port 5050 (HTTP, your IP only) |
| EC2 IAM Instance Profile | See [Step 2 — IAM Role](#step-2--iam-role-for-ec2) |
| Git | Clone from local Mac or GitHub |

---

## Step 1 — Launch the EC2 Instance

### Via AWS Console

1. Go to **EC2 → Launch Instance**
2. Name: `aws-tagging-utils-e2e`
3. AMI: **Amazon Linux 2023** (recommended) or **Ubuntu 22.04**
4. Instance type: `t3.small`
5. Key pair: select your existing key pair
6. Network: your default VPC, a public subnet
7. Security group — inbound rules:

   | Type | Port | Source |
   |---|---|---|
   | SSH | 22 | Your IP (`x.x.x.x/32`) |
   | Custom TCP | 5050 | Your IP (`x.x.x.x/32`) |

8. Storage: 20 GB gp3
9. **IAM instance profile**: attach the role from Step 2
10. Click **Launch**

### Via AWS CLI (one-liner)

```bash
# Replace with your real values
aws ec2 run-instances \
  --image-id ami-0dee22c13ea7a9a67 \
  --instance-type t3.small \
  --key-name YOUR_KEY_PAIR_NAME \
  --iam-instance-profile Name=aws-tagging-utils-ec2-role \
  --security-group-ids sg-XXXXXXXX \
  --subnet-id subnet-XXXXXXXX \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":20,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=aws-tagging-utils-e2e},{Key=Project,Value=aws-tagging-utils},{Key=Environment,Value=e2e}]' \
  --region ap-south-1 \
  --query 'Instances[0].PublicIpAddress' \
  --output text
```

---

## Step 2 — IAM Role for EC2

Create a role that allows the application to call the necessary AWS services. Attach it to the EC2 instance.

### Create the trust policy

```bash
cat > /tmp/trust-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ec2.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
EOF
```

### Create the IAM role

```bash
aws iam create-role \
  --role-name aws-tagging-utils-ec2-role \
  --assume-role-policy-document file:///tmp/trust-policy.json \
  --region ap-south-1
```

### Create the permission policy

This is derived directly from what the application actually calls:

```bash
cat > /tmp/app-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "STSIdentity",
      "Effect": "Allow",
      "Action": ["sts:GetCallerIdentity"],
      "Resource": "*"
    },
    {
      "Sid": "TaggingAPI",
      "Effect": "Allow",
      "Action": [
        "tag:GetResources",
        "tag:GetTagKeys",
        "tag:GetTagValues",
        "tag:TagResources",
        "tag:UntagResources"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EC2Tags",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeVolumes",
        "ec2:DescribeSnapshots",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeTags",
        "ec2:CreateTags",
        "ec2:DeleteTags"
      ],
      "Resource": "*"
    },
    {
      "Sid": "S3Tags",
      "Effect": "Allow",
      "Action": [
        "s3:ListAllMyBuckets",
        "s3:GetBucketTagging",
        "s3:PutBucketTagging"
      ],
      "Resource": "*"
    },
    {
      "Sid": "LambdaTags",
      "Effect": "Allow",
      "Action": [
        "lambda:ListFunctions",
        "lambda:ListTags",
        "lambda:TagResource",
        "lambda:UntagResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "RDSTags",
      "Effect": "Allow",
      "Action": [
        "rds:DescribeDBInstances",
        "rds:DescribeDBClusters",
        "rds:ListTagsForResource",
        "rds:AddTagsToResource",
        "rds:RemoveTagsFromResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "FinOps",
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:GetTags",
        "ce:GetDimensionValues"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Logging",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name aws-tagging-utils-ec2-role \
  --policy-name aws-tagging-utils-app-policy \
  --policy-document file:///tmp/app-policy.json
```

### Create instance profile and attach

```bash
aws iam create-instance-profile \
  --instance-profile-name aws-tagging-utils-ec2-role

aws iam add-role-to-instance-profile \
  --instance-profile-name aws-tagging-utils-ec2-role \
  --role-name aws-tagging-utils-ec2-role
```

---

## Step 3 — SSH into the EC2 Instance

```bash
ssh -i ~/.ssh/YOUR_KEY.pem ec2-user@<EC2_PUBLIC_IP>
# For Ubuntu:
# ssh -i ~/.ssh/YOUR_KEY.pem ubuntu@<EC2_PUBLIC_IP>
```

---

## Step 4 — Install Docker on EC2

Run the provided bootstrap script, or execute these commands directly:

### Amazon Linux 2023

```bash
sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker

# Docker Compose plugin
ARCH=$(uname -m)
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL \
  "https://github.com/docker/compose/releases/download/v2.27.0/docker-compose-linux-${ARCH}" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
```

### Ubuntu 22.04

```bash
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release git
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker
```

### Verify installation

```bash
docker --version
docker compose version
```

---

## Step 5 — Copy the Application to EC2

### Option A: Clone from GitHub

```bash
git clone https://github.com/YOUR_ORG/aws-tagging-utils.git
cd aws-tagging-utils
```

### Option B: rsync from your local Mac

Run this on your **Mac** (not on EC2):

```bash
rsync -avz \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache' \
  --exclude '.ruff_cache' \
  --exclude '.mypy_cache' \
  --exclude 'app.db' \
  -e "ssh -i ~/.ssh/YOUR_KEY.pem" \
  /Users/opstree/Documents/github.io/aws-tagging-utils/ \
  ec2-user@<EC2_PUBLIC_IP>:~/aws-tagging-utils/
```

Then SSH back into EC2:

```bash
ssh -i ~/.ssh/YOUR_KEY.pem ec2-user@<EC2_PUBLIC_IP>
cd ~/aws-tagging-utils
```

---

## Step 6 — Create the `.env` File

```bash
# On the EC2 instance, inside ~/aws-tagging-utils/
cp .env.example .env
```

Edit `.env` with your actual values:

```bash
nano .env
```

### Minimum required changes

| Variable | What to set |
|---|---|
| `AWS_DEFAULT_REGION` | Your region, e.g. `ap-south-1` |
| `AUTH_MODE` | `local_dev` (for EC2 without ALB OIDC) |
| `DEV_AUTH_USER` | `admin:admin` (or your preferred username) |

### Full env variable reference

```bash
# ── REQUIRED ──────────────────────────────────────────────────────────
AWS_DEFAULT_REGION=ap-south-1

# Auth: local_dev bypasses OIDC. Use alb_oidc only when behind a real ALB.
AUTH_MODE=local_dev
DEV_AUTH_USER=admin:admin

# ── OPTIONAL (leave as-is for a working basic deployment) ─────────────

# SQLite DB path (docker-compose mounts a named volume here)
SQLITE_DB_PATH=/app/data/app.db

# Tag schema location (inside the container)
GOVERNANCE_SCHEMA_PATH=config/tag-schema.yaml
MANDATORY_TAGS=Owner,Environment

# Governance
GOVERNANCE_UNKNOWN_TAGS=warn
GOVERNANCE_STRICT_MODE=true
GOVERNANCE_NORMALIZATION=true
GOVERNANCE_REMEDIATION_ENABLED=true
GOVERNANCE_GRACE_PERIOD_DAYS=7
GOVERNANCE_TERMINATION_ENABLED=false
MAX_REMEDIATION_ATTEMPTS=3

# Worker (disable if no SQS queue is set up)
WORKER_ENABLED=false

# FinOps
FINOPS_ENABLED=true

# Multi-account
MULTI_ACCOUNT_ROLE_NAME=AWSOrganizationTagGovernanceRole

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json

# Boto retry
BOTO_MAX_RETRIES=5
BOTO_RETRY_MODE=adaptive

# ── LEAVE BLANK unless specifically needed ────────────────────────────
# GOVERNANCE_DYNAMODB_TABLE=TagGovernanceState
# GOVERNANCE_SNS_TOPIC_ARN=
# SQS_QUEUE_URL=
# REPORT_BUCKET=
# AUTH_ALB_ARN=
# AUTH_AWS_REGION=
```

---

## Step 7 — Verify AWS Access from EC2

Before starting the app, verify the EC2 instance profile is working:

```bash
aws sts get-caller-identity
```

Expected output (using instance profile):

```json
{
    "UserId": "AROAXXXXXXXXXXXXXXXXX:i-xxxxxxxxxxxxxxxxx",
    "Account": "123456789012",
    "Arn": "arn:aws:sts::123456789012:assumed-role/aws-tagging-utils-ec2-role/i-xxxxxxxxxxxxxxxxx"
}
```

> If this fails, the IAM instance profile is not attached correctly. Re-check Step 2.

---

## Step 8 — Build and Start with Docker Compose

```bash
cd ~/aws-tagging-utils

# Build the image and start all services in the background
docker compose up -d --build
```

This will:
1. Build the `aws-tagging-utils:latest` image from the Dockerfile
2. Start the `web` container on port 5050
3. Start the `mcp` container (MCP server)
4. Create a named Docker volume `sqlite_data` for SQLite persistence

### Watch the startup logs

```bash
docker compose logs -f
```

You should see:

```
aws-tagging-utils-web  | Configuration Validation: AWS reachable. Assumed identity: arn:aws:...
aws-tagging-utils-web  |  * Running on http://0.0.0.0:5050
```

---

## Step 9 — Verify Health

### From the EC2 instance

```bash
curl -s http://localhost:5050/health | python3 -m json.tool
```

Expected:

```json
{"status": "ok"}
```

### From your Mac

```bash
curl -s http://<EC2_PUBLIC_IP>:5050/health | python3 -m json.tool
```

---

## Step 10 — Test APIs

All APIs require the `X-Dev-User` header when `AUTH_MODE=local_dev` is set. The identity comes from `DEV_AUTH_USER`.

> ⚠️ In `local_dev` mode, auth is handled via the `DEV_AUTH_USER` env var — **no header is needed**. The identity is injected automatically.

### Dashboard

```bash
curl -s http://<EC2_PUBLIC_IP>:5050/api/dashboard | python3 -m json.tool
```

### Schema (tag-schema.yaml)

```bash
curl -s http://<EC2_PUBLIC_IP>:5050/api/schema | python3 -m json.tool
```

### Resource types

```bash
curl -s http://<EC2_PUBLIC_IP>:5050/api/meta/resource-types | python3 -m json.tool
```

### Trigger compliance refresh (async, hits real AWS)

```bash
curl -s -X POST http://<EC2_PUBLIC_IP>:5050/api/compliance/refresh | python3 -m json.tool
```

### Check compliance refresh status

```bash
curl -s http://<EC2_PUBLIC_IP>:5050/api/compliance/status | python3 -m json.tool
```

### Get compliance results (from cache)

```bash
curl -s http://<EC2_PUBLIC_IP>:5050/api/compliance | python3 -m json.tool
```

### Read tags from real AWS resource

```bash
curl -s -X POST http://<EC2_PUBLIC_IP>:5050/api/read \
  -H 'Content-Type: application/json' \
  -d '{"resource": "EC2Instance", "filters": {"Environment": "e2e"}}' \
  | python3 -m json.tool
```

### Write tags to a real AWS resource

```bash
curl -s -X POST http://<EC2_PUBLIC_IP>:5050/api/write \
  -H 'Content-Type: application/json' \
  -d '{
    "arn": "arn:aws:ec2:ap-south-1:123456789012:instance/i-xxxxxxxxx",
    "tags": {
      "Owner": "test-user",
      "Environment": "e2e",
      "Application": "aws-tagging-utils"
    }
  }' | python3 -m json.tool
```

### FinOps (Cost Explorer)

```bash
curl -s http://<EC2_PUBLIC_IP>:5050/api/finops | python3 -m json.tool
```

### Audit log

```bash
curl -s http://<EC2_PUBLIC_IP>:5050/api/audit | python3 -m json.tool
```

---

## Step 11 — Validate Persistence (Restart Test)

```bash
# Stop all containers
docker compose down

# Restart — SQLite data must survive in the named volume
docker compose up -d

# Check audit logs are still present
curl -s http://localhost:5050/api/audit | python3 -m json.tool

# Check compliance cache is still present
curl -s http://localhost:5050/api/compliance | python3 -m json.tool
```

> The named volume `sqlite_data` persists `/app/data/app.db` across restarts. If you ran `docker compose down -v` (with `-v`), the volume is deleted and data is lost. **Never use `-v` in production.**

---

## Step 12 — Check Container Status

```bash
# List running containers
docker compose ps

# Inspect container health
docker inspect aws-tagging-utils-web --format '{{.State.Health.Status}}'

# View web logs
docker compose logs web

# View MCP logs
docker compose logs mcp

# Tail live logs
docker compose logs -f web
```

---

## Step 13 — Run Unit Tests Inside the Container

```bash
docker compose exec web python -m pytest tests/ -m "not integration" -q
```

---

## Troubleshooting

### Container fails to start (port already in use)

```bash
sudo lsof -i :5050
# Kill the process using the port, or change the port mapping in docker-compose.yml
```

### `AWS credentials missing` in logs

- Check the IAM instance profile is attached to the EC2 instance
- On EC2: `curl http://169.254.169.254/latest/meta-data/iam/info` should return your role
- Verify: `aws sts get-caller-identity`

### `AUTH_MODE=alb_oidc` but no `x-amzn-oidc-data` header → 401

- Set `AUTH_MODE=local_dev` in `.env` for direct EC2 access without an ALB

### SQLite data lost after `docker compose down`

- Do NOT use `docker compose down -v`
- Check the volume: `docker volume inspect aws-tagging-utils_sqlite_data`

### `Operation not permitted` when Flask tries to bind port 80/443

- Flask is configured to use port 5050. Do not change to ports < 1024 without root.
- Use a reverse proxy (nginx) or ALB for standard HTTP/HTTPS ports.

---

## Environment Variable Quick Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `AWS_DEFAULT_REGION` | Yes | `us-east-2` | AWS region for all API calls |
| `AUTH_MODE` | Yes | `alb_oidc` | `local_dev` or `alb_oidc` |
| `DEV_AUTH_USER` | When `local_dev` | — | `username:Role1,Role2` |
| `AUTH_ALB_ARN` | When `alb_oidc` | — | Full ARN of the ALB |
| `AUTH_AWS_REGION` | When `alb_oidc` | `AWS_DEFAULT_REGION` | Region for ALB public key lookup |
| `AUTH_GROUPS_CLAIM` | No | `groups` | JWT claim holding IdP groups (e.g. `cognito:groups`) |
| `SQLITE_DB_PATH` | No | `/app/data/app.db` | SQLite file path inside container |
| `GOVERNANCE_SCHEMA_PATH` | No | `config/tag-schema.yaml` | Tag governance schema |
| `MANDATORY_TAGS` | No | `Owner` | Comma-separated mandatory tag keys |
| `GOVERNANCE_DYNAMODB_TABLE` | No | `TagGovernanceState` | DynamoDB table for state |
| `GOVERNANCE_SNS_TOPIC_ARN` | No | `` | SNS topic for notifications |
| `GOVERNANCE_REMEDIATION_ENABLED` | No | `true` | Enable auto-remediation |
| `GOVERNANCE_GRACE_PERIOD_DAYS` | No | `7` | Days before enforcement |
| `WORKER_ENABLED` | No | `false` | Enable SQS-based worker |
| `SQS_QUEUE_URL` | When worker | — | SQS queue URL |
| `FINOPS_ENABLED` | No | `true` | Enable Cost Explorer integration |
| `REPORT_BUCKET` | No | `` | S3 bucket for reports |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | No | `json` | `json` or `text` |
| `BOTO_MAX_RETRIES` | No | `5` | Max boto3 API retries |
| `BOTO_RETRY_MODE` | No | `adaptive` | `legacy`, `standard`, or `adaptive` |

---

## Architecture on EC2

```
Your Browser / curl
        │
        ▼ :5050
┌───────────────────────────────────────────┐
│           EC2 Instance                    │
│                                           │
│  ┌────────────────────────────────────┐  │
│  │  aws-tagging-utils-web (Docker)    │  │
│  │  Flask on 0.0.0.0:5050            │  │
│  │  AUTH_MODE=local_dev               │  │
│  └──────────────┬─────────────────────┘  │
│                 │ /app/data/app.db        │
│  ┌──────────────▼─────────────────────┐  │
│  │     sqlite_data (Docker volume)    │  │
│  └────────────────────────────────────┘  │
│                 │                         │
│  ┌──────────────▼─────────────────────┐  │
│  │  aws-tagging-utils-mcp (Docker)    │  │
│  │  MCP Server (stdio)                │  │
│  └────────────────────────────────────┘  │
│                                           │
│       IAM Instance Profile                │
│              │                            │
└──────────────┼────────────────────────────┘
               │
               ▼
         AWS APIs
    (STS, Tagging, EC2,
     S3, Lambda, CE...)
```
