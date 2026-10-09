import json
import boto3
from abc import ABC, abstractmethod
from typing import Dict, Any

from src.config import GOVERNANCE_SNS_TOPIC_ARN, DEFAULT_REGION
from src.logging_config import get_logger

logger = get_logger(__name__)

class NotificationProvider(ABC):
    @abstractmethod
    def notify(self, severity: str, message: str, context: Dict[str, Any]) -> bool:
        pass


class SNSNotificationProvider(NotificationProvider):
    def __init__(self, topic_arn: str = GOVERNANCE_SNS_TOPIC_ARN, region: str = DEFAULT_REGION):
        self.topic_arn = topic_arn
        self.region = region
        self.client = None
        
    def _get_client(self):
        if not self.client:
            self.client = boto3.client("sns", region_name=self.region)
        return self.client

    def notify(self, severity: str, message: str, context: Dict[str, Any]) -> bool:
        if not self.topic_arn:
            logger.warning("SNS topic ARN is not configured. Skipping notification.")
            return False
            
        try:
            client = self._get_client()
            payload = {
                "severity": severity,
                "message": message,
                "context": context
            }
            client.publish(
                TopicArn=self.topic_arn,
                Message=json.dumps(payload, indent=2),
                Subject=f"Tag Governance Alert: {severity} - {context.get('resource_id', 'Unknown Resource')}"
            )
            return True
        except Exception as e:
            logger.error("Failed to publish to SNS: %s", e)
            return False


class WebhookNotificationProvider(NotificationProvider):
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        
    def notify(self, severity: str, message: str, context: Dict[str, Any]) -> bool:
        # Avoid external dependencies like requests for now, use standard urllib
        import urllib.request
        import urllib.error
        
        if not self.webhook_url:
            return False
            
        payload = {
            "text": f"*{severity}*: {message}\n```{json.dumps(context, indent=2)}```"
        }
        
        req = urllib.request.Request(
            self.webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        
        try:
            with urllib.request.urlopen(req) as response:
                return response.status in (200, 201, 202, 204)
        except Exception as e:
            logger.error("Failed to send webhook notification: %s", e)
            return False
