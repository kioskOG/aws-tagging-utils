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

def generate_report(target_regions: List[str], mandatory_tags: List[str], resource_types: List[str] = None) -> Dict[str, Any]:
    """
    Scan resources via the Resource Groups Tagging API and evaluate each one against
    the tag schema plus `mandatory_tags`.

    Note: GetResources only returns resources that are tagged or were tagged at some
    point; resources that never had any tag are not visible to this API.
    """
    started = time.perf_counter()
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mandatory_tags": mandatory_tags,
        "resource_filter": resource_types,
        "summary": {
            "total_resources": 0,
            "compliant": 0,
            "non_compliant": 0,
            "compliance_score": 0.0
        },
        "regions": {}
    }

    scan_types = resource_types if resource_types else list(RESOURCE_TYPE_MAP.values())
    
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
            page_iterator = paginator.paginate(ResourceTypeFilters=scan_types)

            
            for page in page_iterator:
                for item in page.get("ResourceTagMappingList", []):
                    arn = item.get("ResourceARN", "")
                    tags = {t["Key"]: t["Value"] for t in item.get("Tags", [])}
                    
                    validation_result = governance_engine.evaluate(
                        tags, resource_id=arn, extra_required=mandatory_tags
                    )
                    is_compliant = validation_result.compliant
                    
                    res_info = {
                        "ResourceARN": arn,
                        "IsCompliant": is_compliant,
                        "Violations": [v.to_dict() for v in validation_result.violations],
                        "Warnings": [w.to_dict() for w in validation_result.warnings],
                        "Tags": tags,
                        "NormalizedTags": validation_result.normalized_tags
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

    failed_regions = [r for r, d in report["regions"].items() if d.get("error")]
    report["summary"]["failed_regions"] = failed_regions
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
