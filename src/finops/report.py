import logging
from typing import Dict, Any
from src.finops.cost_explorer import CostExplorerClient
from src.governance.schema_provider import SchemaProvider
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class FinOpsReportGenerator:
    def __init__(self, ce_client: CostExplorerClient, schema_provider: SchemaProvider):
        self.ce_client = ce_client
        self.schema = schema_provider.get_schema()

    def generate_report(self) -> Dict[str, Any]:
        """
        Generates a FinOps report by correlating tag compliance with actual AWS billing data.
        NOTE: AWS Cost Explorer attribution has limitations. Not all spend can be tagged (e.g. data transfer).
        """
        logger.info("Generating FinOps Report...")
        
        # Calculate date range for current month
        today = datetime.today()
        start_date = today.replace(day=1).strftime('%Y-%m-%d')
        # Cost explorer end date is exclusive, so use tomorrow
        end_date = (today + timedelta(days=1)).strftime('%Y-%m-%d')
        
        report = {
            "TotalSpend": 0.0,
            "TaggedSpend": 0.0,
            "UntaggedSpend": 0.0,
            "AllocationPercentage": 0.0,
            "CostAllocationTags": [],
            "Warnings": [
                "AWS Cost Explorer data is up to 24 hours delayed.",
                "Not all AWS services support cost allocation tagging."
            ]
        }
        
        # 1. Get Total Spend
        total_data = self.ce_client.get_cost_and_usage(start_date, end_date)
        if total_data and 'ResultsByTime' in total_data and total_data['ResultsByTime']:
            total_spend = float(total_data['ResultsByTime'][0]['Total']['UnblendedCost']['Amount'])
            report["TotalSpend"] = total_spend
            
        # 2. Check FinOps tags
        finops_tags = [key for key, rule in self.schema.items() if rule.finops and rule.finops.get("cost_allocation")]
        report["CostAllocationTags"] = finops_tags
        
        import os
        primary_tag = os.environ.get("FINOPS_PRIMARY_TAG")
        if not primary_tag and finops_tags:
            primary_tag = finops_tags[0]
            
        if primary_tag:
            report["Warnings"].append(f"Global TaggedSpend is calculated using the primary canonical tag: '{primary_tag}'. Additional tags are informational and not independently summed to avoid double-counting.")
            
            tag_data = self.ce_client.get_cost_by_tag(primary_tag, start_date, end_date)
            
            tagged_spend = 0.0
            
            if tag_data and 'ResultsByTime' in tag_data:
                for result in tag_data['ResultsByTime']:
                    for group in result.get('Groups', []):
                        keys = group.get('Keys', [])
                        if not keys:
                            continue
                            
                        key_val = keys[0]
                        metrics = group.get('Metrics', {})
                        if 'UnblendedCost' not in metrics:
                            continue
                            
                        try:
                            amount = float(metrics['UnblendedCost'].get('Amount', 0.0))
                        except ValueError:
                            amount = 0.0
                        
                        # AWS returns 'TagKey$TagValue'. 
                        # If TagValue is empty, it means the resource is not tagged with this key (or value is empty).
                        if "$" in key_val:
                            tag_val = key_val.split("$", 1)[1]
                            if tag_val.strip():
                                tagged_spend += amount
                        else:
                            if key_val.strip():
                                tagged_spend += amount
                                
            report["TaggedSpend"] = tagged_spend
            report["UntaggedSpend"] = max(0.0, report["TotalSpend"] - tagged_spend)
            
            if report["TotalSpend"] > 0:
                report["AllocationPercentage"] = round((report["TaggedSpend"] / report["TotalSpend"]) * 100, 2)

        return report
