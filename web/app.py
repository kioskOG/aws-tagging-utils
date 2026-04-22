"""
Local web UI for AWS tagging utilities (tag read / write).

Run from project root:
  pip install -r requirements.txt
  python -m web.app

Or:
  flask --app web.app run --debug
"""

from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

# Project root: aws-tagging-utils/
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.tag_read import RESOURCE_TYPE_MAP, lambda_handler as read_handler
from src.tag_writer import lambda_handler as write_handler
from src.tag_on_create import lambda_handler as gov_handler
from src.tag_report import lambda_handler as report_handler
from src.tag_sync import lambda_handler as sync_handler

app = Flask(__name__, template_folder="templates", static_folder=None)


def _lambda_result_to_response(result: dict) -> tuple[Response, int]:
    status = int(result.get("statusCode", 500))
    body = result.get("body", {})
    if isinstance(body, (dict, list)):
        return jsonify(body), status
    return Response(str(body), mimetype="text/plain"), status


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/meta/resource-types")
def resource_types():
    aliases = sorted(RESOURCE_TYPE_MAP.keys())
    return jsonify({"aliases": aliases, "map": RESOURCE_TYPE_MAP})


@app.post("/api/read")
def api_read():
    payload = request.get_json(force=True, silent=True) or {}
    result = read_handler(payload, None)
    return _lambda_result_to_response(result)


@app.post("/api/write")
def api_write():
    payload = request.get_json(force=True, silent=True) or {}
    result = write_handler(payload, None)
    return _lambda_result_to_response(result)


@app.post("/api/gov")
def api_gov():
    payload = request.get_json(force=True, silent=True) or {}
    result = gov_handler(payload, None)
    return _lambda_result_to_response(result)


@app.post("/api/report")
def api_report():
    payload = request.get_json(force=True, silent=True) or {}
    result = report_handler(payload, None)
    return _lambda_result_to_response(result)


@app.post("/api/sync")
def api_sync():
    payload = request.get_json(force=True, silent=True) or {}
    result = sync_handler(payload, None)
    return _lambda_result_to_response(result)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
