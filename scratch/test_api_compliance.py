import urllib.request
import json
try:
    req = urllib.request.Request("http://127.0.0.1:5050/api/compliance")
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        print("Count:", len(data.get("resources", [])))
        if data.get("resources"):
            print("First item:", json.dumps(data["resources"][0], indent=2))
except Exception as e:
    print("Error:", e)
