import logging
import os
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_REGION = os.environ.get("AWS_REGION", "us-east-2")

RESOURCE_TYPE_MAP = {
    "Elasticache": "elasticache:cluster",
    "RDS": "rds:db",
    "RDSCluster": "rds:cluster",
    "DynamoDB": "dynamodb:table",
    "Elasticsearch": "es",
    "ELB": "elasticloadbalancing:loadbalancer",
    "S3": "s3:bucket",

    # EC2 family
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

    # Compute / containers
    "Lambda": "lambda:function",
    "ECSCluster": "ecs:cluster",
    "ECSService": "ecs:service",
    "ECR": "ecr:repository",
    "EKS": "eks:cluster",

    # Messaging / integration
    "SNS": "sns:topic",
    "SQS": "sqs:queue",

    # Data / analytics
    "Redshift": "redshift:cluster",
    "KinesisStream": "kinesis:stream",
    "KMS": "kms:key",

    "LogGroup": "logs:log-group",
    "ApiGateway": "apigateway:restapis",

    # Analytics / Data Integration
    "Athena": "athena:workgroup",
    "Glue": "glue:job",
    "EMR": "elasticmapreduce:cluster",

    # Serverless / Orchestration
    "StepFunction": "states:stateMachine",
    "CloudFormation": "cloudformation:stack",
    "EventBridge": "events:rule",

    # Machine Learning
    "SageMaker": "sagemaker:notebook-instance",
}


def get_client(region: str):
    return boto3.client("resourcegroupstaggingapi", region_name=region)


def build_response(status_code: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "body": body,
    }


def resolve_resource_types(resource_spec: Any) -> List[str]:
    """
    Resolve one or more user-provided resource values into AWS ResourceTypeFilters.

    Accepts:
    - A single string (same as `resource` in events)
    - A non-empty list of strings (friendly aliases or raw types like 'ec2:instance')
    """
    if resource_spec is None:
        raise ValueError("Field 'resource' or 'resources' is required.")

    if isinstance(resource_spec, str):
        value = resource_spec.strip()
        if not value:
            raise ValueError("Field 'resource' or 'resources' is required.")
        return [resolve_resource_type(value)]

    if isinstance(resource_spec, list):
        if not resource_spec:
            raise ValueError("'resources' must be a non-empty list.")
        out: List[str] = []
        for item in resource_spec:
            s = str(item).strip()
            if s:
                out.append(resolve_resource_type(s))
        if not out:
            raise ValueError("'resources' must contain at least one non-empty type.")
        # Preserve order, drop duplicate resolved types
        seen: set = set()
        unique: List[str] = []
        for t in out:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique

    raise ValueError(
        "'resource' must be a string or 'resources' must be a list of strings."
    )


def resolve_resource_type(resource_name: str) -> str:
    """
    Resolve the user-provided resource value into an AWS ResourceTypeFilters value.

    Supports:
    - Friendly aliases like 'RDS', 'EC2Instance', 'Lambda'
    - Raw AWS resource type filters like 'ec2:instance', 'lambda:function'
    """
    if not resource_name or not isinstance(resource_name, str):
        raise ValueError("Field 'resource' is required.")

    value = resource_name.strip()
    if not value:
        raise ValueError("Field 'resource' is required.")

    # Friendly alias
    if value in RESOURCE_TYPE_MAP:
        return RESOURCE_TYPE_MAP[value]

    # Raw AWS resource type filter
    if ":" in value:
        return value

    raise ValueError(
        f"Unsupported resource type '{value}'. "
        f"Use one of {sorted(RESOURCE_TYPE_MAP.keys())} "
        f"or pass a raw AWS resource type filter like 'ec2:instance'."
    )


def normalize_tag_filters(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert input like:
        {
          "org": "finance",
          "service": ["billing", "ledger"],
          "pod": "pod-a"
        }

    into AWS TagFilters format:
        [
          {"Key": "org", "Values": ["finance"]},
          {"Key": "service", "Values": ["billing", "ledger"]},
          {"Key": "pod", "Values": ["pod-a"]}
        ]
    """
    if not isinstance(filters, dict) or not filters:
        raise ValueError("'filters' must be a non-empty object")

    tag_filters: List[Dict[str, Any]] = []

    for key, value in filters.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("All filter keys must be non-empty strings")

        if isinstance(value, str):
            values = [value.strip()]
        elif isinstance(value, list):
            values = [str(v).strip() for v in value if str(v).strip()]
        else:
            raise ValueError(
                f"Filter value for '{key}' must be a string or list of strings"
            )

        values = [v for v in values if v]
        if not values:
            raise ValueError(
                f"Filter '{key}' must contain at least one non-empty value"
            )

        tag_filters.append(
            {
                "Key": key.strip(),
                "Values": values,
            }
        )

    return tag_filters


def normalize_tag_filters_optional(filters: Any, *, allow_empty: bool) -> List[Dict[str, Any]]:
    """
    When allow_empty is True, a missing or empty filters object yields no TagFilters.
    Otherwise the same rules as normalize_tag_filters (non-empty dict required).
    """
    if filters is None or filters == {}:
        if allow_empty:
            return []
        raise ValueError("'filters' must be a non-empty object")

    if not isinstance(filters, dict):
        raise ValueError("'filters' must be an object")

    if not filters:
        if allow_empty:
            return []
        raise ValueError("'filters' must be a non-empty object")

    return normalize_tag_filters(filters)


def resource_missing_tag_key(tags: Dict[str, str], tag_key: str) -> bool:
    """True if key is absent or value is empty/whitespace (AWS tags are case-sensitive)."""
    if tag_key not in tags:
        return True
    return str(tags.get(tag_key, "")).strip() == ""


# def extract_resource_info(item: Dict[str, Any]) -> Dict[str, Any]:
#     tags = {
#         tag["Key"]: tag["Value"]
#         for tag in item.get("Tags", [])
#         if "Key" in tag and "Value" in tag
#     }

#     return {
#         "ResourceARN": item.get("ResourceARN", ""),
#         "Name": tags.get("Name", "---"),
#         "OrgTag": tags.get("org", "---"),
#         "PodTag": tags.get("pod", "---"),
#         "ServiceTag": tags.get("service", "---"),
#         "Tags": tags,
#     }

def extract_resource_info(item):
    tags = {
        tag["Key"]: tag["Value"]
        for tag in item.get("Tags", [])
        if "Key" in tag and "Value" in tag
    }

    return {
        "ResourceARN": item.get("ResourceARN", ""),
        "Name": tags.get("Name", "---"),
        "Tags": tags,
    }


def get_resources(
    resource_types: List[str],
    tag_filters: Optional[List[Dict[str, Any]]],
    rg_tag_client,
) -> List[Dict[str, Any]]:
    resources: List[Dict[str, Any]] = []
    by_arn: Dict[str, Dict[str, Any]] = {}

    paginator = rg_tag_client.get_paginator("get_resources")
    paginate_kwargs: Dict[str, Any] = {"ResourceTypeFilters": resource_types}
    if tag_filters:
        paginate_kwargs["TagFilters"] = tag_filters

    page_iterator = paginator.paginate(**paginate_kwargs)

    for page in page_iterator:
        for item in page.get("ResourceTagMappingList", []):
            resource = extract_resource_info(item)
            arn = resource["ResourceARN"]
            if arn and arn not in by_arn:
                by_arn[arn] = resource
                logger.info("Matched resource: %s", arn)

    resources = list(by_arn.values())
    return resources


def lambda_handler(event, context):
    logger.info("Received event: %s", event)

    region = str(event.get("region", DEFAULT_REGION)).strip()
    filters = event.get("filters", {})
    resource_single = event.get("resource")
    resource_multi = event.get("resources")
    missing_raw = event.get("missing_tag")
    missing_tag = (
        str(missing_raw).strip()
        if missing_raw is not None and str(missing_raw).strip()
        else None
    )

    try:
        if resource_multi is not None:
            resolved_types = resolve_resource_types(resource_multi)
            resource_display = resource_multi
        elif resource_single is not None and str(resource_single).strip():
            resource_display = str(resource_single).strip()
            resolved_types = resolve_resource_types(resource_display)
        else:
            return build_response(
                400,
                {
                    "message": "Provide 'resource' (string) or 'resources' (non-empty list).",
                },
            )

        allow_empty_filters = bool(missing_tag)
        tag_filters = normalize_tag_filters_optional(filters, allow_empty=allow_empty_filters)
        if not missing_tag and not tag_filters:
            raise ValueError("'filters' must be a non-empty object")

        # Support multi-region
        target_regions = event.get("regions")
        if target_regions == "all":
            try:
                from tag_on_create import get_all_regions
            except ImportError:
                from src.tag_on_create import get_all_regions
            target_regions = get_all_regions()
        elif isinstance(target_regions, str):
            target_regions = [target_regions]
        elif isinstance(target_regions, list):
            pass
        else:
            target_regions = [region]

        all_resources = []
        for reg in target_regions:
            try:
                rg_tag_client = get_client(reg)
                resources = get_resources(
                    resource_types=resolved_types,
                    tag_filters=tag_filters if tag_filters else None,
                    rg_tag_client=rg_tag_client,
                )
                # Add region info to each resource
                for r in resources:
                    r["Region"] = reg
                all_resources.extend(resources)
            except Exception as e:
                logger.warning("Failed to fetch resources for region %s: %s", reg, e)

        resources = all_resources
        scanned_count = len(resources)
        if missing_tag:
            resources = [
                r
                for r in resources
                if resource_missing_tag_key(r.get("Tags") or {}, missing_tag)
            ]

        body: Dict[str, Any] = {
            "count": len(resources),
            "resolved_resource_types": resolved_types,
            "filters": filters,
            "resources": resources,
        }
        if missing_tag:
            body["missing_tag"] = missing_tag
            body["scanned_count"] = scanned_count

        if isinstance(resource_display, list):
            body["resources_input"] = resource_display
        else:
            body["resource"] = resource_display
            body["resolved_resource_type"] = resolved_types[0]

        return build_response(200, body)

    except ValueError as e:
        logger.warning("Validation error: %s", e)
        return build_response(400, {"message": str(e)})

    except (ClientError, BotoCoreError) as e:
        logger.exception("AWS error while fetching resources")
        return build_response(
            500,
            {
                "message": "Failed to fetch resources from AWS.",
                "error": str(e),
            },
        )

    except Exception as e:
        logger.exception("Unexpected error while fetching resources")
        return build_response(
            500,
            {
                "message": "Unexpected error occurred.",
                "error": str(e),
            },
        )

# executable directly with python src/tag_read.py
if __name__ == "__main__":
    sample_event = {
        "resource": "ec2:instance",
        "region": "ap-southeast-1",
        "filters": {
            "Owner": "abc.example.com"
        }
    }
    print(lambda_handler(sample_event, None))
