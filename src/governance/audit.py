import json
import sys
from datetime import datetime, timezone
from src.logging_config import get_logger

logger = get_logger(__name__)

class AuditLogger:
    """
    Writes structured JSON logs to CloudWatch logs (via standard output).
    """
    
    @staticmethod
    def log(event_type: str, action: str, account_id: str, region: str, resource_id: str, resource_type: str, result: str, actor: str = "aws-tagging-utils", details: dict = None):
        payload = {
            "event_type": event_type,
            "action": action,
            "account_id": account_id,
            "region": region,
            "resource_id": resource_id,
            "resource_type": resource_type,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": actor
        }
        
        if details:
            payload["details"] = details
            
        # Write exactly one line of JSON for CloudWatch Logs processing
        print(json.dumps(payload), flush=True)
        # Also log to standard logger for debugging
        logger.info("AUDIT EVENT: %s - %s - %s", event_type, action, result)
