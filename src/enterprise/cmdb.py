from abc import ABC, abstractmethod
from typing import Dict, Any

class CMDBProvider(ABC):
    @abstractmethod
    def get_application_metadata(self, app_id: str) -> Dict[str, Any]:
        pass
        
    @abstractmethod
    def get_owner_for_resource(self, resource_id: str) -> str:
        pass


class ServiceNowProvider(CMDBProvider):
    """
    Mock implementation of a ServiceNow CMDB integration.
    """
    def __init__(self, endpoint_url: str):
        self.endpoint_url = endpoint_url
        
    def get_application_metadata(self, app_id: str) -> Dict[str, Any]:
        # Simulated API call to ServiceNow
        return {
            "app_id": app_id,
            "cost_center": "CC-9999",
            "owner": "team-payments"
        }
        
    def get_owner_for_resource(self, resource_id: str) -> str:
        # Simulated API call
        return "team-infrastructure"
