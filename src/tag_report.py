import logging
import os
import json
from typing import Any, Dict, List, Optional
from datetime import datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# Try to import from tag_read to reuse RESOURCE_TYPE_MAP
try:
    from tag_read import RESOURCE_TYPE_MAP
except ImportError:
    try:
        from src.tag_read import RESOURCE_TYPE_MAP
    except ImportError:
        # Fallback if module is completely undiscoverable
        RESOURCE_TYPE_MAP = {
            "Elasticache": "elasticache:cluster",
            "RDS": "rds:db",
            "RDSCluster": "rds:cluster",
            "DynamoDB": "dynamodb:table",
            "Elasticsearch": "es",
            "ELB": "elasticloadbalancing:loadbalancer",
            "S3": "s3:bucket",
            "EC2Instance": "ec2:instance",
            "EBSVolume": "ec2:volume",
            "EBSSnapshot": "ec2:snapshot",
            "VPC": "ec2:vpc",
            "Subnet": "ec2:subnet",
            "SecurityGroup": "ec2:security-group",
            "InternetGateway": "ec2:internet-gateway",
            "NatGateway": "ec2:natgateway",
            "RouteTable": "ec2:route-table",
            "NetworkInterface": "ec2:network-interface",
            "Lambda": "lambda:function",
            "ECSCluster": "ecs:cluster",
            "ECSService": "ecs:service",
            "ECR": "ecr:repository",
            "EKS": "eks:cluster",
            "SNS": "sns:topic",
            "SQS": "sqs:queue",
            "Redshift": "redshift:cluster",
            "KinesisStream": "kinesis:stream",
            "KMS": "kms:key",
            "LogGroup": "logs:log-group",
            "ApiGateway": "apigateway:restapis",
            "Athena": "athena:workgroup",
            "Glue": "glue:job",
            "EMR": "elasticmapreduce:cluster",
            "StepFunction": "states:stateMachine",
            "CloudFormation": "cloudformation:stack",
            "EventBridge": "events:rule",
            "SageMaker": "sagemaker:notebook-instance",
        }

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_REGION = os.environ.get("AWS_REGION", "us-east-2")
# Mandatory tags to check for (comma-separated string)
MANDATORY_TAGS = [t.strip() for t in os.environ.get("MANDATORY_TAGS", "Owner,Environment,CostCenter").split(",") if t.strip()]

def get_client(region: str):
    return boto3.client("resourcegroupstaggingapi", region_name=region)

def get_s3_client():
    return boto3.client("s3")

def build_response(status_code: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "body": body,
    }

def get_all_regions():
    try:
        ec2 = boto3.client("ec2", region_name=DEFAULT_REGION)
        regs = ec2.describe_regions()
        return [r["RegionName"] for r in regs["Regions"]]
    except Exception:
        return [DEFAULT_REGION]

def generate_report(target_regions: List[str], mandatory_tags: List[str]) -> Dict[str, Any]:
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "mandatory_tags": mandatory_tags,
        "summary": {
            "total_resources": 0,
            "compliant": 0,
            "non_compliant": 0,
            "compliance_score": 0.0
        },
        "regions": {}
    }

    all_types = list(RESOURCE_TYPE_MAP.values())
    
    for region in target_regions:
        logger.info("Auditing region: %s", region)
        region_report = {
            "total": 0,
            "compliant": 0,
            "non_compliant": 0,
            "resources": []
        }
        
        try:
            client = get_client(region)
            paginator = client.get_paginator("get_resources")
            page_iterator = paginator.paginate(ResourceTypeFilters=all_types)
            
            for page in page_iterator:
                for item in page.get("ResourceTagMappingList", []):
                    arn = item.get("ResourceARN", "")
                    tags = {t["Key"]: t["Value"] for t in item.get("Tags", [])}
                    
                    missing = [t for t in mandatory_tags if t not in tags or not str(tags[t]).strip()]
                    is_compliant = len(missing) == 0
                    
                    res_info = {
                        "ResourceARN": arn,
                        "IsCompliant": is_compliant,
                        "MissingTags": missing,
                        "Tags": tags
                    }
                    
                    region_report["resources"].append(res_info)
                    region_report["total"] += 1
                    if is_compliant:
                        region_report["compliant"] += 1
                    else:
                        region_report["non_compliant"] += 1
                        
            report["summary"]["total_resources"] += region_report["total"]
            report["summary"]["compliant"] += region_report["compliant"]
            report["summary"]["non_compliant"] += region_report["non_compliant"]
            report["regions"][region] = region_report
            if region_report["total"] > 0:
                region_report["compliance_score"] = round((region_report["compliant"] / region_report["total"]) * 100, 2)
            else:
                region_report["compliance_score"] = 100.0
            
        except Exception as e:
            logger.error("Failed to audit region %s: %s", region, e)
            report["regions"][region] = {"error": str(e), "compliance_score": 0.0}

    if report["summary"]["total_resources"] > 0:
        report["summary"]["compliance_score"] = round((report["summary"]["compliant"] / report["summary"]["total_resources"]) * 100, 2)
        
    return report

def lambda_handler(event, context):
    logger.info("Received event: %s", event)
    
    target_regions = event.get("regions")
    if target_regions == "all":
        target_regions = get_all_regions()
    elif isinstance(target_regions, str):
        target_regions = [target_regions]
    elif not isinstance(target_regions, list):
        target_regions = [DEFAULT_REGION]
        
    mandatory_tags = event.get("mandatory_tags")
    if not mandatory_tags:
        mandatory_tags = MANDATORY_TAGS
    elif isinstance(mandatory_tags, str):
        mandatory_tags = [t.strip() for t in mandatory_tags.split(",") if t.strip()]
        
    report = generate_report(target_regions, mandatory_tags)
    
    # Optional S3 Export
    bucket = event.get("export_bucket") or os.environ.get("REPORT_BUCKET")
    if bucket:
        try:
            s3 = get_s3_client()
            key = f"tagging-reports/report-{datetime.utcnow().strftime('%Y-%m-%d-%H-%M-%S')}.json"
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=json.dumps(report, indent=2),
                ContentType="application/json"
            )
            report["export_location"] = f"s3://{bucket}/{key}"
            logger.info("Report exported to %s", report["export_location"])
        except Exception as e:
            logger.error("Failed to export report to S3: %s", e)
            report["export_error"] = str(e)
            
    return build_response(200, report)

if __name__ == "__main__":
    # Local test
    test_event = {
        "regions": [DEFAULT_REGION],
        "mandatory_tags": ["Owner", "Environment"]
    }
    print(json.dumps(lambda_handler(test_event, None), indent=2))
