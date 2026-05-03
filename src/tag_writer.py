from typing import Any, Dict, List, Optional

from botocore.exceptions import BotoCoreError, ClientError

from src.clients import get_tagging_client
from src.config import DEFAULT_REGION
from src.logging_config import get_logger

logger = get_logger(__name__)


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
            # Max 20 resources per call
            BATCH_SIZE = 20
            for i in range(0, len(reg_arns), BATCH_SIZE):
                batch = reg_arns[i : i + BATCH_SIZE]
                resp = client.tag_resources(ResourceARNList=batch, Tags=tags)
                
                failed = resp.get("FailedResourcesMap", {})
                results["failed_resources"].update(failed)
                results["tagged_count"] += (len(batch) - len(failed))
                
        except Exception as e:
            logger.error("Failed to tag resources in region %s: %s", reg, e)
            for a in reg_arns:
                results["failed_resources"][a] = {"ErrorCode": "ClientError", "ErrorMessage": str(e)}

    return results


def lambda_handler(event, context):
    logger.info("Received event: %s", event)

    # Input can be single ARN or list of ARNs
    arn = event.get("arn")
    arns = event.get("arns", [])
    if arn:
        arns.append(arn)
    
    tags = event.get("tags", {})
    region = str(event.get("region", DEFAULT_REGION)).strip()

    try:
        if not arns:
            raise ValueError("Field 'arn' or 'arns' is required.")
        if not tags or not isinstance(tags, dict):
            raise ValueError("Field 'tags' must be a non-empty object.")

        result = tag_resources(arns, tags, region)

        if result["failed_resources"]:
            return build_response(207, {
                "message": "Partial success",
                "details": result
            })

        return build_response(200, {
            "message": "Successfully tagged resources",
            "count": result["tagged_count"]
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
