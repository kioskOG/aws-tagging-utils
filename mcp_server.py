"""
MCP Server for AWS Tagging Utilities.
Exposes AWS tagging operations as tools for AI agents.

Run:
    python mcp_server.py
    # or via the registered entrypoint:
    mcp_server
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

# Project root: aws-tagging-utils/
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config import DEFAULT_REGION
from src.logging_config import get_logger
from src.tag_read import RESOURCE_TYPE_MAP, lambda_handler as read_handler
from src.tag_writer import lambda_handler as write_handler
from src.tag_on_create import lambda_handler as gov_handler
from src.tag_report import lambda_handler as report_handler
from src.tag_sync import lambda_handler as sync_handler

logger = get_logger(__name__)

# Create MCP server
mcp = FastMCP("AWS Tagging Utils")


@mcp.tool()
def list_resource_types() -> Dict[str, Any]:
    """
    List supported AWS resource types and their friendly aliases.
    Use these aliases in other tools like read_tags.
    """
    return {
        "aliases": sorted(RESOURCE_TYPE_MAP.keys()),
        "map": RESOURCE_TYPE_MAP
    }


@mcp.tool()
def read_tags(
    resource: Optional[str] = None,
    resources: Optional[List[str]] = None,
    filters: Optional[Dict[str, Any]] = None,
    region: str = DEFAULT_REGION,
    regions: Optional[Any] = None,
    missing_tag: Optional[str] = None
) -> Dict[str, Any]:
    """
    Read tags from AWS resources.

    Args:
        resource: A single resource type alias (e.g., 'EC2Instance', 'S3').
        resources: A list of resource type aliases.
        filters: Tag filters as a dictionary (e.g., {"env": "prod"}).
        region: Default AWS region.
        regions: List of regions to scan, or "all".
        missing_tag: If provided, only return resources missing this specific tag key.
    """
    logger.info("read_tags called", extra={"resource_type": resource or resources, "aws_region": region})
    payload = {
        "region": region,
        "resource": resource,
        "resources": resources,
        "regions": regions,
        "missing_tag": missing_tag
    }
    if filters:
        payload["filters"] = filters
    result = read_handler(payload, None)
    return result


@mcp.tool()
def write_tags(
    arns: List[str],
    tags: Dict[str, str],
    region: str = DEFAULT_REGION
) -> Dict[str, Any]:
    """
    Apply tags to one or more AWS resource ARNs.

    Args:
        arns: List of resource ARNs to tag.
        tags: Dictionary of tags to apply (e.g., {"Owner": "DevOps"}).
        region: Default AWS region for the request.
    """
    logger.info("write_tags called", extra={"arn_count": len(arns), "aws_region": region})
    payload = {
        "arns": arns,
        "tags": tags,
        "region": region
    }
    result = write_handler(payload, None)
    return result


@mcp.tool()
def apply_governance(
    region: str = DEFAULT_REGION,
    regions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Scan for untagged resources and attempt to apply governance tags (e.g., Creator).

    Args:
        region: Single region to scan (used if regions not provided).
        regions: List of regions to scan, or pass a single region.
    """
    target = regions or [region]
    logger.info("apply_governance called", extra={"aws_region": target})
    payload = {"action": "scan", "regions": target}
    result = gov_handler(payload, None)
    return result


@mcp.tool()
def get_tag_report(
    resource: Optional[str] = None,
    resources: Optional[List[str]] = None,
    regions: Optional[List[str]] = None,
    region: str = DEFAULT_REGION,
    mandatory_tags: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Generate a report of tagging compliance status.

    Args:
        resource: Single resource type alias (e.g. 'DynamoDB').
        resources: List of resource type aliases.
        regions: List of regions to scan.
        region: Default region if regions not provided.
        mandatory_tags: Custom list of mandatory tag keys.
    """
    target_regions = regions or [region]
    logger.info("get_tag_report called", extra={"aws_region": target_regions, "resource_type": resource or resources})
    payload = {
        "resource": resource,
        "resources": resources,
        "regions": target_regions,
        "mandatory_tags": mandatory_tags
    }
    result = report_handler(payload, None)
    return result


@mcp.tool()
def sync_tags(
    source_arn: str,
    target_type: str = "vpc_children",
    region: str = DEFAULT_REGION
) -> Dict[str, Any]:
    """
    Sync tags from a source resource to related target resources.

    Currently supports VPC → children propagation (Subnets, Security Groups,
    Route Tables, Internet Gateways, NAT Gateways).

    Args:
        source_arn: The ARN of the source VPC resource.
        target_type: Type of sync operation. Currently only 'vpc_children' is supported.
        region: AWS region.
    """
    # Extract VPC ID from ARN (e.g. arn:aws:ec2:us-east-2:123456:vpc/vpc-abc123)
    vpc_id = source_arn.split("/")[-1] if "/" in source_arn else source_arn
    logger.info("sync_tags called", extra={"aws_region": region, "resource_type": target_type})
    payload = {
        "action": "sync_vpc",
        "vpc_id": vpc_id,
        "region": region
    }
    result = sync_handler(payload, None)
    return result


def main():
    logger.info("Starting AWS Tagging Utils MCP server")
    mcp.run()


if __name__ == "__main__":
    main()
