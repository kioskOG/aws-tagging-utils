import json
import time
from unittest.mock import patch, MagicMock
from web.app import app

def manual_verification():
    app.config["TESTING"] = True
    client = app.test_client()

    mock_ce = MagicMock()
    mock_ce.get_cost_and_usage.return_value = {
        'ResultsByTime': [{'Total': {'UnblendedCost': {'Amount': '125400.00'}}}]
    }
    mock_ce.get_cost_by_tag.return_value = {
        'ResultsByTime': [{'Groups': [{'Keys': ['CostCenter$Engineering'], 'Metrics': {'UnblendedCost': {'Amount': '100000.00'}}}]}]
    }
    
    with patch("src.finops.cost_explorer.CostExplorerClient", return_value=mock_ce):
        print("1. GET /api/finops")
        r1 = client.get("/api/finops")
        print("Status:", r1.status_code)
        print("Body:", json.dumps(r1.get_json(), indent=2))
        
        print("\n2. POST /api/finops/refresh")
        r2 = client.post("/api/finops/refresh")
        print("Status:", r2.status_code)
        print("Body:", json.dumps(r2.get_json(), indent=2))
        
        print("\n3. GET /api/finops/status")
        r_status = client.get("/api/finops/status")
        print("Status:", r_status.status_code)
        print("Body:", json.dumps(r_status.get_json(), indent=2))
        
        # Wait for cache
        time.sleep(1)
        
        print("\n4. GET /api/finops (After refresh)")
        r3 = client.get("/api/finops")
        print("Status:", r3.status_code)
        report = r3.get_json()
        print(f"TotalSpend: {report.get('TotalSpend')} TaggedSpend: {report.get('TaggedSpend')}")
        print("_meta:", json.dumps(report.get("_meta", {}), indent=2))

manual_verification()
