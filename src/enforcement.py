import json
from typing import Dict, Any

from src.config import (
    GOVERNANCE_REMEDIATION_ENABLED,
    GOVERNANCE_SCHEMA_PATH, 
    GOVERNANCE_UNKNOWN_TAGS, 
    GOVERNANCE_NORMALIZATION
)
from src.governance.schema_provider import FileSchemaProvider
from src.governance.engine import TagGovernanceEngine
from src.governance.state import DynamoDBStateStore
from src.governance.exemptions import ExemptionManager
from src.governance.notifications import SNSNotificationProvider
from src.governance.remediation import RemediationEngine
from src.tag_on_create import parse_detail, EVENT_EXTRACTORS, _account_id, _region
from src.logging_config import get_logger

logger = get_logger(__name__)

# Initialize components globally to reuse across invocations
schema_provider = FileSchemaProvider(GOVERNANCE_SCHEMA_PATH)
governance_engine = TagGovernanceEngine(
    schema_provider=schema_provider,
    unknown_tags_behavior=GOVERNANCE_UNKNOWN_TAGS,
    enable_normalization=GOVERNANCE_NORMALIZATION
)

state_store = DynamoDBStateStore()
exemption_manager = ExemptionManager(state_store)
# In production, load actual exemptions
# exemption_manager.load_local_exemptions("config/exemptions.json")

notification_provider = SNSNotificationProvider()
remediation_engine = RemediationEngine(state_store, exemption_manager, notification_provider)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    EventBridge trigger for resource creation or tag modification.
    """
    logger.info("Received event: %s", event)
    
    # Simple EventBridge ResourceTagging event detection
    if event.get("source") == "aws.tag":
        # Tag Change Event
        return handle_tag_change_event(event)
        
    # CloudTrail creation event detection (reuse TagOnCreate extractors)
    detail, envelope = parse_detail(event)
    event_name = str(detail.get("eventName") or "")
    
    extractor = EVENT_EXTRACTORS.get(event_name)
    if not extractor:
        detail_type = envelope.get("detail-type") or event.get("detail-type") or ""
        # Tag Change (Drift) handling
        if detail_type == "Tag Change on Resource":
            return handle_tag_change_event(event)


        logger.warning(f"Unsupported event type: {detail_type}")
        return {"statusCode": 400, "body": "Unsupported event type"}
        
    arns = extractor(detail, envelope)
    arns = [a for a in arns if a]
    
    if not arns:
        return {"statusCode": 200, "message": "no_arns"}
        
    account_id = _account_id(detail, envelope)
    region = _region(detail, envelope)
    
    results = []
    # For each ARN, fetch current tags and process governance
    # (Since this is async, we need to fetch the tags ourselves, or rely on the event payload if available)
    from src.tag_on_create import fetch_tags_for_arns, get_tagging_client
    client = get_tagging_client(region)
    tag_maps = fetch_tags_for_arns(client, arns)
    
    for arn in arns:
        tags = tag_maps.get(arn, {})
        # Resource Type is 3rd element in ARN usually, or simple extraction
        parts = arn.split(":")
        res_type = parts[2] if len(parts) > 2 else "unknown"
        
        # Evaluate
        val_res = governance_engine.evaluate(tags, resource_id=arn)
        
        if not val_res.compliant and not GOVERNANCE_REMEDIATION_ENABLED:
            # Report-only mode: record the violation but don't mutate the resource
            results.append({"arn": arn, "remediation": {"status": "REPORT_ONLY",
                            "violations": [v.to_dict() for v in val_res.violations]}})
        elif not val_res.compliant:
            request_data = {
                "resource_arn": arn,
                "requested_tags": val_res.normalized_tags
            }
            req_id = getattr(context, "aws_request_id", "unknown") if context else "unknown"
            rem_res = remediation_engine.process_sync(request_data, actor="eventbridge", request_id=req_id)
            results.append({"arn": arn, "remediation": rem_res})
        else:
            results.append({"arn": arn, "remediation": {"status": "COMPLIANT"}})
        
    return {"statusCode": 200, "body": results}


def handle_tag_change_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Protected-tag drift: detect or revert changes made outside the app (see src.protected_tags)."""
    from src.protected_tags import handle_tag_change_event as handle_drift
    return handle_drift(event, exemption_manager=exemption_manager, notifier=notification_provider)

