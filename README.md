# AWS Tagging Utilities

## Overview

This project provides lightweight AWS Lambda utilities for working with resource tags across multiple AWS services using the **Resource Groups Tagging API**.

It includes:

* **TagRead**: reads resources by resource type and tag filters
* **TagWriter**: adds or updates one or more tags on a resource ARN
* **TagOnCreate**: auto-tags new resources with an `Owner` tag based on the creator's identity
* **TagReport**: generates a compliance scorecard and coverage report
* **TagSync**: propagates tags from parent resources (like VPCs) to children

These Lambdas are useful for:

* internal inventory lookup
* tag-based governance
* resource discovery by org, service, pod, environment, or any custom tag
* internal self-service platforms
* operational tagging workflows

---

## What this project does

### TagRead

The read Lambda:

* accepts a supported resource type
* accepts one or more tag filters
* calls AWS Resource Groups Tagging API
* returns all matching resources
* includes a simplified response with selected common tags and the full tag map

### TagWriter

The write Lambda:

* accepts a resource ARN
* accepts one or more tags
* applies those tags to the resource
* returns success or partial-failure details

### TagOnCreate

The automation Lambda:

* triggered by EventBridge via CloudTrail API calls (e.g., `RunInstances`, `CreateBucket`)
* identifies the IAM principal (User or Role) that created the resource
* checks if the resource currently has **no tags**
* if untagged, applies an `Owner` tag (default key: `Owner`) with the principal's name
* helps ensure that new resources are always attributed to a creator without forcing manual tagging during the initial API call

---

## Supported resource types

The current implementation supports the following logical resource names:

### Friendly aliases

| Input            | AWS Type                          |
| ---------------- | --------------------------------- |
| Elasticache      | elasticache:cluster               |
| RDS              | rds:db                            |
| RDSCluster       | rds:cluster                       |
| DynamoDB         | dynamodb:table                    |
| Elasticsearch    | es                                |
| ELB              | elasticloadbalancing:loadbalancer |
| S3               | s3:bucket                         |
| EC2Instance      | ec2:instance                      |
| EBSVolume        | ec2:volume                        |
| EBSSnapshot      | ec2:snapshot                      |
| VPC              | ec2:vpc                           |
| Subnet           | ec2:subnet                        |
| SecurityGroup    | ec2:security-group                |
| InternetGateway  | ec2:internet-gateway              |
| NatGateway       | ec2:natgateway                    |
| RouteTable       | ec2:route-table                   |
| NetworkInterface | ec2:network-interface             |
| Lambda           | lambda:function                   |
| ECSCluster       | ecs:cluster                       |
| ECSService       | ecs:service                       |
| ECR              | ecr:repository                    |
| EKS              | eks:cluster                       |
| SNS              | sns:topic                         |
| SQS              | sqs:queue                         |
| Redshift         | redshift:cluster                  |
| KinesisStream    | kinesis:stream                    |
| KMS              | kms:key                           |
| LogGroup         | logs:log-group                    |
| ApiGateway       | apigateway:restapis               |
| Athena           | athena                            |
| Glue             | glue                              |
| StepFunction     | states                            |
| CloudFormation   | cloudformation                    |
| EMR              | elasticmapreduce                  |
| SageMaker        | sagemaker                         |

### Raw AWS resource types

You can also pass raw values:

```json
"resource": "ec2:instance"
"resource": "lambda:function"
```

---

## Project structure

A simple structure is recommended:

```text
aws-tagging-utils/
├── README.md
├── src/
│   ├── tag_read.py
│   ├── tag_writer.py
│   ├── tag_on_create.py
│   ├── tag_report.py
│   └── tag_sync.py
└── requirements.txt
```

### Suggested `requirements.txt`

```txt
boto3
botocore
```

If deploying on AWS Lambda using the managed Python runtime, `boto3` and `botocore` are already available. You usually do not need to package them unless you want version pinning.

---

## Multi-Region Support

All utilities are now designed for multi-region operations, allowing you to manage tags across your entire AWS footprint from a single deployment or command.

---

## Lambda 1: TagRead (Multi-Region)

### What's New
TagRead can now search across multiple regions simultaneously and aggregate results.

### Input format
```json
{
  "resources": ["RDS", "EC2Instance"],
  "regions": ["us-east-1", "us-east-2", "eu-west-1"],
  "filters": {
    "env": "production"
  }
}
```
*   Use `"regions": "all"` to scan all enabled regions in the account.
*   The response now includes a `Region` field for each resource.

---

## Lambda 2: TagWriter (Multi-Region)

### What's New
TagWriter now automatically detects the region from each provided ARN. You can send a list of ARNs from different regions in a single request, and the Lambda will handle the regional client switching for you.

### Input
```json
{
  "arns": [
    "arn:aws:ec2:us-east-1:123456789012:instance/i-123",
    "arn:aws:ec2:us-west-2:123456789012:instance/i-456"
  ],
  "tags": {
    "CostCenter": "9999"
  }
}
```

---

## Lambda 3: TagOnCreate (Multi-Region & Scanning)

### What's New
TagOnCreate has been upgraded for both responsive and proactive governance.

1.  **Responsive**: Now handles multi-region CloudTrail events by extracting the correct region from the payload and initiating a dynamic tagging client. 
    **Deployment Note**: CloudTrail emits events to the EventBridge bus in the *specific region where the API call occurred*. To monitor multiple regions with a single Lambda:
    - Deploy the TagOnCreate Lambda in your primary region (e.g., `us-east-2`).
    - Create EventBridge rules in all other active regions.
    - Set the target of those regional rules to forward matched events to the default Event Bus in your primary region.
2.  **Proactive Scan (Reconciliation)**: You can now trigger a manual scan to find untagged resources from the past. It uses CloudTrail history to find the original creator.

### Scan Input
```json
{
  "action": "scan",
  "regions": "all"
}
```

---

## Lambda 4: TagReport (Visibility & Reporting)

TagReport audits your account for tagging compliance across multiple regions and generates a health scorecard.

### Features
*   **Compliance Audit**: Checks if resources have all `MANDATORY_TAGS`.
*   **Scorecard**: Calculates a compliance percentage for the account/region.
*   **S3 Export**: Saves a detailed JSON report to an S3 bucket.

### Input
```json
{
  "regions": "all",
  "mandatory_tags": ["Owner", "Environment", "CostCenter"],
  "export_bucket": "my-tagging-reports-bucket"
}
```

---

## Lambda 5: TagSync (Automation & Intelligence)

TagSync ensures tag consistency by propagating tags from "Parent" resources to their children.

### Features
*   **VPC Propagation**: Automatically copies VPC tags to all associated Subnets, Security Groups, Route Tables, and Gateways.
*   **Consistency**: Eliminates manual tagging errors for network infrastructure.

### Input
```json
{
  "action": "sync_vpc",
  "vpc_id": "vpc-0123456789abcdef0",
  "region": "us-east-1"
}
```

---

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `OWNER_TAG_KEY` | `Owner` | Tag key for creator attribution |
| `AWS_REGION` | `us-east-2` | Primary/Fallback region |

---

## IAM permissions

### TagRead IAM policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TagReadOnly",
      "Effect": "Allow",
      "Action": [
        "tag:GetResources"
      ],
      "Resource": "*"
    }
  ]
}
```

### TagOnCreate IAM policy

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "TaggingApiAccess",
            "Effect": "Allow",
            "Action": [
                "tag:GetResources",
                "tag:TagResources"
            ],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetBucketTagging",
                "s3:PutBucketTagging",
                "ec2:CreateTags",
                "ec2:DescribeTags",
                "rds:AddTagsToResource",
                "rds:ListTagsForResource",
                "lambda:TagResource",
                "lambda:ListTags",
                "dynamodb:TagResource",
                "dynamodb:ListTagsOfResource",
                "elasticache:AddTagsToResource",
                "elasticache:ListTagsForResource",
                "es:AddTags",
                "es:ListTags",
                "elasticloadbalancing:AddTags",
                "elasticloadbalancing:DescribeTags",
                "sns:TagResource",
                "sns:ListTagsForResource",
                "sqs:TagQueue",
                "sqs:ListQueueTags",
                "ecs:TagResource",
                "ecs:ListTagsForResource",
                "eks:TagResource",
                "eks:ListTagsForResource",
                "ecr:TagResource",
                "ecr:ListTagsForResource",
                "redshift:CreateTags",
                "redshift:DeleteTags",
                "redshift:DescribeTags",
                "kinesis:AddTagsToStream",
                "kinesis:ListTagsForStream",
                "kms:TagResource",
                "kms:ListResourceTags",
                "logs:TagLogGroup",
                "logs:ListTagsForResource",
                "apigateway:TagResource",
                "apigateway:GetTagsForResource",
                "athena:TagResource",
                "athena:ListTagsForResource",
                "glue:TagResource",
                "glue:GetTags",
                "states:TagResource",
                "states:ListTagsForResource",
                "cloudformation:TagResource",
                "cloudformation:DescribeStacks",
                "elasticmapreduce:AddTags",
                "elasticmapreduce:ListTags",
                "sagemaker:AddTags",
                "sagemaker:ListTags"
            ],
            "Resource": "*"
        }
    ]
}
```

### TagWriter IAM policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TagApiWrite",
      "Effect": "Allow",
      "Action": [
        "tag:TagResources"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowRdsTagging",
      "Effect": "Allow",
      "Action": [
        "rds:AddTagsToResource",
        "rds:ListTagsForResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowDynamoDbTagging",
      "Effect": "Allow",
      "Action": [
        "dynamodb:TagResource",
        "dynamodb:ListTagsOfResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowElastiCacheTagging",
      "Effect": "Allow",
      "Action": [
        "elasticache:AddTagsToResource",
        "elasticache:ListTagsForResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowElasticsearchTagging",
      "Effect": "Allow",
      "Action": [
        "es:AddTags",
        "es:ListTags"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowElbTagging",
      "Effect": "Allow",
      "Action": [
        "elasticloadbalancing:AddTags",
        "elasticloadbalancing:DescribeTags"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowS3Tagging",
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketTagging",
        "s3:PutBucketTagging"
      ],
      "Resource": "arn:aws:s3:::*"
    }
  ]
}
```

---

## How to deploy

## Option 1: Deploy manually in AWS Lambda Console

### TagRead

1. Create a new Lambda function
2. Choose Python runtime
3. Paste the TagRead code into `lambda_function.py`
4. Configure execution role with the TagRead IAM policy
5. Set environment variable:

```text
AWS_REGION=us-east-2
```

6. Deploy and test

### TagWriter

1. Create another Lambda function
2. Choose Python runtime
3. Paste the TagWriter code into `lambda_function.py`
4. Configure execution role with the TagWriter IAM policy
5. Set environment variable:

```text
AWS_REGION=us-east-2
```

6. Deploy and test

---

## Option 2: Deploy with ZIP package

From the project root:

```bash
zip -r tag-read.zip src/tag_read.py
zip -r tag-writer.zip src/tag_writer.py
```

Then upload the ZIPs to their respective Lambda functions.

If you want a cleaner package layout:

```bash
mkdir -p build/tag-read
cp src/tag_read.py build/tag-read/lambda_function.py
cd build/tag-read && zip -r ../../tag-read.zip . && cd -

mkdir -p build/tag-writer
cp src/tag_writer.py build/tag-writer/lambda_function.py
cd build/tag-writer && zip -r ../../tag-writer.zip . && cd -
```

---

## How to test locally

### Prerequisites

* Python 3.10+ or similar
* AWS credentials configured locally
* access to target AWS account
* permissions matching the Lambda role behavior

Check identity:

```bash
aws sts get-caller-identity
```

Set region:

```bash
export AWS_REGION=us-east-2
```

---

## Local test for TagRead

Create `test_tag_read.py`:

```python
from src.tag_read import lambda_handler


event = {
    "resource": "RDS",
    "region": "us-east-2",
    "filters": {
        "org": "finance",
        "service": ["billing", "ledger"],
        "pod": "pod-a"
    }
}

print(lambda_handler(event, None))
```

Run:

```bash
python test_tag_read.py
```

### or

```py
if __name__ == "__main__":
    sample_event = {
        "resource": "ec2:instance",
        "region": "us-east-2",
        "filters": {
            "Name": "Haproxy-1"
        }
    }
    print(lambda_handler(sample_event, None))
```

Run:

```bash
python3 tag_read.py
# or
uv run flask --app web/app run --reload
```

---

## Local test for TagWriter

Create `test_tag_writer.py`:

```python
from src.tag_writer import lambda_handler


event = {
    "arn": "arn:aws:rds:us-east-2:123456789012:db:finance-db",
    "region": "us-east-2",
    "tags": {
        "org": "finance",
        "service": "billing",
        "pod": "pod-a"
    }
}

print(lambda_handler(event, None))
```

Run:

```bash
python test_tag_writer.py
```

### or

```py
if __name__ == "__main__":
    sample_event = {
        "arn": "arn:aws:ec2:us-east-2:547580490325:instance/i-03a7beb7702ef226d",
        "region": "us-east-2",
        "tags": {
            "environment": "dev",
            "owner": "devops"
        }
    }
    print(lambda_handler(sample_event, None))
```

```bash
python3 tag_writer.py
```

---

## How to test in Lambda Console

### Sample test event for TagRead

```json
{
  "resource": "RDS",
  "region": "us-east-2",
  "filters": {
    "org": "finance",
    "service": ["billing", "ledger"],
    "pod": "pod-a"
  }
}
```

### Sample test event for TagWriter

```json
{
  "arn": "arn:aws:rds:us-east-2:123456789012:db:finance-db",
  "region": "us-east-2",
  "tags": {
    "org": "finance",
    "service": "billing",
    "pod": "pod-a"
  }
}
```

---

## Validation behavior

### TagRead validations

* `resource` must be provided
* `resource` must be one of the supported values
* `filters` must be a non-empty object
* each filter value must be either:

  * a string
  * a non-empty list of strings

### TagWriter validations

* `arn` must be provided
* `tags` must be a non-empty object
* tag keys must be non-empty
* tag values must not be null

---

## Common troubleshooting

### 1. No resources returned

Possible reasons:

* wrong region
* resource type mismatch
* tags do not match filter values
* resource type is not supported by the current map

### 2. Tagging failed

Possible reasons:

* Lambda role is missing service-specific tag permissions
* resource ARN is invalid
* resource type does not support tagging through the used permissions
* cross-account or cross-region mismatch

### 3. Access denied

Check:

* Lambda execution role
* attached IAM policy
* SCP restrictions if using AWS Organizations
* resource-specific service permissions

---

## Example use cases

### Find all finance RDS databases

```json
{
  "resource": "RDS",
  "filters": {
    "org": "finance"
  }
}
```

### Find all ELBs for treasury billing service

```json
{
  "resource": "ELB",
  "filters": {
    "org": "treasury",
    "service": "billing"
  }
}
```

### Apply multiple tags to an S3 bucket

```json
{
  "arn": "arn:aws:s3:::my-audit-bucket",
  "tags": {
    "org": "platform",
    "service": "audit",
    "environment": "prod"
  }
}
```

---

## Summary

This project provides a simple internal tagging toolkit for AWS.

* **TagRead** helps discover resources using one or more tag filters
* **TagWriter** helps apply one or more tags to a resource
* both are simple to deploy as Lambda functions
* both are useful for governance, inventory, and internal self-service workflows
