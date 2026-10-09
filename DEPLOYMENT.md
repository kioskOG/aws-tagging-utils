# Enterprise AWS Tag Governance Platform

## Fully Executable Infrastructure Deployment Specification

**Version:** 1.0
**Status:** Production Implementation Specification
**Primary Region:** `ap-south-1`
**IaC:** Terraform + Terragrunt
**Multi-account onboarding:** CloudFormation StackSets
**Application:** Python / Flask
**Frontend:** Existing Vanilla JS / HTML / CSS
**Container Platform:** ECS Fargate
**State:** DynamoDB
**Events:** EventBridge + CloudTrail
**Notifications:** SNS
**Cost:** AWS Cost Explorer
**Secrets:** AWS Secrets Manager
**CI/CD:** GitHub Actions + AWS OIDC

---

# 1. Purpose

This document defines the exact infrastructure required to deploy the complete AWS Tag Governance platform.

It is intended to be executable by a junior DevOps engineer.

The engineer should be able to:

1. Clone the repository.
2. Configure AWS credentials.
3. Configure account IDs and environment values.
4. Run the documented Terragrunt commands.
5. Deploy the central governance infrastructure.
6. Deploy member-account resources through StackSets.
7. Build and deploy the Flask UI.
8. Configure GitHub OIDC.
9. Validate the complete event → governance → remediation → reporting → UI flow.

---

# 2. Important Assumptions

Replace these example values before production deployment.

```text
AWS Organization Management Account:
111111111111

Governance / Platform Account:
222222222222

Production Account:
333333333333

Development Account:
444444444444

QA Account:
555555555555

Primary Region:
ap-south-1

Company:
pathly

Platform:
tag-governance
```

These values must be centralized in Terragrunt configuration.

Do not hardcode them throughout Terraform.

---

# 3. Final Architecture

```text
                         AWS ORGANIZATION
                               |
                +--------------+--------------+
                |                             |
         Management Account             Governance Account
                |                             |
                |                    +--------+--------+
                |                    |        |        |
                |                   S3     DynamoDB   SNS
                |                    |        |        |
                |                    +--------+--------+
                |                             |
                |                      EventBridge Bus
                |                             |
                |                      Governance Lambda
                |                             |
                |          +------------------+----------------+
                |          |                  |                |
                |     Governance         Enforcement      FinOps
                |          |                  |                |
                |     Compliance        Remediation      Cost Explorer
                |          |                  |
                |       Exemptions          Audit
                |                             |
                |                       Flask API
                |                             |
                |                       ECS Fargate
                |                             |
                |                         ALB / HTTPS
                |                             |
                |                     Existing Vanilla UI
                |
        CloudFormation StackSets
                |
       +--------+--------+
       |        |        |
      Prod     Dev       QA
       |        |        |
   EventBridge Event Forwarder
   Governance Execution Role
   AWS Config integration
```

---

# 4. Repository Infrastructure Layout

Create the following infrastructure structure.

```text
infrastructure/
│
├── terragrunt.hcl
│
├── _env/
│   ├── common.hcl
│   ├── dev.hcl
│   ├── qa.hcl
│   └── prod.hcl
│
├── modules/
│   ├── governance-core/
│   ├── dynamodb/
│   ├── sns/
│   ├── eventbridge/
│   ├── iam/
│   ├── cloudtrail/
│   ├── config/
│   ├── finops/
│   ├── networking/
│   ├── ecr/
│   ├── ecs/
│   ├── alb/
│   ├── secrets/
│   ├── monitoring/
│   ├── github-oidc/
│   └── stackset/
│
├── live/
│   ├── governance/
│   │   └── ap-south-1/
│   │       ├── governance-core/
│   │       ├── dynamodb/
│   │       ├── sns/
│   │       ├── eventbridge/
│   │       ├── cloudtrail/
│   │       ├── iam/
│   │       ├── finops/
│   │       ├── networking/
│   │       ├── ecr/
│   │       ├── ecs/
│   │       ├── alb/
│   │       ├── secrets/
│   │       ├── monitoring/
│   │       └── github-oidc/
│   │
│   └── management/
│       └── ap-south-1/
│           └── stacksets/
│
└── stacksets/
    ├── governance-role.yaml
    ├── event-forwarder.yaml
    └── config.yaml
```

---

# 5. Terraform Module Responsibilities

## `modules/governance-core`

Creates:

* Lambda
* Lambda execution role
* Lambda log groups
* environment configuration
* optional DLQ

---

## `modules/dynamodb`

Creates:

```text
pathly-tg-prod-governance-state
pathly-tg-prod-exemptions
pathly-tg-prod-idempotency
```

---

## `modules/sns`

Creates:

```text
pathly-tg-prod-governance-alerts
pathly-tg-prod-security-alerts
pathly-tg-prod-remediation-alerts
```

---

## `modules/eventbridge`

Creates:

```text
pathly-tg-prod-events
```

and rules:

```text
pathly-tg-prod-governance-events
pathly-tg-prod-security-events
```

---

## `modules/iam`

Creates:

```text
pathly-tg-prod-governance-lambda
pathly-tg-prod-remediation-lambda
pathly-tg-prod-web
pathly-tg-prod-cross-account
```

---

## `modules/cloudtrail`

Creates/configures:

```text
pathly-tg-prod-org-trail
```

---

## `modules/networking`

Creates:

```text
VPC
Public subnets
Private subnets
Route tables
NAT Gateway
VPC endpoints
Security groups
```

---

## `modules/ecr`

Creates:

```text
pathly/tag-governance-web
```

---

## `modules/ecs`

Creates:

```text
pathly-tg-prod-cluster
pathly-tg-prod-web
```

---

## `modules/alb`

Creates:

```text
pathly-tg-prod-alb
```

---

## `modules/secrets`

Creates Secrets Manager entries.

---

## `modules/monitoring`

Creates:

* CloudWatch dashboards
* alarms
* log groups
* DLQ alarms
* ECS alarms
* Lambda alarms

---

## `modules/github-oidc`

Creates:

```text
pathly-tg-github-deploy
```

---

# 6. Global Naming Standard

All resources must follow:

```text
pathly-tg-<environment>-<component>
```

Examples:

```text
pathly-tg-prod-governance-state
pathly-tg-prod-exemptions
pathly-tg-prod-events
pathly-tg-prod-alerts
pathly-tg-prod-web
pathly-tg-prod-cluster
pathly-tg-prod-alb
pathly-tg-prod-api
```

---

# 7. Terraform Provider

Use:

```hcl
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Application = "tag-governance"
      Environment = var.environment
      Team        = "platform"
      ManagedBy   = "terraform"
    }
  }
}
```

Pin the provider version used by the repository rather than blindly upgrading an existing deployment.

---

# 8. Root Terraform Variables

Create:

```hcl
variable "aws_region" {
  type = string
}

variable "environment" {
  type = string
}

variable "governance_account_id" {
  type = string
}

variable "organization_id" {
  type = string
}

variable "domain_name" {
  type = string
}

variable "github_repository" {
  type = string
}
```

---

# 9. Terragrunt Root

`infrastructure/terragrunt.hcl`

```hcl
locals {
  company     = "pathly"
  platform    = "tag-governance"
  aws_region  = "ap-south-1"
  environment = get_env("TG_ENV", "dev")
}

generate "provider" {
  path      = "provider.tf"
  if_exists = "overwrite"

  contents = <<EOF
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "${local.aws_region}"

  default_tags {
    tags = {
      Application = "${local.platform}"
      Environment = "${local.environment}"
      Team        = "platform"
      ManagedBy   = "terraform"
    }
  }
}
EOF
}
```

---

# 10. Environment Configuration

Example:

`_env/prod.hcl`

```hcl
locals {
  environment = "prod"

  aws_region = "ap-south-1"

  governance_account_id = "222222222222"

  domain_name = "tag-governance.example.com"

  termination_enabled = false

  grace_period_days = 7

  log_retention_days = 30

  tags = {
    Application = "tag-governance"
    Environment = "prod"
    Team        = "platform"
    ManagedBy   = "terraform"
  }
}
```

---

# 11. Remote Terraform State

Use an S3 backend.

Example:

```text
pathly-terraform-state-prod
```

Enable:

* Versioning
* Encryption
* Restricted bucket policy
* Object ownership controls

Use separate state paths:

```text
tag-governance/prod/
tag-governance/qa/
tag-governance/dev/
```

Do not store state locally for production.

---

# 12. DynamoDB State Design

## Table 1

```text
pathly-tg-prod-governance-state
```

Primary key:

```text
PK
SK
```

Example:

```text
PK = RESOURCE#arn:aws:s3:::example
SK = VIOLATION#OwnerMissing
```

Attributes:

```text
account_id
region
resource_type
resource_id
violation_type
status
detected_at
deadline
exemption_id
remediation_action
last_action
last_error
updated_at
created_at
```

---

# 13. Governance State Terraform

```hcl
resource "aws_dynamodb_table" "governance_state" {
  name         = "${var.name}-governance-state"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "PK"
  range_key = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = true

  tags = var.tags
}
```

---

# 14. Exemption Table

```text
pathly-tg-prod-exemptions
```

Keys:

```text
PK = SCOPE#<scope>
SK = EXEMPTION#<id>
```

Attributes:

```text
reason
owner
created_by
created_at
expires_at
status
resource_id
account_id
scope
```

---

# 15. Idempotency Table

```text
pathly-tg-prod-idempotency
```

Keys:

```text
PK = EVENT#<event-id>
```

Attributes:

```text
processed_at
result
resource_id
operation
```

Use this to prevent duplicate EventBridge/CloudTrail processing.

---

# 16. SNS

Create:

```hcl
resource "aws_sns_topic" "governance" {
  name = "${var.name}-governance-alerts"
}

resource "aws_sns_topic" "security" {
  name = "${var.name}-security-alerts"
}

resource "aws_sns_topic" "remediation" {
  name = "${var.name}-remediation-alerts"
}
```

Use encryption where required by the organization's security baseline.

---

# 17. EventBridge Event Bus

Create:

```hcl
resource "aws_cloudwatch_event_bus" "governance" {
  name = "${var.name}-events"
}
```

Final bus:

```text
pathly-tg-prod-events
```

---

# 18. Member Account Event Forwarding

Each member account receives a StackSet containing:

```text
EventBridge Rule
      |
      v
Central Event Bus
```

The rule should only forward relevant CloudTrail events.

Do not forward every CloudTrail event.

---

# 19. Central Event Bus Policy

Allow only organization member accounts.

Example concept:

```hcl
resource "aws_cloudwatch_event_permission" "organization" {
  statement_id = "AllowOrganizationAccounts"

  principal = "*"

  action = "events:PutEvents"

  condition {
    key   = "aws:PrincipalOrgID"
    type  = "StringEquals"
    value = var.organization_id
  }
}
```

The exact Terraform resource/schema should be validated against the pinned AWS provider version during implementation.

---

# 20. EventBridge Governance Rule

Create:

```text
pathly-tg-prod-governance-events
```

Example event pattern:

```json
{
  "detail-type": [
    "AWS API Call via CloudTrail"
  ],
  "detail": {
    "eventSource": [
      "ec2.amazonaws.com",
      "s3.amazonaws.com",
      "rds.amazonaws.com",
      "lambda.amazonaws.com",
      "eks.amazonaws.com"
    ]
  }
}
```

Do not use this broad pattern blindly in production.

Prefer matching only APIs relevant to tag governance.

---

# 21. Tag Mutation Events

The initial event list should include the APIs actually supported by the implementation.

Examples:

```text
CreateTags
DeleteTags
TagResource
UntagResource
CreateBucket
CreateFunction
CreateDBInstance
CreateCluster
```

CloudTrail events are delivered to EventBridge and can be filtered by event source and event name.

---

# 22. EventBridge Lambda Target

```hcl
resource "aws_cloudwatch_event_target" "governance_lambda" {
  rule           = aws_cloudwatch_event_rule.governance.name
  event_bus_name = aws_cloudwatch_event_bus.governance.name
  target_id      = "governance-lambda"
  arn            = aws_lambda_function.governance.arn
}
```

Grant EventBridge permission to invoke Lambda.

---

# 23. DLQ

Create:

```text
pathly-tg-prod-governance-dlq
```

Use an SQS queue.

Configure EventBridge/Lambda asynchronous failure handling according to the processing path.

Alarm when:

```text
ApproximateNumberOfMessagesVisible > 0
```

---

# 24. CloudTrail

Create an organization trail:

```text
pathly-tg-prod-org-trail
```

Destination:

```text
pathly-tg-prod-cloudtrail
```

S3 prefix:

```text
cloudtrail/
```

Enable:

```text
Management events
```

Enable data events only where required because they can materially increase cost.

---

# 25. CloudTrail S3 Bucket

Example:

```text
pathly-tg-prod-cloudtrail-<account-id>
```

Bucket settings:

```text
Block Public Access = true
Versioning = true
Encryption = SSE-S3 or SSE-KMS
Object Ownership = Bucket owner enforced
```

---

# 26. IAM — Governance Lambda

Role:

```text
pathly-tg-prod-governance-lambda
```

Trust:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

---

# 27. Governance Lambda Policy

Base policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Logs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DynamoDB",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:Query"
      ],
      "Resource": [
        "arn:aws:dynamodb:ap-south-1:222222222222:table/pathly-tg-prod-governance-state",
        "arn:aws:dynamodb:ap-south-1:222222222222:table/pathly-tg-prod-exemptions",
        "arn:aws:dynamodb:ap-south-1:222222222222:table/pathly-tg-prod-idempotency"
      ]
    },
    {
      "Sid": "AssumeMemberRoles",
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::*:role/pathly-tg-governance-member"
    },
    {
      "Sid": "PublishNotifications",
      "Effect": "Allow",
      "Action": "sns:Publish",
      "Resource": [
        "arn:aws:sns:ap-south-1:222222222222:pathly-tg-prod-governance-alerts",
        "arn:aws:sns:ap-south-1:222222222222:pathly-tg-prod-security-alerts",
        "arn:aws:sns:ap-south-1:222222222222:pathly-tg-prod-remediation-alerts"
      ]
    }
  ]
}
```

Replace the account ID through Terraform interpolation.

---

# 28. Member Account Governance Role

Role name:

```text
pathly-tg-governance-member
```

Trust:

```text
Governance account
```

Example:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::222222222222:role/pathly-tg-prod-governance-lambda"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

---

# 29. Member Account Read Policy

Start with read-only discovery:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TagDiscovery",
      "Effect": "Allow",
      "Action": [
        "tag:GetResources",
        "tag:GetTagKeys",
        "tag:GetTagValues"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EC2Read",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeTags",
        "ec2:DescribeVolumes",
        "ec2:DescribeSecurityGroups"
      ],
      "Resource": "*"
    },
    {
      "Sid": "S3Read",
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketTagging",
        "s3:ListAllMyBuckets"
      ],
      "Resource": "*"
    },
    {
      "Sid": "RDSRead",
      "Effect": "Allow",
      "Action": [
        "rds:DescribeDBInstances",
        "rds:ListTagsForResource"
      ],
      "Resource": "*"
    }
  ]
}
```

Add service permissions only for services actually supported by `TagRead`.

---

# 30. Member Account Remediation Policy

Do not deploy remediation permissions initially.

Start with:

```text
READ_ONLY
```

After successful validation, add only required actions.

Example:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EC2TagRemediation",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateTags",
        "ec2:DeleteTags"
      ],
      "Resource": "*"
    },
    {
      "Sid": "S3TagRemediation",
      "Effect": "Allow",
      "Action": [
        "s3:PutBucketTagging",
        "s3:DeleteBucketTagging"
      ],
      "Resource": "*"
    },
    {
      "Sid": "LambdaTagRemediation",
      "Effect": "Allow",
      "Action": [
        "lambda:TagResource",
        "lambda:UntagResource"
      ],
      "Resource": "*"
    }
  ]
}
```

Do not add resource termination permissions until explicitly approved.

---

# 31. Termination Permissions

These should be isolated.

Do NOT add:

```text
ec2:TerminateInstances
rds:DeleteDBInstance
eks:DeleteCluster
```

to the normal governance role.

If termination is eventually required, create a separate role:

```text
pathly-tg-termination
```

and require an explicit feature flag plus backend authorization.

---

# 32. StackSet Strategy

Use **service-managed StackSets** because the accounts are in AWS Organizations.

This removes the need to manually maintain StackSet administration/execution roles. AWS documents that service-managed StackSets integrate with Organizations and can automatically deploy to accounts added to target OUs.

Before using this:

```text
AWS Organizations:
All Features enabled

CloudFormation StackSets:
Trusted Access enabled
```

Trusted access is required for service-managed StackSets.

---

# 33. StackSet 1 — Governance Role

File:

```text
stacksets/governance-role.yaml
```

Template:

```yaml
AWSTemplateFormatVersion: "2010-09-09"

Description: >
  AWS Tag Governance cross-account execution role.

Parameters:

  GovernanceAccountId:
    Type: String

Resources:

  GovernanceRole:
    Type: AWS::IAM::Role

    Properties:
      RoleName: pathly-tg-governance-member

      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              AWS:
                - !Sub arn:aws:iam::${GovernanceAccountId}:role/pathly-tg-prod-governance-lambda
            Action:
              - sts:AssumeRole

      ManagedPolicyArns:
        - !Ref GovernanceReadPolicy

      Tags:
        - Key: Application
          Value: tag-governance
        - Key: ManagedBy
          Value: cloudformation-stackset

  GovernanceReadPolicy:
    Type: AWS::IAM::ManagedPolicy

    Properties:
      ManagedPolicyName: pathly-tg-governance-read

      PolicyDocument:
        Version: "2012-10-17"

        Statement:

          - Sid: TagDiscovery
            Effect: Allow
            Action:
              - tag:GetResources
              - tag:GetTagKeys
              - tag:GetTagValues
            Resource: "*"

          - Sid: EC2Discovery
            Effect: Allow
            Action:
              - ec2:DescribeInstances
              - ec2:DescribeTags
              - ec2:DescribeVolumes
            Resource: "*"

          - Sid: S3Discovery
            Effect: Allow
            Action:
              - s3:GetBucketTagging
              - s3:ListAllMyBuckets
            Resource: "*"

          - Sid: RDSDiscovery
            Effect: Allow
            Action:
              - rds:DescribeDBInstances
              - rds:ListTagsForResource
            Resource: "*"
```

---

# 34. StackSet 2 — Event Forwarder

File:

```text
stacksets/event-forwarder.yaml
```

Purpose:

```text
Member Account
      |
      v
Default EventBridge Bus
      |
      v
Selected CloudTrail Events
      |
      v
Governance Account Event Bus
```

Parameters:

```yaml
Parameters:

  CentralEventBusArn:
    Type: String

  OrganizationId:
    Type: String
```

Rule:

```yaml
Resources:

  GovernanceEventRule:
    Type: AWS::Events::Rule

    Properties:
      Name: pathly-tg-forward-governance-events

      EventPattern:
        detail-type:
          - AWS API Call via CloudTrail

        detail:
          eventSource:
            - ec2.amazonaws.com
            - s3.amazonaws.com
            - rds.amazonaws.com
            - lambda.amazonaws.com
            - eks.amazonaws.com

          eventName:
            - CreateTags
            - DeleteTags
            - TagResource
            - UntagResource

      Targets:

        - Id: CentralGovernanceBus

          Arn: !Ref CentralEventBusArn

          RoleArn: !GetAtt EventBridgeForwardRole.Arn
```

The actual API list must be generated from the services supported by the project's `TagWriter` and `TagRead`.

---

# 35. StackSet Event Forwarder Role

```yaml
  EventBridgeForwardRole:
    Type: AWS::IAM::Role

    Properties:

      RoleName: pathly-tg-event-forwarder

      AssumeRolePolicyDocument:

        Version: "2012-10-17"

        Statement:

          - Effect: Allow

            Principal:
              Service:
                - events.amazonaws.com

            Action:
              - sts:AssumeRole

      Policies:

        - PolicyName: PutEventsCentralBus

          PolicyDocument:

            Version: "2012-10-17"

            Statement:

              - Effect: Allow

                Action:
                  - events:PutEvents

                Resource:
                  - !Ref CentralEventBusArn
```

---

# 36. StackSet 3 — Config

File:

```text
stacksets/config.yaml
```

Create:

```text
Configuration Recorder
Delivery Channel
Required governance configuration
```

Do not automatically deploy dozens of Config rules.

The centralized governance engine should remain the primary policy evaluator.

AWS Config provides the detective/configuration layer.

---

# 37. StackSet Deployment

From the Organizations management account:

```bash
export AWS_PROFILE=org-management
export AWS_REGION=ap-south-1
```

Verify:

```bash
aws sts get-caller-identity
```

Verify Organizations:

```bash
aws organizations describe-organization
```

---

# 38. Enable StackSet Trusted Access

Use AWS CloudFormation StackSets with service-managed permissions.

Verify:

```bash
aws cloudformation list-stack-sets \
  --region ap-south-1
```

If trusted access is not enabled, enable it from the Organizations/CloudFormation management account.

---

# 39. Create Governance StackSet

Upload the template to a controlled deployment bucket or use the supported template source.

Example:

```bash
aws cloudformation create-stack-set \
  --stack-set-name pathly-tg-governance-role \
  --template-body file://stacksets/governance-role.yaml \
  --permission-model SERVICE_MANAGED \
  --auto-deployment Enabled=true,RetainStacksOnAccountRemoval=true \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=GovernanceAccountId,ParameterValue=222222222222 \
  --region ap-south-1
```

Service-managed StackSets support targeting OUs and automatic deployment to future accounts.

---

# 40. Deploy StackSet to Production OU

First retrieve OU ID:

```bash
aws organizations list-organizational-units-for-parent \
  --parent-id <ROOT-ID>
```

Then:

```bash
aws cloudformation create-stack-instances \
  --stack-set-name pathly-tg-governance-role \
  --deployment-targets OrganizationalUnitIds=<PRODUCTION_OU_ID> \
  --regions ap-south-1 \
  --operation-preferences \
    RegionConcurrencyType=PARALLEL,MaxConcurrentCount=2,FailureToleranceCount=0 \
  --region ap-south-1
```

---

# 41. Verify StackSet

```bash
aws cloudformation list-stack-instances \
  --stack-set-name pathly-tg-governance-role \
  --region ap-south-1
```

Every account should become:

```text
CURRENT
```

Do not proceed if production accounts are:

```text
FAILED
OUTDATED
INOPERABLE
```

---

# 42. Central Event Bus ARN

Example:

```text
arn:aws:events:ap-south-1:222222222222:event-bus/pathly-tg-prod-events
```

Pass this ARN into the event-forwarder StackSet.

---

# 43. Event Bus Resource Policy

The central event bus must permit member accounts to call:

```text
events:PutEvents
```

but only from the organization's accounts.

Use an organization condition such as:

```text
aws:PrincipalOrgID
```

rather than allowing arbitrary external accounts.

---

# 44. Lambda Packaging

The governance Lambda should be packaged from the existing Python application.

Recommended:

```text
build/
├── package/
├── lambda.zip
```

Build:

```bash
rm -rf build
mkdir -p build/package

pip install \
  -r requirements.txt \
  -t build/package

cp -R src build/package/

cd build/package
zip -r ../lambda.zip .
cd ../..
```

If the application has native dependencies, use a Lambda-compatible build image or Lambda container image instead.

---

# 45. Governance Lambda

Name:

```text
pathly-tg-prod-governance
```

Environment:

```text
ENVIRONMENT=prod
AWS_REGION=ap-south-1

GOVERNANCE_STATE_TABLE=pathly-tg-prod-governance-state
EXEMPTIONS_TABLE=pathly-tg-prod-exemptions
IDEMPOTENCY_TABLE=pathly-tg-prod-idempotency

GOVERNANCE_TOPIC_ARN=...
SECURITY_TOPIC_ARN=...
REMEDIATION_TOPIC_ARN=...

TERMINATION_ENABLED=false
GRACE_PERIOD_DAYS=7
```

---

# 46. Lambda Memory/Timeout

Initial production baseline:

```text
Memory:
1024 MB

Timeout:
60 seconds

Reserved concurrency:
evaluate based on event volume
```

Do not guess final capacity.

Load test before production.

---

# 47. Lambda Environment Secrets

Never place:

```text
password
API token
private key
```

in environment variables.

Use Secrets Manager.

Environment variables should contain:

```text
table names
ARNs
feature flags
region
environment
configuration paths
```

---

# 48. Secrets Manager

Create:

```text
/tag-governance/prod/web
/tag-governance/prod/cmdb
/tag-governance/prod/auth
```

Example web secret:

```json
{
  "session_secret": "GENERATED_SECRET"
}
```

Example CMDB:

```json
{
  "base_url": "https://servicenow.example.com",
  "username": "..."
}
```

Do not commit actual values.

---

# 49. Secret Creation

Generate a secret:

```bash
openssl rand -base64 48
```

Create:

```bash
aws secretsmanager create-secret \
  --name /tag-governance/prod/web \
  --secret-string file://web-secret.json \
  --region ap-south-1
```

For production, prefer Terraform-managed secret metadata with secret values injected through a secure deployment process rather than committing secret values to `.tfvars`.

---

# 50. VPC

Example:

```text
VPC:
10.50.0.0/16
```

Public:

```text
10.50.10.0/24
10.50.11.0/24
```

Private:

```text
10.50.20.0/24
10.50.21.0/24
10.50.22.0/24
```

Use at least two Availability Zones.

---

# 51. Internet Gateway

Create:

```text
pathly-tg-prod-igw
```

Attach to:

```text
10.50.0.0/16
```

---

# 52. NAT Gateway

Create one NAT Gateway per AZ for production if high availability is required.

Example:

```text
NAT-AZ1
NAT-AZ2
```

Development may use a single NAT Gateway to reduce cost.

---

# 53. VPC Endpoints

Create endpoints for:

```text
S3
DynamoDB
ECR API
ECR DKR
CloudWatch Logs
Secrets Manager
STS
```

Use gateway endpoints for S3/DynamoDB where applicable.

Use interface endpoints for services that require them.

---

# 54. ECS Cluster

Name:

```text
pathly-tg-prod-cluster
```

Enable Container Insights if the monitoring/cost tradeoff is acceptable.

---

# 55. ECR

Repository:

```text
pathly/tag-governance-web
```

Enable:

```text
Image scanning
Lifecycle policy
Encryption
```

Lifecycle example:

```text
Keep last 30 tagged images
Expire untagged images after 7 days
```

---

# 56. Dockerfile

The application should expose Flask through a production WSGI server.

Example:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY web ./web

EXPOSE 8080

CMD [
  "gunicorn",
  "--bind",
  "0.0.0.0:8080",
  "--workers",
  "2",
  "--threads",
  "4",
  "web.app:app"
]
```

Validate the actual Flask import path before using this exact command.

---

# 57. Container Health Endpoint

Add:

```text
GET /health
```

Response:

```json
{
  "status": "ok"
}
```

Do not make the health endpoint dependent on Cost Explorer, DynamoDB, or every AWS service.

A health check should answer whether the web application process is alive.

---

# 58. ECS Task

Initial baseline:

```text
CPU:
512

Memory:
1024 MB

Container Port:
8080
```

Increase based on load testing.

---

# 59. ECS Task Role

The ECS task role should allow only:

```text
dynamodb:GetItem
dynamodb:Query
dynamodb:Scan
secretsmanager:GetSecretValue
ce:GetCostAndUsage
ce:GetDimensionValues
ce:GetTags
```

plus any read operations required by the reporting implementation.

Do not give:

```text
AdministratorAccess
```

---

# 60. ECS Execution Role

The execution role requires:

```text
ecr:GetAuthorizationToken
ecr:BatchCheckLayerAvailability
ecr:GetDownloadUrlForLayer
ecr:BatchGetImage

logs:CreateLogStream
logs:PutLogEvents
```

---

# 61. Application Security Group

Name:

```text
pathly-tg-prod-web-sg
```

Inbound:

```text
TCP 8080
Source = ALB Security Group
```

Outbound:

```text
TCP 443
Destination = required AWS endpoints/NAT
```

Do not allow internet ingress directly to port 8080.

---

# 62. ALB

Name:

```text
pathly-tg-prod-alb
```

Listeners:

```text
443 HTTPS
```

Optional:

```text
80 -> redirect to 443
```

Target group:

```text
pathly-tg-prod-web-tg
```

Health check:

```text
Path:
/health

Port:
8080

Protocol:
HTTP
```

---

# 63. ACM

Create certificate:

```text
tag-governance.example.com
```

Use DNS validation.

Attach certificate to:

```text
ALB HTTPS listener
```

Do not terminate TLS inside Flask unless there is a specific reason.

---

# 64. Route53

Create:

```text
tag-governance.example.com
```

Alias:

```text
ALB
```

---

# 65. Flask API Routes

Implement:

```text
GET /api/dashboard
GET /api/compliance
GET /api/finops
GET /api/security
GET /api/remediation
GET /api/exemptions
GET /api/audit
GET /api/schema
GET /api/organization
GET /api/cicd
```

Mutating routes must use authenticated backend authorization.

---

# 66. Flask CORS

If UI and API are served by the same Flask application:

```text
CORS:
disabled
```

Prefer same-origin deployment.

Do not introduce permissive:

```text
Access-Control-Allow-Origin: *
```

for authenticated production APIs.

---

# 67. RBAC

Define application roles:

```text
Viewer
TagOperator
ApplicationOwner
FinOps
SecurityAdmin
PlatformAdmin
```

Backend authorization must determine permissions.

Frontend role selection may only be used for development/demo mode.

Never use a browser-selected persona as real authorization.

---

# 68. FinOps Permissions

The ECS/Lambda reporting role should receive:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:GetDimensionValues",
        "ce:GetTags"
      ],
      "Resource": "*"
    }
  ]
}
```

Cost Explorer permissions and Cost Allocation Tag activation are separate billing/configuration concerns.

---

# 69. Cost Allocation Tags

Activate:

```text
Application
Environment
Team
CostCenter
Owner
```

Only activate tags that are actually used for FinOps reporting.

Do not assume a tag becomes immediately useful for historical cost attribution after activation.

---

# 70. FinOps Reporting Contract

The API response should resemble:

```json
{
  "period": {
    "start": "2026-09-01",
    "end": "2026-09-30"
  },
  "currency": "USD",
  "total_spend": 10000,
  "tagged_spend": 8200,
  "potentially_unallocated_spend": 1800,
  "allocation_percentage": 82,
  "attribution": {
    "actual_aws_reported": 8200,
    "estimated": 0,
    "unallocated": 1800
  }
}
```

Do not represent estimated values as actual AWS-reported spend.

---

# 71. Security / Drift API

Example:

```json
{
  "resource_id": "i-012345",
  "account_id": "333333333333",
  "region": "ap-south-1",
  "tag": "Owner",
  "expected": "platform",
  "actual": "unknown",
  "changed_by": "arn:aws:iam::...",
  "changed_at": "2026-09-12T08:30:00Z",
  "status": "PROTECTED_VIOLATION"
}
```

---

# 72. Audit Record

Every mutation should produce:

```json
{
  "timestamp": "2026-09-12T08:30:00Z",
  "action": "TAG_REMEDIATION",
  "actor": "governance-engine",
  "resource_id": "arn:aws:...",
  "account_id": "333333333333",
  "result": "SUCCESS",
  "correlation_id": "event-123"
}
```

---

# 73. CloudWatch Log Groups

Create:

```text
/aws/tag-governance/prod/governance
/aws/tag-governance/prod/remediation
/aws/tag-governance/prod/web
/aws/tag-governance/prod/finops
/aws/tag-governance/prod/security
```

Retention:

```text
30 days
```

Export long-term audit data to S3 if required.

---

# 74. CloudWatch Alarms

Minimum alarms:

```text
GovernanceLambdaErrors
GovernanceLambdaThrottles
GovernanceDLQMessages
RemediationFailures
CrossAccountFailures
FinOpsFailures
Web5xx
WebTargetUnhealthy
DynamoDBThrottles
```

---

# 75. CloudWatch Dashboard

Dashboard:

```text
pathly-tg-prod
```

Widgets:

```text
Compliance %
Violations
Remediations
Protected Tag Violations
Exemptions
Cross-account failures
Lambda errors
DLQ
API 5xx
API latency
FinOps query failures
```

---

# 76. GitHub OIDC

Create:

```text
pathly-tg-github-deploy
```

Trust GitHub's OIDC provider.

The trust policy must restrict:

```text
token.actions.githubusercontent.com:aud
```

to:

```text
sts.amazonaws.com
```

and restrict:

```text
token.actions.githubusercontent.com:sub
```

to the exact repository/environment.

Example:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::222222222222:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": [
            "repo:YOUR_ORG/YOUR_REPO:ref:refs/heads/main"
          ]
        }
      }
    }
  ]
}
```

For production, prefer GitHub Environments and restrict the subject accordingly.

---

# 77. GitHub Deployment Policy

Do not give GitHub:

```text
AdministratorAccess
```

Split roles:

```text
pathly-tg-github-plan
pathly-tg-github-deploy-dev
pathly-tg-github-deploy-prod
```

Production role should require GitHub environment approval.

---

# 78. GitHub Actions Authentication

Example:

```yaml
permissions:
  id-token: write
  contents: read

steps:

  - uses: actions/checkout@v4

  - name: Configure AWS
    uses: aws-actions/configure-aws-credentials@v4
    with:
      role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
      aws-region: ap-south-1
```

Do not configure:

```yaml
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
```

---

# 79. Deployment Pipeline

Recommended:

```text
Pull Request
    |
    +--> Python tests
    +--> Governance tests
    +--> Terraform fmt
    +--> Terraform validate
    +--> Terragrunt plan
    +--> Security scan
    |
    v
Merge
    |
    v
Build Docker
    |
    v
Push ECR
    |
    v
Deploy Dev
    |
    v
Integration Tests
    |
    v
Deploy QA
    |
    v
Approval
    |
    v
Deploy Prod
```

---

# 80. Exact Deployment Sequence

The following sequence is the canonical deployment order.

## Step 1 — Clone

```bash
git clone <repository-url>
cd infrastructure
```

---

## Step 2 — Authenticate

```bash
aws sso login --profile org-management
```

Verify:

```bash
AWS_PROFILE=org-management \
aws sts get-caller-identity
```

---

# 81. Step 3 — Configure Variables

Create:

```text
_env/prod.hcl
```

Set:

```text
organization_id
governance_account_id
production_ou_id
region
domain
GitHub repository
```

---

# 82. Step 4 — Bootstrap Terraform State

Deploy the state infrastructure first.

```bash
cd live/governance/ap-south-1/bootstrap

TG_ENV=prod terragrunt init
TG_ENV=prod terragrunt plan
TG_ENV=prod terragrunt apply
```

If bootstrap is managed separately, run its dedicated Terraform module.

---

# 83. Step 5 — Deploy Networking

```bash
cd ../networking

TG_ENV=prod terragrunt init
TG_ENV=prod terragrunt plan
TG_ENV=prod terragrunt apply
```

Verify:

```bash
aws ec2 describe-vpcs \
  --filters Name=tag:Application,Values=tag-governance
```

---

# 84. Step 6 — Deploy DynamoDB

```bash
cd ../dynamodb

TG_ENV=prod terragrunt plan
TG_ENV=prod terragrunt apply
```

Verify:

```bash
aws dynamodb list-tables \
  --region ap-south-1
```

---

# 85. Step 7 — Deploy SNS

```bash
cd ../sns

TG_ENV=prod terragrunt apply
```

Verify:

```bash
aws sns list-topics \
  --region ap-south-1
```

---

# 86. Step 8 — Deploy IAM

```bash
cd ../iam

TG_ENV=prod terragrunt plan
TG_ENV=prod terragrunt apply
```

Verify:

```bash
aws iam get-role \
  --role-name pathly-tg-prod-governance-lambda
```

---

# 87. Step 9 — Deploy ECR

```bash
cd ../ecr

TG_ENV=prod terragrunt apply
```

Verify:

```bash
aws ecr describe-repositories \
  --repository-names pathly/tag-governance-web \
  --region ap-south-1
```

---

# 88. Step 10 — Deploy EventBridge

```bash
cd ../eventbridge

TG_ENV=prod terragrunt plan
TG_ENV=prod terragrunt apply
```

Verify:

```bash
aws events list-event-buses \
  --region ap-south-1
```

---

# 89. Step 11 — Deploy CloudTrail

```bash
cd ../cloudtrail

TG_ENV=prod terragrunt plan
TG_ENV=prod terragrunt apply
```

Verify:

```bash
aws cloudtrail describe-trails \
  --region ap-south-1
```

---

# 90. Step 12 — Deploy Governance Lambda

Build:

```bash
cd ../../../
./scripts/build-lambda.sh
```

Then:

```bash
cd infrastructure/live/governance/ap-south-1/governance-core

TG_ENV=prod terragrunt apply
```

Verify:

```bash
aws lambda list-functions \
  --region ap-south-1
```

---

# 91. Step 13 — Deploy StackSets

From the management account:

```bash
cd infrastructure/live/management/ap-south-1/stacksets
```

Apply the StackSet infrastructure:

```bash
TG_ENV=prod terragrunt plan
TG_ENV=prod terragrunt apply
```

Then verify:

```bash
aws cloudformation list-stack-sets \
  --region ap-south-1
```

---

# 92. Step 14 — Deploy Member Accounts

Deploy first to:

```text
Development OU
```

Do not start with Production.

Verify:

```bash
aws cloudformation list-stack-instances \
  --stack-set-name pathly-tg-governance-role \
  --region ap-south-1
```

---

# 93. Step 15 — Test Cross Account

Assume:

```bash
aws sts assume-role \
  --role-arn arn:aws:iam::444444444444:role/pathly-tg-governance-member \
  --role-session-name governance-test
```

Verify the role works.

Then test resource discovery.

---

# 94. Step 16 — Deploy Config

Deploy Config StackSet to Development.

Verify:

```bash
aws configservice describe-configuration-recorders \
  --region ap-south-1
```

---

# 95. Step 17 — Run Read-Only Mode

Set:

```text
TERMINATION_ENABLED=false
REMEDIATION_ENABLED=false
```

Run the platform.

Verify resources appear.

---

# 96. Step 18 — Test Event Flow

Create a test tag change.

Example:

```bash
aws ec2 create-tags \
  --resources <TEST_INSTANCE_ID> \
  --tags Key=Owner,Value=test
```

Expected:

```text
CloudTrail
    |
EventBridge
    |
Central Event Bus
    |
Governance Lambda
    |
DynamoDB
    |
Audit
```

Check logs:

```bash
aws logs tail \
  /aws/tag-governance/prod/governance \
  --follow \
  --region ap-south-1
```

---

# 97. Step 19 — Test Notification

Trigger a known test violation.

Verify SNS delivery.

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn <TOPIC_ARN>
```

---

# 98. Step 20 — Test Remediation

Only after read-only validation:

```text
REMEDIATION_ENABLED=true
```

Keep:

```text
TERMINATION_ENABLED=false
```

Create a test violation.

Verify:

```text
Violation
    |
    v
State
    |
    v
Remediation
    |
    v
Resource fixed
    |
    v
Audit
```

---

# 99. Step 21 — Test Exemption

Create an exemption.

Verify:

```text
Violation -> EXEMPT
```

Verify remediation does not run.

---

# 100. Step 22 — Deploy FinOps

Deploy:

```bash
cd infrastructure/live/governance/ap-south-1/finops

TG_ENV=prod terragrunt apply
```

Verify IAM.

Then query:

```text
Last 30 days
```

Compare results against AWS Cost Explorer.

---

# 101. Step 23 — Deploy ECS

Build image:

```bash
docker build \
  -t pathly/tag-governance-web:$(git rev-parse --short HEAD) \
  .
```

Authenticate:

```bash
aws ecr get-login-password \
  --region ap-south-1 |
docker login \
  --username AWS \
  --password-stdin \
  222222222222.dkr.ecr.ap-south-1.amazonaws.com
```

Tag:

```bash
docker tag \
  pathly/tag-governance-web:<TAG> \
  222222222222.dkr.ecr.ap-south-1.amazonaws.com/pathly/tag-governance-web:<TAG>
```

Push:

```bash
docker push \
  222222222222.dkr.ecr.ap-south-1.amazonaws.com/pathly/tag-governance-web:<TAG>
```

---

# 102. Step 24 — Deploy ECS Infrastructure

```bash
cd infrastructure/live/governance/ap-south-1/ecs

TG_ENV=prod terragrunt plan
TG_ENV=prod terragrunt apply
```

Verify:

```bash
aws ecs list-services \
  --cluster pathly-tg-prod-cluster \
  --region ap-south-1
```

---

# 103. Step 25 — Deploy ALB

```bash
cd ../alb

TG_ENV=prod terragrunt apply
```

Verify target health:

```bash
aws elbv2 describe-target-health \
  --target-group-arn <TARGET_GROUP_ARN> \
  --region ap-south-1
```

Expected:

```text
healthy
```

---

# 104. Step 26 — DNS

Create Route53 alias:

```text
tag-governance.example.com
        |
        v
ALB
```

Test:

```bash
curl -I https://tag-governance.example.com/health
```

Expected:

```text
HTTP/2 200
```

---

# 105. Step 27 — UI Verification

Open:

```text
https://tag-governance.example.com
```

Verify:

```text
Dashboard
Governance
Compliance
Enforcement
Organization
FinOps
Security
CI/CD
```

---

# 106. Step 28 — Deploy GitHub OIDC

```bash
cd infrastructure/live/governance/ap-south-1/github-oidc

TG_ENV=prod terragrunt plan
TG_ENV=prod terragrunt apply
```

Verify:

```bash
aws iam get-role \
  --role-name pathly-tg-github-deploy
```

---

# 107. Step 29 — GitHub Deployment Test

Create a PR.

Verify:

```text
Terraform fmt
Terraform validate
Terragrunt plan
Python tests
Governance tests
Security scan
```

Merge to development branch.

Verify deployment.

Production requires approval.

---

# 108. Step 30 — Production Member Account Onboarding

Only after Development succeeds:

```text
Development
   |
   v
QA
   |
   v
Production
```

Deploy StackSet to QA.

Validate.

Then Production.

---

# 109. Production Enforcement

Production starts:

```text
TERMINATION_ENABLED=false
REMEDIATION_ENABLED=false
```

Then:

```text
REMEDIATION_ENABLED=true
```

Only after successful observation should remediation be enabled.

Termination must remain separately controlled.

---

# 110. Production Termination Gate

Termination requires an explicit change.

Example:

```hcl
termination_enabled = false
```

Changing to:

```hcl
termination_enabled = true
```

must require:

* PR
* security review
* platform approval
* production approval
* test evidence

Never make this a UI-only toggle.

---

# 111. Required Terraform Outputs

The infrastructure should expose:

```hcl
output "governance_state_table_name" {
  value = aws_dynamodb_table.governance_state.name
}

output "exemptions_table_name" {
  value = aws_dynamodb_table.exemptions.name
}

output "event_bus_arn" {
  value = aws_cloudwatch_event_bus.governance.arn
}

output "governance_lambda_arn" {
  value = aws_lambda_function.governance.arn
}

output "governance_alert_topic_arn" {
  value = aws_sns_topic.governance.arn
}

output "web_alb_dns_name" {
  value = aws_lb.web.dns_name
}

output "web_ecr_repository_url" {
  value = aws_ecr_repository.web.repository_url
}
```

These outputs should feed dependent Terragrunt configurations.

---

# 112. No Hardcoded ARNs

Do not write:

```text
arn:aws:dynamodb:ap-south-1:222222222222:...
```

inside application code.

Terraform should generate them.

Use:

```hcl
aws_dynamodb_table.governance_state.arn
```

or Terragrunt dependency outputs.

---

# 113. Terragrunt Dependencies

Example:

```hcl
dependency "dynamodb" {
  config_path = "../dynamodb"
}

dependency "eventbridge" {
  config_path = "../eventbridge"
}

dependency "sns" {
  config_path = "../sns"
}
```

Then:

```hcl
inputs = {
  governance_state_table =
    dependency.dynamodb.outputs.governance_state_table_name

  event_bus_arn =
    dependency.eventbridge.outputs.event_bus_arn

  governance_topic_arn =
    dependency.sns.outputs.governance_topic_arn
}
```

---

# 114. Environment Isolation

Never allow:

```text
dev Terraform
```

to accidentally deploy into:

```text
prod
```

Use separate:

```text
AWS account
state
IAM role
Terraform state
variables
```

for production.

---

# 115. Final Resource Inventory

The production environment should contain approximately:

## Governance Account

```text
S3
  pathly-tg-prod-cloudtrail
  pathly-tg-prod-audit

DynamoDB
  pathly-tg-prod-governance-state
  pathly-tg-prod-exemptions
  pathly-tg-prod-idempotency

SNS
  pathly-tg-prod-governance-alerts
  pathly-tg-prod-security-alerts
  pathly-tg-prod-remediation-alerts

EventBridge
  pathly-tg-prod-events
  governance rules

Lambda
  pathly-tg-prod-governance

ECR
  pathly/tag-governance-web

ECS
  pathly-tg-prod-cluster
  pathly-tg-prod-web

ALB
  pathly-tg-prod-alb

Secrets Manager
  /tag-governance/prod/web
  /tag-governance/prod/cmdb
  /tag-governance/prod/auth

CloudWatch
  Logs
  Dashboard
  Alarms

IAM
  Governance Lambda
  ECS Task
  ECS Execution
  GitHub Deployment
```

---

# 116. Member Account Inventory

Each governed account should receive:

```text
IAM
  pathly-tg-governance-member
  pathly-tg-event-forwarder

EventBridge
  pathly-tg-forward-governance-events

AWS Config
  Configuration Recorder
  Delivery Channel

CloudTrail
  Organization trail participation
```

---

# 117. Final End-to-End Test

The final test must prove:

```text
Developer changes resource
        |
        v
AWS API
        |
        v
CloudTrail
        |
        v
EventBridge
        |
        v
Central Event Bus
        |
        v
Governance Lambda
        |
        v
TagGovernanceEngine
        |
        +--> compliant
        |
        +--> violation
                |
                v
           DynamoDB State
                |
        +-------+-------+
        |               |
        v               v
      Audit          Notification
        |
        v
     Remediation
        |
        v
   Resource corrected
        |
        v
     Audit update
        |
        v
     Flask API
        |
        v
   Existing UI
```

---

# 118. Final FinOps Test

```text
AWS Billing / Cost Data
        |
        v
Cost Explorer
        |
        v
FinOpsReportGenerator
        |
        v
Flask API
        |
        v
Existing UI
```

Verify:

```text
Total Spend
Tagged Spend
Potentially Unallocated Spend
Allocation %
Account breakdown
Service breakdown
Environment breakdown
Cost Center breakdown
```

---

# 119. Final Security Test

Change:

```text
Owner=platform
```

to:

```text
Owner=unknown
```

Expected:

```text
CloudTrail
    |
    v
EventBridge
    |
    v
Security evaluation
    |
    +--> protected violation
    |
    +--> Audit
    |
    +--> Notification
    |
    +--> optional revert
```

The browser must not be able to bypass authorization and directly perform the revert.

---

# 120. Definition of Done

The deployment is complete only when:

```text
[ ] Terraform state operational
[ ] Terragrunt hierarchy operational
[ ] Organizations operational
[ ] StackSets operational
[ ] Member accounts onboarded
[ ] Cross-account AssumeRole operational
[ ] CloudTrail operational
[ ] EventBridge operational
[ ] Central Event Bus operational
[ ] DynamoDB operational
[ ] SNS operational
[ ] Lambda operational
[ ] AWS Config operational
[ ] Governance Engine operational
[ ] Enforcement operational
[ ] Remediation operational
[ ] Exemptions operational
[ ] Audit operational
[ ] FinOps operational
[ ] Cost Allocation Tags activated
[ ] Protected tag drift operational
[ ] ECS operational
[ ] ALB operational
[ ] HTTPS operational
[ ] Route53 operational
[ ] Secrets Manager operational
[ ] RBAC operational
[ ] GitHub OIDC operational
[ ] CI/CD operational
[ ] CloudWatch dashboard operational
[ ] CloudWatch alarms operational
[ ] DLQ operational
[ ] Backup operational
[ ] Disaster recovery documented
[ ] End-to-end tests passing
[ ] Termination disabled
[ ] Security review completed
```

---

# 121. Critical Design Rules

These rules must not be violated.

### Rule 1

DynamoDB is the authoritative state store.

### Rule 2

The Governance Engine is the authoritative compliance evaluator.

### Rule 3

Backend authorization is authoritative.

### Rule 4

The browser never receives AWS credentials.

### Rule 5

The browser never directly calls privileged AWS APIs.

### Rule 6

CloudTrail + EventBridge provide the event path.

### Rule 7

StackSets onboard member accounts.

### Rule 8

SNS is the notification abstraction.

### Rule 9

Audit records are generated by backend actions.

### Rule 10

Cost Explorer is the source for AWS-reported cost data.

### Rule 11

FinOps must distinguish actual, estimated and unallocated spend.

### Rule 12

Termination is disabled by default.

### Rule 13

No duplicate Governance Engine.

### Rule 14

No duplicate Remediation Engine.

### Rule 15

No duplicate Notification system.

### Rule 16

No duplicate State Store.

### Rule 17

No new frontend framework.

### Rule 18

The existing Flask + Vanilla UI remains the product UI.

---

# 122. Production Architecture Summary

The resulting system is:

```text
                    ┌──────────────────────┐
                    │ AWS Organizations    │
                    └──────────┬───────────┘
                               │
                  CloudFormation StackSets
                               │
              ┌────────────────┼────────────────┐
              │                │                │
             DEV              QA              PROD
              │                │                │
         EventBridge      EventBridge      EventBridge
              │                │                │
              └────────────────┼────────────────┘
                               │
                               ▼
                    Central EventBridge Bus
                               │
                               ▼
                       Governance Lambda
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
     Governance           Enforcement            FinOps
          │                    │                    │
          ▼                    ▼                    ▼
      Compliance          Remediation        Cost Explorer
          │                    │                    │
          └────────────┬───────┴────────────────────┘
                       │
                       ▼
                    DynamoDB
                       │
             ┌─────────┼─────────┐
             ▼         ▼         ▼
           Audit      SNS      Reporting
                                  │
                                  ▼
                              Flask API
                                  │
                                  ▼
                            ECS Fargate
                                  │
                                  ▼
                              ALB/HTTPS
                                  │
                                  ▼
                          Existing Vanilla UI
```

---

# 123. Implementation Directive

The infrastructure engineer implementing this specification must:

1. Reuse existing Terraform/Terragrunt conventions in the organization's infrastructure repository.
2. Reuse existing Parts 1–3 application modules.
3. Do not duplicate existing AWS clients or domain services.
4. Implement the infrastructure modules listed in this document.
5. Replace all example account IDs with environment configuration.
6. Keep all credentials out of Git.
7. Validate every IAM policy using least privilege.
8. Deploy member-account infrastructure through StackSets.
9. Deploy the application through ECS.
10. Deploy the existing UI without introducing a frontend framework.
11. Start in read-only mode.
12. Validate the complete event flow.
13. Enable remediation only after successful testing.
14. Keep termination disabled unless separately approved.

---

# 124. Official AWS References

Use AWS documentation as the authoritative source when AWS behavior differs from this specification.

AWS documents that service-managed StackSets can target organizational units and automatically deploy to accounts added to those OUs.

AWS also documents that trusted access with Organizations is required for service-managed StackSets.

CloudFormation's StackSet API supports `SERVICE_MANAGED`, automatic deployment and OU-based targets.

CloudTrail events can be delivered to EventBridge and filtered using event source/event name patterns.

---

# 125. Final Deployment Philosophy

This platform should be deployed in this order:

```text
Infrastructure
      ↓
Identity
      ↓
State
      ↓
Events
      ↓
Governance
      ↓
Reporting
      ↓
Remediation
      ↓
FinOps
      ↓
Security
      ↓
Web UI
      ↓
CI/CD
      ↓
Monitoring
      ↓
Production Enforcement
```

The most important operational principle is:

> **Build the control plane first, prove detection second, prove remediation third, and only then introduce enforcement.**

Production termination must always remain an explicit, independently controlled capability.
