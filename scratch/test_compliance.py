import sys
import os
sys.path.append(os.getcwd())
import json
from src.tag_report import generate_report
from src.config import MANDATORY_TAGS
print("MANDATORY_TAGS:", MANDATORY_TAGS)
res = generate_report(["us-east-2"], MANDATORY_TAGS)
print(json.dumps(res, indent=2))
