from typing import Any, Dict, List

from botocore.exceptions import BotoCoreError, ClientError

from src.clients import get_tagging_client
from src.config import DEFAULT_REGION, TAG_API_BATCH_SIZE as TAG_RESOURCE_ARN_BATCH_SIZE
from src.logging_config import get_logger

logger = get_logger(__name__)


def get_client(region: str):
    return get_tagging_client(region)


def build_response(status_code: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "body": body,
    }


def normalize_tags(tags: Dict[str, Any]) -> Dict[str, str]:
    """
    Validate and normalize tags input.

    Example input:
        {
          "org": "finance",
          "service": "billing",
          "pod": "pod-a"
        }
    """
    if not isinstance(tags, dict) or not tags:
        raise ValueError("'tags' must be a non-empty object")

    normalized: Dict[str, str] = {}

    for key, value in tags.items():
        key_str = str(key).strip()

        if not key_str:
            raise ValueError("Tag keys must be non-empty strings")

        if value is None:
            raise ValueError(f"Tag '{key_str}' has a null value")

        value_str = str(value).strip()
        normalized[key_str] = value_str

    return normalized


def normalize_arn_list(event: Dict[str, Any]) -> List[str]:
    """
    Build a deduplicated ordered list of ARNs from 'arn' (string) or 'arns' (list).
    """
    arns_raw = event.get("arns")
    single = event.get("arn")

    out: List[str] = []
    if arns_raw is not None:
        if not isinstance(arns_raw, list):
            raise ValueError("'arns' must be a list of ARN strings.")
        for item in arns_raw:
            s = str(item).strip()
            if s:
                out.append(s)
    elif single is not None:
        s = str(single).strip()
        if s:
            out.append(s)

    if not out:
        raise ValueError("Provide 'arn' (string) or 'arns' (non-empty list).")

    seen: set = set()
    unique: List[str] = []
    for a in out:
        if a not in seen:
            seen.add(a)
            unique.append(a)
    return unique


def tag_resources_batched(
    arns: List[str], tags: Dict[str, str], rg_tag_client
) -> Dict[str, Any]:
    failed: Dict[str, Any] = {}
    for i in range(0, len(arns), TAG_RESOURCE_ARN_BATCH_SIZE):
        chunk = arns[i : i + TAG_RESOURCE_ARN_BATCH_SIZE]
        response = rg_tag_client.tag_resources(
            ResourceARNList=chunk,
            Tags=tags,
        )
        failed.update(response.get("FailedResourcesMap") or {})
    return {"FailedResourcesMap": failed}


def get_region_from_arn(arn: str, default: str) -> str:
    """Extract region from ARN (4th element). Fallback to default if not present/empty."""
    if not arn or not isinstance(arn, str):
        return default
    parts = arn.split(":")
    if len(parts) >= 4 and parts[3]:
        return parts[3]
    return default


def lambda_handler(event, context):
    logger.info("Received event: %s", event)

    default_region = str(event.get("region", DEFAULT_REGION)).strip()
    tags = event.get("tags", {})

    try:
        arn_list = normalize_arn_list(event)
        normalized_tags = normalize_tags(tags)
        
        # Group ARNs by region
        region_groups: Dict[str, List[str]] = {}
        for arn in arn_list:
            reg = get_region_from_arn(arn, default_region)
            region_groups.setdefault(reg, []).append(arn)

        all_failed: Dict[str, Any] = {}
        for reg, reg_arns in region_groups.items():
            try:
                rg_tag_client = get_client(reg)
                response = tag_resources_batched(reg_arns, normalized_tags, rg_tag_client)
                all_failed.update(response.get("FailedResourcesMap", {}))
            except Exception as e:
                logger.error("Failed to tag resources in region %s: %s", reg, e)
                for arn in reg_arns:
                    all_failed[arn] = {"ErrorMessage": str(e)}

        if all_failed:
            logger.warning("Tagging completed with failures: %s", all_failed)
            return build_response(
                207,
                {
                    "message": "Tagging completed with partial failures.",
                    "arn_count": len(arn_list),
                    "tags": normalized_tags,
                    "failed_resources": all_failed,
                },
            )

        return build_response(
            200,
            {
                "message": "Tags applied successfully across regions.",
                "arn_count": len(arn_list),
                "arns": arn_list,
                "tags": normalized_tags,
            },
        )

    except ValueError as e:
        logger.warning("Validation error: %s", e)
        return build_response(400, {"message": str(e)})

    except (ClientError, BotoCoreError) as e:
        logger.exception("AWS error while tagging resource")
        return build_response(
            500,
            {
                "message": "Failed to tag resource in AWS.",
                "error": str(e),
            },
        )

    except Exception as e:
        logger.exception("Unexpected error while tagging resource")
        return build_response(
            500,
            {
                "message": "Unexpected error occurred.",
                "error": str(e),
            },
        )

# executable directly with python src/tag_writer.py
if __name__ == "__main__":
    sample_event = {
        "arn": "arn:aws:ec2:ap-southeast-1:547580490325:instance/i-03a7beb7702ef226d",
        "region": "ap-southeast-1",
        "tags": {
            "environment": "dev",
            "owner": "devops"
        }
    }
    print(lambda_handler(sample_event, None))
