"""
TagSync: propagate tags from a parent resource to its children.

Supported parents (see src/propagation.py RULES): vpc, ec2_instance, ebs_volume, asg,
ecs_service, cloudformation, rds_cluster, eks_cluster, elbv2, lambda.

Event formats
-------------
{"action": "sync_vpc", "vpc_id": "vpc-123", "region": "us-east-1"}                 # legacy
{"action": "propagate", "rule": "asg", "parent": "my-asg", "region": "us-east-1",
 "overwrite": false, "dry_run": false, "fix_config": true}

Only tags selected by PROPAGATE_KEYS are copied; PROPAGATE_EXCLUDE_KEYS (default "Name") and
aws:* tags never are, so children keep their own names. By default existing child values are
kept (overwrite=false). Every applied sync is recorded as an undoable change set.
"""
import json
from typing import Any, Dict

from src.config import DEFAULT_REGION
from src.logging_config import get_logger

logger = get_logger(__name__)


def propagate(rule: str, parent: str, region: str, overwrite: bool = False, dry_run: bool = False,
              fix_config: bool = True, actor: str = "tag-sync") -> Dict[str, Any]:
    """Check one parent and (unless dry_run) apply missing/mismatched tags to its children."""
    from src import changesets, propagation
    result = propagation.check([rule], [region], parent=parent)
    if result["summary"]["errors"]:
        return {"error": result["summary"]["errors"][0]["error"]}
    if not result["summary"]["parents_checked"].get(rule):
        return {"error": f"{rule} parent {parent!r} not found in {region}"}
    findings = result["findings"]
    if not fix_config:
        findings = [f for f in findings if f["kind"] != "config"]
    plan = propagation.fix_plan(findings, overwrite)
    out: Dict[str, Any] = {"rule": rule, "parent": parent, "region": region, "findings": findings}
    if dry_run:
        out["preview"] = changesets.preview(plan["targets"], overwrite) if plan["targets"] else None
        out["config_changes"] = plan["ops"]
        return out
    if not plan["targets"] and not plan["ops"]:
        out.update(message="Children already carry the parent's tags", updated_resources=[])
        return out
    cs = changesets.apply(plan["targets"], overwrite, actor, kind="propagation",
                          description=f"Propagate {rule} {parent} ({region})", ops=plan["ops"])
    out.update(change_set_id=cs["id"], status=cs["status"], summary=cs["summary"],
               updated_resources=[i.get("arn") or i.get("target") for i in cs["items"] if i.get("result") == "SUCCESS"],
               errors=[f"{i.get('arn') or i.get('target')}: {i.get('error')}" for i in cs["items"] if i.get("result") == "FAILED"])
    return out


def sync_vpc_tags(region: str, vpc_id: str, overwrite: bool = False) -> Dict[str, Any]:
    """Legacy entry point: VPC → subnets, SGs, route tables, gateways, NACLs, endpoints."""
    result = propagate("vpc", vpc_id, region, overwrite=overwrite)
    if result.get("error"):
        return {"error": result["error"]}
    result["vpc_id"] = vpc_id
    return result


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main entry point for TagSync (Lambda, web API and MCP)."""
    logger.info("Received event: %s", event)

    action = event.get("action", "sync_vpc")
    region = event.get("region", DEFAULT_REGION)
    overwrite = bool(event.get("overwrite", False))
    actor = event.get("actor", "tag-sync")

    try:
        if action == "sync_vpc":
            vpc_id = event.get("vpc_id")
            if not vpc_id:
                return {"statusCode": 400, "body": {"message": "vpc_id is required for sync_vpc action"}}
            result = sync_vpc_tags(region, vpc_id, overwrite=overwrite)
        elif action == "propagate":
            from src.propagation import RULES
            rule, parent = event.get("rule"), event.get("parent")
            if rule not in RULES:
                return {"statusCode": 400, "body": {"message": f"rule must be one of {sorted(RULES)}"}}
            if not parent:
                return {"statusCode": 400, "body": {"message": "parent (ARN, ID or name) is required"}}
            result = propagate(rule, parent, region, overwrite=overwrite,
                               dry_run=bool(event.get("dry_run", False)),
                               fix_config=bool(event.get("fix_config", True)), actor=actor)
        else:
            return {"statusCode": 400, "body": {"message": f"Unsupported action: {action}"}}

        if result.get("error"):
            status = 404 if "not found" in result["error"].lower() else 502
            return {"statusCode": status, "body": {"message": result["error"]}}
        return {"statusCode": 200, "body": result}

    except ValueError as e:
        return {"statusCode": 400, "body": {"message": str(e)}}
    except Exception as e:
        logger.exception("Unexpected error in TagSync")
        return {"statusCode": 500, "body": {"message": str(e)}}


if __name__ == "__main__":
    # python -m src.tag_sync <rule> <parent> [region]   e.g. python -m src.tag_sync vpc vpc-123 us-east-1
    import sys
    if len(sys.argv) > 2:
        print(json.dumps(propagate(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else DEFAULT_REGION,
                                   dry_run=True), indent=2, default=str))
