import json
import time
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError
import boto3

from src.tag_read import RESOURCE_TYPE_MAP
from src.clients import get_ec2_client, get_s3_client, get_tagging_client
from src.config import (
    DEFAULT_REGION, 
    MANDATORY_TAGS, 
    REPORT_BUCKET,
    GOVERNANCE_SCHEMA_PATH,
    GOVERNANCE_UNKNOWN_TAGS,
    GOVERNANCE_NORMALIZATION
)
from src.logging_config import get_logger
from src.governance.engine import TagGovernanceEngine
from src.governance.schema_provider import FileSchemaProvider
from src.multi_account import CrossAccountManager
from src.observability.metrics import record_compliance_report, record_scan_outcome

logger = get_logger(__name__)


# Initialize Governance Engine
schema_provider = FileSchemaProvider(GOVERNANCE_SCHEMA_PATH)
governance_engine = TagGovernanceEngine(
    schema_provider=schema_provider,
    unknown_tags_behavior=GOVERNANCE_UNKNOWN_TAGS,
    enable_normalization=GOVERNANCE_NORMALIZATION
)

def get_client(region: str):
    return get_tagging_client(region)

def _get_s3_client():
    return get_s3_client()

def build_response(status_code: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "body": body,
    }

def get_all_regions():
    try:
        ec2 = get_ec2_client(DEFAULT_REGION)
        regs = ec2.describe_regions()
        return [r["RegionName"] for r in regs["Regions"]]
    except Exception:
        return [DEFAULT_REGION]

def collect_inventory(region: str, account_id: Optional[str], type_filters: Optional[List[str]]) -> Dict[str, Any]:
    """Indirection so tests can stub the inventory without AWS."""
    from src.inventory import collect
    return collect(region, account_id, type_filters)


def generate_report(target_regions: List[str], mandatory_tags: List[str], resource_types: List[str] = None,
                    accounts: Optional[List[Optional[str]]] = None) -> Dict[str, Any]:
    """
    Inventory every account × region and evaluate each resource against the tag schema
    plus `mandatory_tags`.

    Without `resource_types`, the scan reads every resource type and evaluates those in
    RESOURCE_TYPE_MAP (COMPLIANCE_SCOPE=mapped) or all of them (COMPLIANCE_SCOPE=all);
    types outside the map are counted in report["coverage"]["unmapped_types"].
    """
    from src.arn_utils import account_of, type_matches
    from src.aws_session import target_accounts
    from src.config import COMPLIANCE_SCOPE, INVENTORY_SOURCE

    started = time.perf_counter()
    accounts = accounts if accounts is not None else target_accounts()
    mapped_types = list(RESOURCE_TYPE_MAP.values())
    report: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mandatory_tags": mandatory_tags,
        "resource_filter": resource_types,
        "summary": {
            "total_resources": 0,
            "compliant": 0,
            "non_compliant": 0,
            "compliance_score": 0.0
        },
        "regions": {},
        "accounts": {},
    }
    coverage: Dict[str, Any] = {
        "inventory_source": INVENTORY_SOURCE,
        "scope": "filtered" if resource_types else COMPLIANCE_SCOPE,
        "never_tagged": 0,
        "unmapped_types": {},
        "warnings": [],
    }
    region_errors: Dict[str, List[str]] = {}

    for account in accounts:
        acct_label = account or "default"
        for region in target_regions:
            logger.info("Auditing account=%s region=%s", acct_label, region)
            bucket = report["regions"].setdefault(
                region, {"total": 0, "compliant": 0, "non_compliant": 0, "resources": []})
            try:
                inv = collect_inventory(region, account, resource_types or None)
            except Exception as e:
                logger.error("Failed to audit account=%s region=%s: %s", acct_label, region, e)
                region_errors.setdefault(region, []).append(f"{acct_label}: {e}")
                report["accounts"].setdefault(acct_label, {"total": 0, "compliant": 0, "errors": {}})["errors"][region] = str(e)
                continue
            if inv.get("warning"):
                coverage["warnings"].append(inv["warning"])
            if inv.get("source") == "resource_explorer":
                coverage["inventory_source"] = "resource_explorer"

            for res in inv["resources"]:
                arn, tags, rtype = res["arn"], res["tags"], res["resource_type"]
                if not resource_types and COMPLIANCE_SCOPE != "all" and not type_matches(rtype, mapped_types):
                    coverage["unmapped_types"][rtype] = coverage["unmapped_types"].get(rtype, 0) + 1
                    continue

                validation_result = governance_engine.evaluate(
                    tags, resource_id=arn, extra_required=mandatory_tags
                )
                is_compliant = validation_result.compliant
                bucket["resources"].append({
                    "ResourceARN": arn,
                    "ResourceType": rtype,
                    "IsCompliant": is_compliant,
                    "NeverTagged": bool(res.get("never_tagged")),
                    "Violations": [v.to_dict() for v in validation_result.violations],
                    "Warnings": [w.to_dict() for w in validation_result.warnings],
                    "Tags": tags,
                    "NormalizedTags": validation_result.normalized_tags
                })
                bucket["total"] += 1
                bucket["compliant" if is_compliant else "non_compliant"] += 1
                coverage["never_tagged"] += bool(res.get("never_tagged"))

                acct = account_of(arn) if account is None else account
                a = report["accounts"].setdefault(acct, {"total": 0, "compliant": 0, "errors": {}})
                a["total"] += 1
                a["compliant"] += is_compliant

    for region, bucket in report["regions"].items():
        errs = region_errors.get(region, [])
        if errs and len(errs) == len(accounts):
            # Every account failed in this region
            report["regions"][region] = {"error": errs[0], "compliance_score": 0.0}
            continue
        bucket["compliance_score"] = round(bucket["compliant"] * 100 / bucket["total"], 2) if bucket["total"] else 100.0
        report["summary"]["total_resources"] += bucket["total"]
        report["summary"]["compliant"] += bucket["compliant"]
        report["summary"]["non_compliant"] += bucket["non_compliant"]

    for a in report["accounts"].values():
        a["compliance_score"] = round(a["compliant"] * 100 / a["total"], 2) if a["total"] else 100.0

    if report["summary"]["total_resources"] > 0:
        report["summary"]["compliance_score"] = round((report["summary"]["compliant"] / report["summary"]["total_resources"]) * 100, 2)

    failed_regions = [r for r, d in report["regions"].items() if d.get("error")]
    report["summary"]["failed_regions"] = failed_regions
    report["summary"]["failed_accounts"] = sorted(k for k, v in report["accounts"].items()
                                                  if v["errors"] and len(v["errors"]) == len(target_regions))
    report["summary"]["accounts_scanned"] = len(accounts)
    report["summary"]["coverage"] = coverage
    report["summary"]["accounts"] = report["accounts"]
    duration = time.perf_counter() - started
    report["summary"]["duration_seconds"] = round(duration, 2)
    if failed_regions and len(failed_regions) == len(target_regions):
        record_scan_outcome("failed", failed_regions)
    else:
        record_scan_outcome("partial" if failed_regions else "success", failed_regions)
        record_compliance_report(report, duration)
    return report

def generate_organization_report(regions: List[str], mandatory_tags: List[str] = MANDATORY_TAGS, resource_types: List[str] = None):
    """
    Mock implementation of cross-account scanning.
    In a real scenario, this would:
    1. Call organizations:ListAccounts
    2. Iterate over accounts
    3. Use CrossAccountManager.assume_role(account_id)
    4. Pass the assumed session to generate_report
    """
    logger.info("Generating organization-wide report...")
    org_client = boto3.client('organizations')
    try:
        # Paginator for accounts would go here
        pass
    except Exception as e:
        logger.warning("Not in an AWS Organization or lack permissions. Generating local only.")
        
    return generate_report(regions, mandatory_tags, resource_types)

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

    resource_types = event.get("resource_types") or event.get("resources")

    if not resource_types and event.get("resource"):
        resource_types = [event.get("resource")]
    
    # Map friendly aliases to AWS types if needed
    if resource_types:
        final_types = []
        for t in resource_types:
            if t in RESOURCE_TYPE_MAP:
                final_types.append(RESOURCE_TYPE_MAP[t])
            else:
                final_types.append(t)
        resource_types = final_types

    report = generate_report(target_regions, mandatory_tags, resource_types)

    
    # Optional S3 Export
    bucket = event.get("export_bucket") or REPORT_BUCKET
    if bucket:
        try:
            s3 = _get_s3_client()
            key = f"tagging-reports/report-{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H-%M-%S')}.json"
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
        "mandatory_tags": ["Owner"]
    }
    print(json.dumps(lambda_handler(test_event, None), indent=2))
