from typing import Any, Dict, List, Optional

from botocore.exceptions import BotoCoreError, ClientError

from src.clients import get_tagging_client
from src.config import (
    DEFAULT_REGION, 
    TAG_API_BATCH_SIZE,
    GOVERNANCE_SCHEMA_PATH, 
    GOVERNANCE_UNKNOWN_TAGS, 
    GOVERNANCE_STRICT_MODE,
    GOVERNANCE_NORMALIZATION
)
from src.logging_config import get_logger
from src.governance.engine import TagGovernanceEngine
from src.governance.schema_provider import FileSchemaProvider
from src.observability.metrics import record_tag_writes
from src import protected_tags

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


def build_response(status_code: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "body": body,
    }


def get_region_from_arn(arn: str) -> Optional[str]:
    """Extract region from ARN (4th element)."""
    if not arn or not isinstance(arn, str):
        return None
    parts = arn.split(":")
    if len(parts) >= 4 and parts[3]:
        return parts[3]
    return None


def tag_resources(arns: List[str], tags: Dict[str, str], default_region: str) -> Dict[str, Any]:
    """
    Tag resources across multiple regions.
    Groups ARNs by region and uses the appropriate client.
    """
    # Group by region
    region_groups: Dict[str, List[str]] = {}
    for arn in arns:
        reg = get_region_from_arn(arn) or default_region
        region_groups.setdefault(reg, []).append(arn)

    results = {
        "tagged_count": 0,
        "failed_resources": {}
    }

    for reg, reg_arns in region_groups.items():
        try:
            client = get_client(reg)
            # TagResources accepts at most 20 ARNs per call
            batch_size = max(1, min(TAG_API_BATCH_SIZE, 20))
            for i in range(0, len(reg_arns), batch_size):
                batch = reg_arns[i : i + batch_size]
                resp = client.tag_resources(ResourceARNList=batch, Tags=tags)
                
                failed = resp.get("FailedResourcesMap", {})
                results["failed_resources"].update(failed)
                results["tagged_count"] += (len(batch) - len(failed))
                protected_tags.record_writes([a for a in batch if a not in failed], set_tags=tags, source="tag-write")
                
        except Exception as e:
            logger.error("Failed to tag resources in region %s: %s", reg, e)
            for a in reg_arns:
                results["failed_resources"][a] = {"ErrorCode": "ClientError", "ErrorMessage": str(e)}

    return results


def collect_arns(event: Dict[str, Any], validate: bool = True) -> List[str]:
    """ARNs from 'arn' / 'resource_arn' (single) and 'arns' (list), de-duplicated, order kept."""
    raw: List[Any] = []
    for key in ("arn", "resource_arn"):
        if event.get(key):
            raw.append(event[key])
    arns_field = event.get("arns") or []
    if isinstance(arns_field, str):
        arns_field = [arns_field]
    if not isinstance(arns_field, list):
        raise ValueError("'arns' must be a list of ARNs.")
    raw.extend(arns_field)

    out: List[str] = []
    for a in raw:
        a = str(a).strip()
        if validate and not a.startswith("arn:"):
            raise ValueError(f"Invalid ARN: {a!r}")
        if a not in out:
            out.append(a)
    return out


def lambda_handler(event, context):
    logger.info("Received event: %s", event)

    tags = event.get("tags", {})
    region = str(event.get("region", DEFAULT_REGION)).strip()

    try:
        arns = collect_arns(event)
        if not arns:
            raise ValueError("Field 'arn' or 'arns' is required.")
        if not tags or not isinstance(tags, dict):
            raise ValueError("Field 'tags' must be a non-empty object.")
        bad_keys = [k for k in tags if not isinstance(k, str) or not k.strip() or k.lower().startswith("aws:")]
        if bad_keys:
            raise ValueError(f"Invalid tag keys (empty or reserved 'aws:' prefix): {bad_keys}")
        if any(not isinstance(v, (str, int, float)) for v in tags.values()):
            raise ValueError("Tag values must be strings.")
        tags = {k.strip(): str(v).strip() for k, v in tags.items()}

        # Governance Engine: Normalize and Validate.
        # A write is a partial update: only the provided keys are validated; tags already
        # on the resource are untouched, so required-tag checks don't apply here.
        validation_result = governance_engine.evaluate(tags, partial=True)
        
        if GOVERNANCE_STRICT_MODE and not validation_result.compliant:
            logger.warning("Tag validation failed in strict mode: %s", validation_result.violations)
            return build_response(400, {
                "message": "Tag validation failed",
                "violations": validation_result.to_dict()["violations"]
            })
            
        if not validation_result.compliant:
            logger.warning("Tag validation warnings (proceeding because strict mode is off): %s", validation_result.violations)

        tags_to_apply = validation_result.normalized_tags

        result = tag_resources(arns, tags_to_apply, region)
        failed_count = len(result["failed_resources"])
        record_tag_writes(result["tagged_count"], failed_count)

        if result["failed_resources"]:
            return build_response(207, {
                "message": "Partial success",
                "details": result
            })

        return build_response(200, {
            "message": "Successfully tagged resources",
            "count": result["tagged_count"],
            "applied_tags": tags_to_apply,
            "warnings": [w.to_dict() for w in validation_result.warnings],
        })

    except ValueError as e:
        logger.warning("Validation error: %s", e)
        return build_response(400, {"message": str(e)})

    except (ClientError, BotoCoreError) as e:
        logger.exception("AWS error while tagging resources")
        return build_response(500, {
            "message": "Failed to tag resources.",
            "error": str(e)
        })

    except Exception as e:
        logger.exception("Unexpected error while tagging resources")
        return build_response(500, {
            "message": "Unexpected error occurred.",
            "error": str(e)
        })

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
