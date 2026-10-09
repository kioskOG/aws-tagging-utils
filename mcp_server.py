"""
MCP Server for AWS Tagging Utilities.
Exposes AWS tagging operations as tools for AI agents.

Run:
    python mcp_server.py
    # or via the registered entrypoint:
    mcp_server
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# stdio transport: stdout is the JSON-RPC channel, so logs and metrics must not use it.
os.environ.setdefault("LOG_STREAM", "stderr")
os.environ["METRICS_EMF_ENABLED"] = "false"

from fastmcp import FastMCP

# Project root: aws-tagging-utils/
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config import DEFAULT_REGION, MCP_READ_ONLY
from src.logging_config import get_logger
from src.tag_read import RESOURCE_TYPE_MAP, lambda_handler as read_handler
from src.tag_writer import lambda_handler as write_handler
from src.tag_on_create import lambda_handler as gov_handler
from src.tag_report import lambda_handler as report_handler
from src.tag_sync import lambda_handler as sync_handler

logger = get_logger(__name__)


def _deny_if_read_only(tool: str) -> Optional[Dict[str, Any]]:
    if MCP_READ_ONLY:
        logger.warning("Refused %s: MCP_READ_ONLY is enabled", tool)
        return {"statusCode": 403, "body": {"message": f"{tool} is disabled: the MCP server runs with MCP_READ_ONLY=true."}}
    return None

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
    denied = _deny_if_read_only("write_tags")
    if denied:
        return denied
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
    denied = _deny_if_read_only("apply_governance")
    if denied:
        return denied
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
    target_type: str = "vpc",
    region: str = DEFAULT_REGION,
    overwrite: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Propagate tags from a parent resource to its children (undoable change set).

    Args:
        source_arn: Parent ARN, ID or name (e.g. a VPC ID, ASG name, stack name).
        target_type: Rule: vpc, ec2_instance, ebs_volume, asg, ecs_service, cloudformation,
            rds_cluster, eks_cluster, elbv2, lambda ("vpc_children" is accepted for vpc).
        region: AWS region.
        overwrite: Also replace child values that differ from the parent.
        dry_run: Only preview the changes.
    """
    if not dry_run:
        denied = _deny_if_read_only("sync_tags")
        if denied:
            return denied
    rule = "vpc" if target_type == "vpc_children" else target_type
    logger.info("sync_tags called", extra={"aws_region": region, "resource_type": rule})
    return sync_handler({"action": "propagate", "rule": rule, "parent": source_arn, "region": region,
                         "overwrite": overwrite, "dry_run": dry_run, "actor": "mcp"}, None)


@mcp.tool()
def check_tag_propagation(rules: Optional[List[str]] = None, region: str = DEFAULT_REGION) -> Dict[str, Any]:
    """
    Find children that don't carry their parent's tags (EC2 → volumes/ENIs, ASG → instances,
    ECS service → tasks, CloudFormation stack → resources, ...) and parents whose tag propagation
    is switched off (ASG PropagateAtLaunch, ECS propagateTags). Read-only.

    Args:
        rules: Subset of rules to check (default: all).
        region: AWS region.
    """
    from src.propagation import check
    return check(rules, [region])


@mcp.tool()
def preview_tag_changes(arns: List[str], tags: Dict[str, str], overwrite: bool = False) -> Dict[str, Any]:
    """
    Preview a bulk tag change: per-resource diff and compliance before/after. Read-only.
    Pass the returned preview_token to apply_tag_changes.
    """
    from src.changesets import preview
    return preview([{"arn": a, "tags": tags} for a in arns], overwrite)


@mcp.tool()
def apply_tag_changes(arns: List[str], tags: Dict[str, str], overwrite: bool = False,
                      preview_token: Optional[str] = None) -> Dict[str, Any]:
    """
    Apply a bulk tag change as an undoable change set (use undo_tag_changes with its id).
    """
    denied = _deny_if_read_only("apply_tag_changes")
    if denied:
        return denied
    from src.changesets import apply
    return apply([{"arn": a, "tags": tags} for a in arns], overwrite, "mcp", preview_token=preview_token,
                 description=f"MCP bulk change of {len(arns)} resources")


@mcp.tool()
def undo_tag_changes(change_set_id: str) -> Dict[str, Any]:
    """Undo a change set; keys changed by someone else since are left alone and reported."""
    denied = _deny_if_read_only("undo_tag_changes")
    if denied:
        return denied
    from src.changesets import undo
    return undo(change_set_id, "mcp")


@mcp.tool()
def suggest_tag_values(arns: List[str], keys: List[str]) -> Dict[str, Any]:
    """Suggest values for missing tags from related resources in the latest scan. Read-only."""
    from src.suggestions import suggest
    return suggest(arns, keys)


@mcp.tool()
def compliance_leaderboard(dimension: str = "team") -> Dict[str, Any]:
    """Compliance ranking by team, account, ou or service, with week-over-week change. Read-only."""
    from src.insights import leaderboard
    return leaderboard(dimension)


def main():
    logger.info("Starting AWS Tagging Utils MCP server")
    mcp.run()


if __name__ == "__main__":
    main()
