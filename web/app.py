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
import time
import logging
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, g

logger = logging.getLogger(__name__)

# Project root: aws-tagging-utils/
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.tag_read import RESOURCE_TYPE_MAP, lambda_handler as read_handler
from src.tag_writer import lambda_handler as write_handler
from src.tag_on_create import lambda_handler as gov_handler
from src.tag_report import lambda_handler as report_handler
from src.tag_sync import lambda_handler as sync_handler
from src.governance.schema_provider import FileSchemaProvider
from src.config import GOVERNANCE_SCHEMA_PATH

schema_provider = FileSchemaProvider(GOVERNANCE_SCHEMA_PATH)

app = Flask(__name__, template_folder="templates", static_folder="static")


from src.errors import register_error_handlers, APIError
from src.config import validate_config

register_error_handlers(app)
validate_config()

@app.before_request
def start_timer():
    g.start_time = time.perf_counter()

@app.after_request
def log_and_time_response(response):
    if hasattr(g, 'start_time'):
        elapsed = time.perf_counter() - g.start_time
        duration_ms = elapsed * 1000
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
        
        if duration_ms > 1000:
            req_id = getattr(g, 'request_id', 'unknown')
            logger.warning(
                "Slow API request\n"
                f"request_id={req_id}\n"
                f"method={request.method}\n"
                f"path={request.path}\n"
                f"status={response.status_code}\n"
                f"duration_ms={duration_ms:.2f}"
            )
    return response

def _lambda_result_to_response(result: dict) -> tuple[Response, int]:
    status = int(result.get("statusCode", 500))
    body = result.get("body", {})
    
    if status >= 400:
        if isinstance(body, dict):
            # If it's an expected validation error
            if status == 400:
                raise APIError(body.get("message", "Bad Request"), status_code=400, error_code="VALIDATION_ERROR")
            # For other lambda errors, we just wrap them
            raise APIError(body.get("message", "An internal error occurred"), status_code=status)
        raise APIError(str(body), status_code=status)
        
    if isinstance(body, (dict, list)):
        return jsonify(body), status
    return Response(str(body), mimetype="text/plain"), status


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    """Lightweight health check for ALB / ECS. Does not call AWS APIs."""
    return jsonify({"status": "ok"}), 200


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
    
    # Task 7 - Audit Logging
    status = int(result.get("statusCode", 500))
    if status in (200, 207):
        try:
            from src.db import insert_audit_log
            
            # Extract requested ARNs
            arns = []
            if payload.get("arn"):
                arns.append(payload.get("arn"))
            if payload.get("arns"):
                arns.extend(payload.get("arns"))
                
            tags = payload.get("tags", {})
            
            # Identify failed resources in case of partial success
            failed_arns = set()
            if status == 207:
                failed_arns = set(result.get("body", {}).get("details", {}).get("failed_resources", {}).keys())
                
            req_id = getattr(g, 'request_id', None)
            
            # Create an audit record for each resource
            for arn in arns:
                res = "FAILED" if arn in failed_arns else "SUCCESS"
                
                # We only record SUCCESS writes per prompt, or FAILED if workflow supports it securely
                # The prompt explicitly requires "An audit database failure must not cause an otherwise successful AWS tag write to be reported as failed."
                details = {
                    "tags_requested": tags
                }
                
                insert_audit_log(
                    actor="anonymous",
                    action="TAG_WRITE",
                    resource_arn=arn,
                    result=res,
                    details=details,
                    request_id=req_id
                )
        except Exception as e:
            # Must not fail the AWS write
            logger.error(f"Failed to persist audit log: {str(e)}", extra={"correlation_id": getattr(g, 'request_id', None)})
            
    return _lambda_result_to_response(result)


@app.post("/api/gov")
def api_gov():
    payload = request.get_json(force=True, silent=True) or {}
    result = gov_handler(payload, None)
    return _lambda_result_to_response(result)


from src.cache_manager import (
    get_cached_report, save_report, background_refresh,
    is_refreshing, is_cache_stale, get_refresh_error,
)
import threading

@app.post("/api/report")
def api_report():
    payload = request.get_json(force=True, silent=True) or {}
    
    # We will trigger the background refresh, but wait for it if this is the explicit legacy report tool
    # Wait, the legacy report tool expects the full JSON immediately.
    # So for /api/report, we will run the report handler synchronously and save the cache.
    result = report_handler(payload, None)
    if int(result.get("statusCode", 500)) == 200:
        save_report(result.get("body", {}))
    return _lambda_result_to_response(result)


@app.post("/api/sync")
def api_sync():
    payload = request.get_json(force=True, silent=True) or {}
    result = sync_handler(payload, None)
    return _lambda_result_to_response(result)

@app.post("/api/compliance/refresh")
def api_compliance_refresh():
    if is_refreshing():
        return jsonify({"status": "already_refreshing"}), 200

    payload = request.get_json(force=True, silent=True) or {}
    regions = payload.get("regions")
    mandatory_tags = payload.get("mandatory_tags")

    t = threading.Thread(target=background_refresh, args=(regions, mandatory_tags), daemon=True)
    t.start()
    return jsonify({"status": "started"}), 202


@app.get("/api/compliance/status")
def api_compliance_status():
    err = get_refresh_error()
    return jsonify({
        "is_refreshing": is_refreshing(),
        "is_stale": is_cache_stale(),
        "last_refresh_error": err,
    })


@app.get("/api/compliance/summary")
def api_compliance_summary():
    """Fast endpoint: returns only the summary block from cached data."""
    report = get_cached_report()
    if not report:
        return jsonify({"no_data": True, "_meta": {"status": "No data"}})
    summary = report.get("summary", {})
    return jsonify({
        "compliance_pct": summary.get("compliance_score", 0.0),
        "total_resources": summary.get("total_resources", 0),
        "compliant": summary.get("compliant", 0),
        "non_compliant": summary.get("non_compliant", 0),
        "_meta": report.get("_meta", {}),
    })

# --- Enterprise Dashboard APIs (Backed by real data) ---

from src.config import DEFAULT_REGION, MANDATORY_TAGS

@app.get("/api/dashboard")
def api_dashboard():
    report = get_cached_report()
    if not report:
        # Return empty structure so UI can render immediately and trigger refresh
        report = {"summary": {}, "regions": {}, "_meta": {"status": "No data"}}
    
    # Check if AWS API failed
    for region, region_data in report.get("regions", {}).items():
        if "error" in region_data:
            logger.error(f"AWS Error in {region}: {region_data['error']}")
            
    summary = report.get("summary", {})
    
    return jsonify({
        "compliance_pct": summary.get("compliance_score", 0.0),
        "total_resources": summary.get("total_resources", 0),
        "compliant_resources": summary.get("compliant", 0),
        "non_compliant_resources": summary.get("non_compliant", 0),
        "active_violations": summary.get("non_compliant", 0),
        "pending_remediation": 0,
        "active_exemptions": 0,
        "protected_violations": 0,
        "total_spend": 0,
        "tagged_spend": 0,
        "unallocated_spend": 0,
        "allocation_pct": 0,
        "_meta": report.get("_meta", {})
    })

@app.get("/api/schema")
def api_schema():
    schema = schema_provider.get_schema()
    result = []
    for tag, rule in schema.items():
        result.append({
            "tag": tag,
            "required": rule.required,
            "protected": getattr(rule, 'protected', False),
            "finops": True if getattr(rule, 'finops', None) else False,
            "allowed_values": rule.allowed_values or [],
            "regex": rule.pattern or "",
            "description": rule.description
        })
    return jsonify(result)

@app.get("/api/compliance")
def api_compliance():
    report = get_cached_report()
    if not report:
        report = {"summary": {}, "regions": {}, "_meta": {"status": "No data"}}
        
    resources = []
    for region, region_data in report.get("regions", {}).items():
        if "error" in region_data:
            logger.error(f"AWS Error in {region}: {region_data['error']}")
            
        for res in region_data.get("resources", []):
            violations = res.get("Violations", [])
            missing_tags = [v.get("tag") for v in violations if v.get("type") == "MISSING_REQUIRED"]
            invalid_tags = [v.get("tag") for v in violations if v.get("type") != "MISSING_REQUIRED"]
            
            resources.append({
                "id": res.get("ResourceARN", "unknown"),
                "type": res.get("ResourceARN", "unknown").split(":")[2] if ":" in res.get("ResourceARN", "") else "unknown",
                "account": res.get("ResourceARN", "").split(":")[4] if len(res.get("ResourceARN", "").split(":")) > 4 else "unknown",
                "region": region,
                "status": "COMPLIANT" if res.get("IsCompliant") else "NON_COMPLIANT",
                "missing_tags": missing_tags,
                "invalid_tags": invalid_tags,
                "protected_violations": [],
                "remediation_state": "None",
                "exemption_state": "None"
            })
    return jsonify({"resources": resources, "_meta": report.get("_meta", {})})

from src.finops.cache_manager import (
    get_cached_report as finops_get_cached_report,
    is_cache_stale as finops_is_cache_stale,
    is_refreshing as finops_is_refreshing,
    get_refresh_error as finops_get_refresh_error,
    background_refresh as finops_background_refresh
)

@app.get("/api/finops")
def api_finops():
    report = finops_get_cached_report()
    
    # Determine stale state
    stale = finops_is_cache_stale()
    
    if not report:
        # If no report exists, trigger refresh if not running
        if not finops_is_refreshing():
            t = threading.Thread(target=finops_background_refresh, daemon=True)
            t.start()
        return jsonify({
            "error": "not_ready", 
            "message": "FinOps data is refreshing. Please check status endpoint.",
            "_meta": {"status": "Refreshing"}
        }), 202
        
    if stale and not finops_is_refreshing():
        t = threading.Thread(target=finops_background_refresh, daemon=True)
        t.start()
        
    return jsonify(report)

@app.post("/api/finops/refresh")
def api_finops_refresh():
    if finops_is_refreshing():
        return jsonify({"status": "already_refreshing"}), 200

    t = threading.Thread(target=finops_background_refresh, daemon=True)
    t.start()
    return jsonify({"status": "started"}), 202

@app.get("/api/finops/status")
def api_finops_status():
    err = finops_get_refresh_error()
    return jsonify({
        "is_refreshing": finops_is_refreshing(),
        "is_stale": finops_is_cache_stale(),
        "last_refresh_error": err,
    })

@app.get("/api/security")
def api_security():
    raise APIError("Security reporting requires AWS Config and DynamoDB state persistence which are not currently configured.", status_code=501, error_code="NOT_IMPLEMENTED")

@app.get("/api/organization")
def api_organization():
    raise APIError("Cross-account organization scanning is not configured. Run TagSync or configure AWS Organizations.", status_code=501, error_code="NOT_IMPLEMENTED")

@app.get("/api/remediation")
def api_remediation():
    raise APIError("Remediation tasks require a configured DynamoDB state table and EventBridge.", status_code=501, error_code="NOT_IMPLEMENTED")

@app.get("/api/exemptions")
def api_exemptions():
    raise APIError("Exemptions require a configured DynamoDB state table.", status_code=501, error_code="NOT_IMPLEMENTED")

@app.get("/api/audit")
def api_audit():
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
        
    from src.db import get_recent_audit_logs
    records = get_recent_audit_logs(limit=limit)
    return jsonify({"audit_events": records, "_meta": {"count": len(records)}})

@app.get("/api/cicd")
def api_cicd():
    raise APIError("CI/CD metrics are not available in local run mode.", status_code=501, error_code="NOT_IMPLEMENTED")

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)

