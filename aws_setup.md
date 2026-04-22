# AWS Auto-Tagging Setup Guide

## Phase 1: Central Region Setup (ap-southeast-1)
For the same region as the lambda & Amazon EventBridge rule. The CloudTrail is multi-region (home region is hong kong) with management events enabled.

### 1. Create Lambda Function
- **Name:** `Tag-on-Create`
- **Code:** Paste the contents of `tag_on_create.py`
- **Environment Variables(2):**
  - `DEFAULT_REGION`: `ap-southeast-1`
  - `OWNER_TAG_KEY`: `Owner`

### 2. Create an Amazon EventBridge Rule
- **Event Pattern:**
```json
{
  "source": ["aws.ec2", "aws.s3", "aws.lambda", "aws.rds", "aws.dynamodb", "aws.elasticache", "aws.es", "aws.elasticloadbalancing", "aws.sns", "aws.sqs", "aws.ecs", "aws.eks", "aws.ecr", "aws.redshift", "aws.kinesis", "aws.kms", "aws.logs", "aws.apigateway"],
  "detail-type": ["AWS API Call via CloudTrail"],
  "detail": {
    "eventName": ["RunInstances", "CreateVolume", "CreateBucket", "CreateFunction", "CreateDBInstance", "CreateDBCluster", "CreateTable", "CreateTopic", "CreateQueue", "CreateCluster", "CreateService", "CreateRepository", "CreateCacheCluster", "CreateReplicationGroup", "CreateElasticsearchDomain", "CreateSnapshot", "CreateVpc", "CreateSubnet", "CreateSecurityGroup", "CreateInternetGateway", "CreateNatGateway", "CreateRouteTable", "CreateNetworkInterface", "CreateStream", "CreateKey", "CreateLogGroup", "CreateRestApi", "CreateApi", "CreateLoadBalancer"],
    "eventSource": ["ec2.amazonaws.com", "s3.amazonaws.com", "lambda.amazonaws.com", "rds.amazonaws.com", "dynamodb.amazonaws.com", "elasticache.amazonaws.com", "es.amazonaws.com", "elasticloadbalancing.amazonaws.com", "sns.amazonaws.com", "sqs.amazonaws.com", "ecs.amazonaws.com", "eks.amazonaws.com", "ecr.amazonaws.com", "redshift.amazonaws.com", "kinesis.amazonaws.com", "kms.amazonaws.com", "logs.amazonaws.com", "apigateway.amazonaws.com"]
  }
}
```
- **Select Target(s):**
  - Select **AWS service**
  - Select target: **Lambda function**, then choose the `Tag-on-Create` lambda.
  - Proceed to next and create the rule.
  
  *Note: Don't select the "Permissions (Use execution role (recommended))" for this event rule.*

### 3. Add Trigger pointing to EventBridge
- Go to the Lambda console for `Tag-on-Create`.
- Under the **Configuration** tab, click **Add Trigger**.
- Select **EventBridge** and pick the rule we just created.
- *This automatically adds the required resource-based policy to the Lambda function to allow EventBridge to invoke it.*

### How to test (Single-Region):
Create an EBS volume in `ap-southeast-1` without any tag. Wait for a bit; this setup will automatically create the `Owner` tag for you with the user/role of whoever created the volume.

---

## Phase 2: Multi-Region Setup (Other Regions)
To handle resources created in regions other than `ap-southeast-1`, you must configure EventBridge in those remote regions to forward their local CloudTrail events to the central `ap-southeast-1` event bus.

### 1. Create an IAM Role for EventBridge Routing (Global)
- Create a new IAM Role for EventBridge (`events.amazonaws.com`) to allow cross-region forwarding. (Permissions to allow `events:PutEvents` to the central event bus in Singapore).

### 2. Configure EventBridge in Source Regions
For every other region where resources are created (e.g., `us-east-1` or `eu-west-1`), perform the following steps:

- **Create an Amazon EventBridge Rule** (on the default bus for that region).
- **Pattern:** Use the exact same JSON pattern defined in Phase 1 above.
- **Select Target(s):**
  - Target type: Select **Event bus in a different account or Region**.
  - Target: Provide the ARN of your `ap-southeast-1` default event bus (e.g., `arn:aws:events:ap-southeast-1:<ACCOUNT_ID>:event-bus/default`).
  - Execution Role: Select the IAM role you created in Step 1.
- Proceed to next and create the rule.

### How to test (Multi-Region):
Create a resource (like an EBS volume) in one of your newly configured source regions. CloudTrail will record the event locally and EventBridge will forward it to `ap-southeast-1`, where your Lambda function will be triggered. The Lambda will natively process the event and reach back to the correct region to apply the tags.



Lambda IAM Role Trust Policy
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": [
                    "lambda.amazonaws.com",
                    "events.amazonaws.com"
                ]
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
```

Lambda IAM Role Permissions

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "tag:GetResources",
                "tag:TagResources"
            ],
            "Resource": "*"
        }
    ]
}
```

>> create inline policy `tags-on-create`

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
                "rds:AddTagsToResource",
                "lambda:TagResource",
                "dynamodb:TagResource",
                "s3:PutBucketTagging",
                "rds:AddTagsToResource",
                "lambda:TagResource",
                "dynamodb:TagResource",
                "rds:AddTagsToResource",
                "rds:ListTagsForResource",
                "elasticache:AddTagsToResource",
                "elasticache:ListTagsForResource",
                "es:AddTags",
                "es:ListTags",
                "elasticloadbalancing:AddTags",
                "elasticloadbalancing:DescribeTags",
                "s3:GetBucketTagging",
                "s3:PutBucketTagging",
                "ec2:CreateTags",
                "ec2:DescribeTags",
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
                "elasticloadbalancing:AddTags",
                "elasticloadbalancing:DescribeTags"
            ],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": "events:PutEvents",
            "Resource": "arn:aws:events:ap-southeast-1:<ACCOUNT_ID>:event-bus/default"
        }
    ]
}
```
