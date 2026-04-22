"""
MCP Server for AWS Tagging Utilities.
Exposes AWS tagging operations as tools for AI agents.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

# Project root: aws-tagging-utils/
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.tag_read import RESOURCE_TYPE_MAP, lambda_handler as read_handler
from src.tag_writer import lambda_handler as write_handler
from src.tag_on_create import lambda_handler as gov_handler
from src.tag_report import lambda_handler as report_handler
from src.tag_sync import lambda_handler as sync_handler

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
    region: str = "us-east-2",
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
    region: str = "us-east-2"
) -> Dict[str, Any]:
    """
    Apply tags to one or more AWS resource ARNs.
    
    Args:
        arns: List of resource ARNs to tag.
        tags: Dictionary of tags to apply (e.g., {"Owner": "DevOps"}).
        region: Default AWS region for the request.
    """
    payload = {
        "arns": arns,
        "tags": tags,
        "region": region
    }
    result = write_handler(payload, None)
    return result

@mcp.tool()
def apply_governance(
    region: str = "us-east-2"
) -> Dict[str, Any]:
    """
    Scan for untagged resources and attempt to apply governance tags (e.g., Creator).
    """
    payload = {"action": "scan", "regions": [region]}
    result = gov_handler(payload, None)
    return result

@mcp.tool()
def get_tag_report(
    resource: str,
    region: str = "us-east-2"
) -> Dict[str, Any]:
    """
    Generate a report of tagging status for a specific resource type.
    """
    payload = {
        "resource": resource,
        "region": region
    }
    result = report_handler(payload, None)
    return result

@mcp.tool()
def sync_tags(
    source_arn: str,
    target_type: str,
    region: str = "us-east-2"
) -> Dict[str, Any]:
    """
    Sync tags from a source resource to related target resources (e.g., VPC to Subnets).
    
    Args:
        source_arn: The ARN of the source resource.
        target_type: The type alias of the target resources (e.g., 'Subnet').
        region: AWS region.
    """
    # Extract VPC ID from ARN (e.g. arn:aws:ec2:us-east-2:123456:vpc/vpc-abc123)
    vpc_id = source_arn.split("/")[-1] if "/" in source_arn else source_arn
    payload = {
        "action": "sync_vpc",
        "vpc_id": vpc_id,
        "region": region
    }
    result = sync_handler(payload, None)
    return result

def main():
    mcp.run()

if __name__ == "__main__":
    main()
