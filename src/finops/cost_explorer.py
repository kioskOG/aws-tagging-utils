import boto3
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class CostExplorerClient:
    def __init__(self):
        self.client = boto3.client('ce')

    def get_cost_and_usage(self, start_date: str, end_date: str, granularity: str = 'MONTHLY') -> Dict[str, Any]:
        """
        Wrapper around AWS ce:GetCostAndUsage
        """
        try:
            response = self.client.get_cost_and_usage(
                TimePeriod={
                    'Start': start_date,
                    'End': end_date
                },
                Granularity=granularity,
                Metrics=['UnblendedCost']
            )
            return response
        except Exception as e:
            logger.error(f"Error fetching cost and usage: {e}")
            raise

    def get_cost_by_tag(self, tag_key: str, start_date: str, end_date: str, granularity: str = 'MONTHLY') -> Dict[str, Any]:
        """
        Wrapper to group costs by a specific Cost Allocation Tag
        """
        try:
            response = self.client.get_cost_and_usage(
                TimePeriod={
                    'Start': start_date,
                    'End': end_date
                },
                Granularity=granularity,
                Metrics=['UnblendedCost'],
                GroupBy=[
                    {
                        'Type': 'TAG',
                        'Key': tag_key
                    }
                ]
            )
            return response
        except self.client.exceptions.DataUnavailableException:
            logger.warning(f"Data unavailable for tag {tag_key}. It might not be activated as a Cost Allocation Tag.")
            return {}
        except Exception as e:
            logger.error(f"Error fetching cost by tag {tag_key}: {e}")
            raise
