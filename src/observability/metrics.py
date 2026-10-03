import json
import time
from typing import Dict, Any, List

class CloudWatchMetrics:
    """
    Implements AWS CloudWatch Embedded Metric Format (EMF) to securely
    log metrics asynchronously without extra API calls.
    """
    @staticmethod
    def put_metric(namespace: str, metric_name: str, value: float, dimensions: Dict[str, str] = None, unit: str = "Count"):
        if dimensions is None:
            dimensions = {}
            
        timestamp = int(time.time() * 1000)
        
        emf_payload = {
            "_aws": {
                "Timestamp": timestamp,
                "CloudWatchMetrics": [
                    {
                        "Namespace": namespace,
                        "Dimensions": [list(dimensions.keys())],
                        "Metrics": [
                            {
                                "Name": metric_name,
                                "Unit": unit
                            }
                        ]
                    }
                ]
            },
            metric_name: value,
        }
        
        # Merge dimensions into root
        emf_payload.update(dimensions)
        
        # Output EMF payload to standard out (CloudWatch will process it)
        print(json.dumps(emf_payload))

# Helper wrappers for governance metrics
def metric_resource_scanned(account_id: str):
    CloudWatchMetrics.put_metric("TagGovernance", "ResourcesScanned", 1, {"AccountId": account_id})

def metric_resource_compliant(account_id: str):
    CloudWatchMetrics.put_metric("TagGovernance", "CompliantResources", 1, {"AccountId": account_id})

def metric_resource_non_compliant(account_id: str):
    CloudWatchMetrics.put_metric("TagGovernance", "NonCompliantResources", 1, {"AccountId": account_id})

def metric_remediation_executed(action_type: str):
    CloudWatchMetrics.put_metric("TagGovernance", "RemediationsExecuted", 1, {"Action": action_type})

def metric_drift_detected():
    CloudWatchMetrics.put_metric("TagGovernance", "DriftEventsDetected", 1)
