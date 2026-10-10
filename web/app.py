"""
Web UI + REST API for AWS tagging utilities.

Run from project root:
  pip install -r requirements.txt
  python -m web.app                      # dev server

Production:
  gunicorn --workers 1 --threads 8 --bind 0.0.0.0:5050 web.app:app
  (one worker: compliance refresh state and caches are process-local)
"""

from __future__ import annotations

import csv
import io
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from flask import Flask, Response, jsonify, render_template, request, g

# Project root: aws-tagging-utils/
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.logging_config import get_logger
from src.tag_read import RESOURCE_TYPE_MAP, lambda_handler as read_handler
from src.tag_writer import collect_arns, lambda_handler as write_handler
from src.tag_on_create import lambda_handler as gov_handler
from src.tag_report import lambda_handler as report_handler
from src.tag_sync import lambda_handler as sync_handler
from src.governance.schema_provider import FileSchemaProvider
from src.governance.state import DynamoDBStateStore
from src.governance.exemptions import ExemptionManager
from src.config import (
    APP_VERSION,
    AUTH_MODE_DEFAULT,
    COMPLIANCE_SCAN_INTERVAL_SECONDS,
    DEFAULT_REGION,
    FINOPS_ENABLED,
    GOVERNANCE_REMEDIATION_ENABLED,
    GOVERNANCE_SCHEMA_PATH,
    MANDATORY_TAGS,
    METRICS_AUTH_TOKEN,
    OBSERVABILITY_ENABLED,
    validate_config,
)
from src.errors import register_error_handlers, APIError
from src.auth.decorators import require_auth, require_permission, require_tag_modify_permission
from src.auth.alb_oidc import get_alb_identity
from src.authorization.policy import AuthorizationPolicy
from src.observability import metrics

logger = get_logger(__name__)

schema_provider = FileSchemaProvider(GOVERNANCE_SCHEMA_PATH)
state_store = DynamoDBStateStore()
exemption_manager = ExemptionManager(state_store)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.json.sort_keys = False

register_error_handlers(app)
_AWS_STATE = validate_config()
_STARTED_AT = time.time()
metrics.set_build_info(version=APP_VERSION, python=sys.version.split()[0])


# ─────────────────────────────────────────────────────────────────────
# Request lifecycle: timing, metrics, security headers
# ─────────────────────────────────────────────────────────────────────

@app.before_request
def start_timer():
    g.start_time = time.perf_counter()


# No inline scripts or handlers: UI events go through data-click/data-change/data-input in app.js.
# Inline style attributes are still used by the templates, hence 'unsafe-inline' for styles only.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


@app.after_request
def log_and_time_response(response):
    if hasattr(g, 'start_time'):
        elapsed = time.perf_counter() - g.start_time
        duration_ms = elapsed * 1000
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"

        # Route template (not the raw path) keeps metric label cardinality bounded
        endpoint = request.url_rule.rule if request.url_rule else "unmatched"
        if endpoint not in ("/metrics", "/health", "/ready"):
            metrics.record_http(request.method, endpoint, response.status_code, elapsed)

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

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _lambda_result_to_response(result: dict) -> tuple[Response, int]:
    status = int(result.get("statusCode", 500))
    body = result.get("body", {})

    if status >= 400:
        if isinstance(body, dict):
            details = body.get("violations")
            # If it's an expected validation error
            if status == 400:
                raise APIError(body.get("message", "Bad Request"), status_code=400,
                               error_code="VALIDATION_ERROR", details=details)
            # For other lambda errors, we just wrap them
            raise APIError(body.get("message", "An internal error occurred"), status_code=status)
        raise APIError(str(body), status_code=status)

    if isinstance(body, (dict, list)):
        return jsonify(body), status
    return Response(str(body), mimetype="text/plain"), status


def _actor() -> str:
    identity = getattr(g, "identity", None)
    return getattr(identity, "user_id", None) or "anonymous"


class _TTLCache:
    """Tiny thread-safe cache for slow, optional lookups (e.g. DynamoDB-backed lists)."""

    def __init__(self, ttl: float):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._value: Any = None
        self._error: Optional[str] = None
        self._at = 0.0

    def get(self, loader: Callable[[], Any]) -> tuple[Any, Optional[str]]:
        with self._lock:
            if time.time() - self._at < self.ttl:
                return self._value, self._error
            try:
                self._value, self._error = loader(), None
            except Exception as e:  # cache failures too, so a missing table isn't hit per request
                self._value, self._error = None, getattr(e, "message", None) or str(e)
            self._at = time.time()
            return self._value, self._error

    def invalidate(self) -> None:
        with self._lock:
            self._at = 0.0


_exemptions_cache = _TTLCache(ttl=60)


# ─────────────────────────────────────────────────────────────────────
# Pages, health, metrics, docs
# ─────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html", version=APP_VERSION)


@app.get("/health")
def health():
    """Liveness probe for ALB / ECS / k8s. Does not call AWS APIs."""
    return jsonify({"status": "ok"}), 200


@app.get("/ready")
def ready():
    """Readiness probe: SQLite usable and schema loaded. Does not call AWS APIs."""
    checks: Dict[str, Any] = {}
    ok = True
    try:
        from src.db import get_connection, init_db
        init_db()
        get_connection().execute("SELECT 1").fetchone()
        checks["database"] = "ok"
    except Exception as e:
        ok = False
        checks["database"] = f"error: {type(e).__name__}"
    try:
        checks["schema_tags"] = len(schema_provider.get_schema())
    except Exception as e:
        ok = False
        checks["schema_tags"] = f"error: {type(e).__name__}"
    checks["aws_at_startup"] = _AWS_STATE
    checks["uptime_seconds"] = round(time.time() - _STARTED_AT)
    return jsonify({"status": "ready" if ok else "not_ready", "version": APP_VERSION, "checks": checks}), (200 if ok else 503)


@app.get("/metrics")
def prometheus_metrics():
    """Prometheus scrape endpoint."""
    if not OBSERVABILITY_ENABLED:
        raise APIError("Metrics are disabled (OBSERVABILITY_ENABLED=false).", status_code=404, error_code="FEATURE_DISABLED")
    if METRICS_AUTH_TOKEN:
        import hmac
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied, METRICS_AUTH_TOKEN):
            raise APIError("Invalid metrics token.", status_code=401, error_code="UNAUTHORIZED")
    _refresh_report_gauges_once()
    body, content_type = metrics.render_prometheus()
    return Response(body, mimetype=content_type.split(";")[0], headers={"Content-Type": content_type})


@app.get("/api/meta/resource-types")
def resource_types():
    aliases = sorted(RESOURCE_TYPE_MAP.keys())
    return jsonify({"aliases": aliases, "map": RESOURCE_TYPE_MAP})


import yaml


@app.get("/api/openapi.json")
def api_openapi_json():
    """Serves the OpenAPI specification."""
    openapi_path = _ROOT / "config" / "openapi.yaml"
    if not openapi_path.exists():
        raise APIError("OpenAPI specification not found.", status_code=404)

    with open(openapi_path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    return jsonify(spec)


@app.get("/api/docs")
def api_docs():
    """Serves the interactive Swagger UI."""
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>API Documentation</title>
      <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.9.0/swagger-ui.css" />
    </head>
    <body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5.9.0/swagger-ui-bundle.js" crossorigin></script>
    <script>
      window.onload = () => {
        window.ui = SwaggerUIBundle({
          url: '/api/openapi.json',
          dom_id: '#swagger-ui',
        });
      };
    </script>
    </body>
    </html>
    """
    return html


@app.get("/api/me")
def api_me():
    """Who am I and what may I do — drives the UI's permission-aware controls."""
    identity = get_alb_identity(dict(request.headers))
    if not identity:
        raise APIError("Unauthorized. ALB OIDC identity required.", status_code=401, error_code="UNAUTHORIZED")
    import os
    return jsonify({
        "user_id": identity.user_id,
        "email": getattr(identity, "email", None),
        "roles": identity.roles,
        "auth_mode": os.environ.get("AUTH_MODE", AUTH_MODE_DEFAULT),
        "permissions": {
            "modify_tags": AuthorizationPolicy.can_write_any_tags(identity),
            "view_finops": AuthorizationPolicy.can_view_finops(identity),
            "manage_exemptions": AuthorizationPolicy.can_manage_exemptions(identity),
            "manage_protected_tags": AuthorizationPolicy.can_manage_protected_tags(identity),
        },
        "features": {
            "finops": FINOPS_ENABLED,
            "remediation": GOVERNANCE_REMEDIATION_ENABLED,
            "metrics": OBSERVABILITY_ENABLED,
        },
        "default_region": DEFAULT_REGION,
        "mandatory_tags": MANDATORY_TAGS,
        "version": APP_VERSION,
    })


# ─────────────────────────────────────────────────────────────────────
# Tag tools (Lambda handlers exposed over HTTP)
# ─────────────────────────────────────────────────────────────────────

@app.post("/api/read")
@require_auth
def api_read():
    payload = request.get_json(force=True, silent=True) or {}
    result = read_handler(payload, None)
    return _lambda_result_to_response(result)


@app.post("/api/write")
@require_tag_modify_permission()
def api_write():
    payload = request.get_json(force=True, silent=True) or {}
    try:
        requested_arns = collect_arns(payload, validate=False)
    except ValueError:
        requested_arns = []  # the handler returns the validation error
    tags = payload.get("tags") if isinstance(payload.get("tags"), dict) else {}
    _authorize_tag_write(requested_arns, tags)
    result = write_handler(payload, None)

    status = int(result.get("statusCode", 500))
    if status in (200, 207):
        try:
            from src.db import insert_audit_log

            tags = payload.get("tags", {})
            failed_arns = set()
            if status == 207:
                failed_arns = set(result.get("body", {}).get("details", {}).get("failed_resources", {}).keys())

            req_id = getattr(g, 'request_id', None)
            # One audit record per resource. An audit DB failure must not turn a
            # successful AWS tag write into a reported failure.
            for arn in requested_arns:
                insert_audit_log(
                    actor=_actor(),
                    action="TAG_WRITE",
                    resource_arn=arn,
                    result="FAILED" if arn in failed_arns else "SUCCESS",
                    details={"tags_requested": tags},
                    request_id=req_id,
                )
        except Exception as e:
            logger.error(f"Failed to persist audit log: {str(e)}", extra={"correlation_id": getattr(g, 'request_id', None)})

    return _lambda_result_to_response(result)


@app.post("/api/gov")
@require_auth
def api_gov():
    payload = request.get_json(force=True, silent=True) or {}
    if not AuthorizationPolicy.can_write_all_resources(g.identity):
        raise APIError("Forbidden. Auto-tagging needs TagOperator or PlatformAdmin.", status_code=403, error_code="FORBIDDEN")
    # Raw CloudTrail events reach tag-on-create from EventBridge only; through the API the
    # creator identity in the event could be forged.
    if payload.get("action") != "scan":
        raise APIError("Only {\"action\": \"scan\"} is accepted here.", status_code=400, error_code="INVALID_REQUEST")
    from src.protected_tags import touched
    from src.config import OWNER_TAG_KEY
    if touched([OWNER_TAG_KEY]) and not AuthorizationPolicy.can_manage_protected_tags(g.identity):
        raise APIError(f"{OWNER_TAG_KEY} is protected; auto-tagging needs SecurityAdmin or PlatformAdmin.",
                       status_code=403, error_code="FORBIDDEN")
    result = gov_handler(payload, None)
    return _lambda_result_to_response(result)


from src.cache_manager import (
    get_cached_report, save_report, background_refresh,
    is_refreshing, is_cache_stale, get_refresh_error,
)


@app.post("/api/report")
@require_auth
def api_report():
    payload = request.get_json(force=True, silent=True) or {}

    # The explicit report tool returns the full JSON synchronously and refreshes the cache.
    result = report_handler(payload, None)
    if int(result.get("statusCode", 500)) == 200:
        body = result.get("body", {})
        regions = body.get("regions", {})
        # Don't overwrite the last good scan with a report where every region failed
        if regions and not all(d.get("error") for d in regions.values()):
            save_report(body)
    return _lambda_result_to_response(result)


def _authorize_sync_protected(payload: Dict[str, Any]) -> None:
    """Sync copies whatever the parent carries; dry-run it first when protected keys may be involved."""
    from src.protected_tags import protected_keys
    if not protected_keys() or AuthorizationPolicy.can_manage_protected_tags(g.identity):
        return
    dry = sync_handler({**payload, "dry_run": True, "action": "propagate",
                        "rule": payload.get("rule") or "vpc",
                        "parent": payload.get("parent") or payload.get("vpc_id")}, None)
    if int(dry.get("statusCode", 500)) != 200:
        return  # the real call reports the same error
    from src import propagation
    plan = propagation.fix_plan(dry.get("body", {}).get("findings", []), bool(payload.get("overwrite")))
    _authorize_bulk(plan["targets"])


@app.post("/api/sync")
@require_auth
def api_sync():
    payload = request.get_json(force=True, silent=True) or {}
    if not payload.get("dry_run"):
        if not AuthorizationPolicy.can_write_all_resources(g.identity):
            raise APIError("Forbidden. Insufficient permissions to modify tags.", status_code=403, error_code="FORBIDDEN")
        _authorize_sync_protected(payload)
    payload["actor"] = _actor()
    result = sync_handler(payload, None)
    return _lambda_result_to_response(result)


# ─────────────────────────────────────────────────────────────────────
# Compliance
# ─────────────────────────────────────────────────────────────────────

@app.post("/api/compliance/refresh")
@require_auth
def api_compliance_refresh():
    if is_refreshing():
        return jsonify({"status": "already_refreshing"}), 200

    payload = request.get_json(force=True, silent=True) or {}
    regions = payload.get("regions")
    if isinstance(regions, str):
        regions = [r.strip() for r in regions.split(",") if r.strip()]
    mandatory_tags = payload.get("mandatory_tags")

    t = threading.Thread(target=background_refresh, args=(regions, mandatory_tags), daemon=True)
    t.start()
    return jsonify({"status": "started"}), 202


@app.get("/api/compliance/status")
@require_auth
def api_compliance_status():
    err = get_refresh_error()
    return jsonify({
        "is_refreshing": is_refreshing(),
        "is_stale": is_cache_stale(),
        "last_refresh_error": err,
    })


@app.get("/api/compliance/summary")
@require_auth
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


@app.get("/api/compliance/history")
@require_auth
def api_compliance_history():
    """Compliance score per completed scan, oldest first (trend chart)."""
    from src.db import get_scan_history
    try:
        limit = max(1, min(int(request.args.get("limit", 30)), 500))
    except ValueError:
        limit = 30
    return jsonify({"scans": get_scan_history(limit)})


def _protected_tags() -> set:
    from src.protected_tags import protected_keys
    return protected_keys()


def _flatten_resources(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Cached report -> flat list of resource rows for the UI, API and CSV export."""
    protected = _protected_tags()
    rows = []
    for region, region_data in report.get("regions", {}).items():
        if region_data.get("error"):
            logger.error(f"AWS Error in {region}: {region_data['error']}")

        for res in region_data.get("resources", []):
            arn = res.get("ResourceARN", "unknown")
            parts = arn.split(":")
            violations = res.get("Violations", [])
            rows.append({
                "id": arn,
                "type": parts[2] if len(parts) > 2 else "unknown",
                "account": parts[4] if len(parts) > 4 else "unknown",
                "region": region,
                "status": "COMPLIANT" if res.get("IsCompliant") else "NON_COMPLIANT",
                "missing_tags": [v.get("tag") for v in violations if v.get("type") == "MISSING_REQUIRED"],
                "invalid_tags": [v.get("tag") for v in violations if v.get("type") != "MISSING_REQUIRED"],
                "tags": res.get("Tags", {}),
                "resource_type": res.get("ResourceType") or "",
                "never_tagged": bool(res.get("NeverTagged")),
                "violations": violations,
                "warnings": res.get("Warnings", []),
                "protected_violations": [v.get("tag") for v in violations if v.get("tag") in protected],
                "remediation_state": "None",
                "exemption_state": "None",
            })
    return rows


def _annotate_exemptions(rows: List[Dict[str, Any]]) -> Optional[str]:
    """Mark rows covered by an active exemption. Returns an error message if the lookup failed."""
    active, err = _exemptions_cache.get(exemption_manager.list_active_exemptions)
    if err or active is None:
        for r in rows:
            r["exemption_state"] = "Unknown"
        return err
    for r in rows:
        # Same resource_type convention as remediation: the ARN's service (e.g. "ec2")
        env = (r["tags"] or {}).get("Environment")
        ex = exemption_manager.is_exempt(r["account"], r["type"], r["id"], env, active=active)
        if ex:
            r["exemption_state"] = "EXEMPT"
            r["exemption_id"] = ex.get("id")
            r["exemption_expires_at"] = ex.get("expires_at")
    return None


@app.get("/api/compliance")
@require_auth
def api_compliance():
    report = get_cached_report()
    if not report:
        report = {"summary": {}, "regions": {}, "_meta": {"status": "No data"}}
    rows = _flatten_resources(report)
    body: Dict[str, Any] = {"resources": rows, "_meta": report.get("_meta", {})}
    if request.args.get("include_exemptions") in ("1", "true"):
        err = _annotate_exemptions(rows)
        if err:
            body["exemptions_error"] = err
    return jsonify(body)


@app.get("/api/compliance/export.csv")
@require_auth
def api_compliance_export():
    """Latest scan as CSV (one row per resource) for spreadsheets / audits."""
    report = get_cached_report() or {"regions": {}}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["arn", "account", "region", "service", "status", "never_tagged", "missing_tags",
                     "invalid_tags", "warnings", "tags"])
    for r in _flatten_resources(report):
        writer.writerow([
            r["id"], r["account"], r["region"], r["type"], r["status"], "yes" if r["never_tagged"] else "",
            ";".join(r["missing_tags"]),
            ";".join(f"{v.get('tag')}={v.get('type')}" for v in r["violations"] if v.get("type") != "MISSING_REQUIRED"),
            ";".join(w.get("tag", "") for w in r["warnings"]),
            ";".join(f"{k}={v}" for k, v in sorted((r["tags"] or {}).items())),
        ])
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="tag-compliance-{ts}.csv"'})


# ─────────────────────────────────────────────────────────────────────
# Dashboard & schema
# ─────────────────────────────────────────────────────────────────────

from src.finops.cache_manager import (
    get_cached_report as finops_get_cached_report,
    is_cache_stale as finops_is_cache_stale,
    is_refreshing as finops_is_refreshing,
    get_refresh_error as finops_get_refresh_error,
    background_refresh as finops_background_refresh
)


def _breakdowns(report: Dict[str, Any]) -> Dict[str, Any]:
    by_tag: Dict[tuple, int] = {}
    by_type: Dict[str, Dict[str, int]] = {}
    by_region: Dict[str, Dict[str, int]] = {}
    protected = _protected_tags()
    protected_count = 0
    for region, region_data in report.get("regions", {}).items():
        for res in region_data.get("resources", []):
            arn = res.get("ResourceARN", "")
            parts = arn.split(":")
            rtype = parts[2] if len(parts) > 2 else "unknown"
            ok = bool(res.get("IsCompliant"))
            for bucket, key in ((by_type, rtype), (by_region, region)):
                b = bucket.setdefault(key, {"total": 0, "compliant": 0})
                b["total"] += 1
                b["compliant"] += ok
            for v in res.get("Violations", []):
                k = (v.get("tag"), v.get("type"))
                by_tag[k] = by_tag.get(k, 0) + 1
                if v.get("tag") in protected:
                    protected_count += 1

    def rows(bucket):
        out = [{"name": k, "total": v["total"], "compliant": v["compliant"],
                "compliance_pct": round(v["compliant"] * 100 / v["total"], 1) if v["total"] else 100.0}
               for k, v in bucket.items()]
        return sorted(out, key=lambda r: (r["compliance_pct"], -r["total"]))

    top = sorted(({"tag": t, "type": ty, "count": n} for (t, ty), n in by_tag.items()),
                 key=lambda r: -r["count"])[:10]
    return {"top_violations": top, "by_service": rows(by_type), "by_region": rows(by_region),
            "protected_violations": protected_count}


@app.get("/api/dashboard")
@require_auth
def api_dashboard():
    report = get_cached_report()
    if not report:
        # Return empty structure so UI can render immediately and trigger refresh
        report = {"summary": {}, "regions": {}, "_meta": {"status": "No data"}}

    # Check if AWS API failed
    for region, region_data in report.get("regions", {}).items():
        if region_data.get("error"):
            logger.error(f"AWS Error in {region}: {region_data['error']}")

    summary = report.get("summary", {})
    extra = _breakdowns(report)

    spend = {"total_spend": 0, "tagged_spend": 0, "unallocated_spend": 0, "allocation_pct": 0}
    if FINOPS_ENABLED:
        try:
            fin = finops_get_cached_report() or {}
            total = float(fin.get("TotalSpend") or 0)
            tagged = float(fin.get("TaggedSpend") or 0)
            spend = {
                "total_spend": round(total, 2),
                "tagged_spend": round(tagged, 2),
                "unallocated_spend": round(float(fin.get("UntaggedSpend") or max(total - tagged, 0)), 2),
                "allocation_pct": round(tagged * 100 / total, 1) if total else 0,
            }
        except Exception as e:
            logger.warning("FinOps cache unavailable for dashboard: %s", e)

    return jsonify({
        "compliance_pct": summary.get("compliance_score", 0.0),
        "total_resources": summary.get("total_resources", 0),
        "compliant_resources": summary.get("compliant", 0),
        "non_compliant_resources": summary.get("non_compliant", 0),
        "active_violations": summary.get("non_compliant", 0),
        # Filled by the UI from /api/remediation and /api/exemptions (DynamoDB-backed)
        "pending_remediation": None,
        "active_exemptions": None,
        "protected_violations": extra["protected_violations"],
        **spend,
        "top_violations": extra["top_violations"],
        "by_service": extra["by_service"],
        "by_region": extra["by_region"],
        "failed_regions": summary.get("failed_regions", []),
        "failed_accounts": summary.get("failed_accounts", []),
        "accounts_scanned": summary.get("accounts_scanned", 1),
        "coverage": _coverage_block(summary),
        "_meta": report.get("_meta", {})
    })


@app.get("/api/schema")
@require_auth
def api_schema():
    schema = schema_provider.get_schema()
    result = []
    for tag, rule in schema.items():
        result.append({
            "tag": tag,
            "required": rule.required or tag in MANDATORY_TAGS,
            "protected": getattr(rule, 'protected', False),
            "finops": True if getattr(rule, 'finops', None) else False,
            "allowed_values": rule.allowed_values or [],
            "regex": rule.pattern or "",
            "aliases": rule.aliases or [],
            "description": rule.description or ""
        })
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────
# FinOps
# ─────────────────────────────────────────────────────────────────────

def _require_finops_enabled():
    if not FINOPS_ENABLED:
        raise APIError("FinOps is disabled (FINOPS_ENABLED=false).", status_code=404, error_code="FEATURE_DISABLED")


@app.get("/api/finops")
@require_permission(AuthorizationPolicy.can_view_finops)
def api_finops():
    _require_finops_enabled()
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

    metrics.record_finops(report)
    return jsonify(report)


@app.post("/api/finops/refresh")
@require_permission(AuthorizationPolicy.can_view_finops)
def api_finops_refresh():
    _require_finops_enabled()
    if finops_is_refreshing():
        return jsonify({"status": "already_refreshing"}), 200

    t = threading.Thread(target=finops_background_refresh, daemon=True)
    t.start()
    return jsonify({"status": "started"}), 202


@app.get("/api/finops/status")
@require_permission(AuthorizationPolicy.can_view_finops)
def api_finops_status():
    _require_finops_enabled()
    err = finops_get_refresh_error()
    return jsonify({
        "is_refreshing": finops_is_refreshing(),
        "is_stale": finops_is_cache_stale(),
        "last_refresh_error": err,
    })


@app.get("/api/security")
@require_auth
def api_security():
    raise APIError("Security reporting requires AWS Config and DynamoDB state persistence which are not currently configured.", status_code=501, error_code="NOT_IMPLEMENTED")


@app.get("/api/organization")
@require_auth
def api_organization():
    """Accounts in the latest scan with names/OUs from the Organizations directory."""
    from src.aws_session import account_directory
    from src.config import COMPLIANCE_ACCOUNTS
    report = get_cached_report() or {}
    accounts = (report.get("summary") or {}).get("accounts") or {}
    try:
        directory = account_directory()
    except Exception:
        directory = {}
    rows = []
    for acct, a in accounts.items():
        d = directory.get(acct, {})
        rows.append({"account_id": acct, "name": d.get("name") or acct, "ou": d.get("ou_name") or "",
                     "total": a.get("total", 0), "compliant": a.get("compliant", 0),
                     "compliance_pct": a.get("compliance_score", 100.0), "errors": a.get("errors", {})})
    rows.sort(key=lambda r: r["compliance_pct"])
    return jsonify({"accounts": rows, "multi_account": bool(COMPLIANCE_ACCOUNTS),
                    "directory_size": len(directory)})


@app.post("/api/organization/refresh")
@require_permission(AuthorizationPolicy.can_manage_exemptions)
def api_organization_refresh():
    """Reload account names and OUs from AWS Organizations."""
    from src.aws_session import refresh_account_directory
    directory = refresh_account_directory()
    return jsonify({"accounts": len(directory)})


# ─────────────────────────────────────────────────────────────────────
# Inventory coverage, leaderboards, owner view
# ─────────────────────────────────────────────────────────────────────

def _coverage_block(summary: Dict[str, Any]) -> Dict[str, Any]:
    from src.config import COMPLIANCE_SCOPE, INVENTORY_SOURCE
    cov = summary.get("coverage") or {}
    unmapped = cov.get("unmapped_types") or {}
    return {
        "inventory_source": cov.get("inventory_source", INVENTORY_SOURCE),
        "scope": cov.get("scope", COMPLIANCE_SCOPE),
        "never_tagged": cov.get("never_tagged", 0),
        "unmapped_resources": sum(unmapped.values()),
        "unmapped_types": sorted(({"resource_type": k, "count": v} for k, v in unmapped.items()),
                                 key=lambda r: (-r["count"], r["resource_type"])),
        "warnings": cov.get("warnings", []),
    }


@app.get("/api/inventory/coverage")
@require_auth
def api_inventory_coverage():
    """What the latest scan could and couldn't see: never-tagged resources and unmapped services."""
    report = get_cached_report() or {}
    block = _coverage_block(report.get("summary") or {})
    block["mapped_types"] = sorted(set(RESOURCE_TYPE_MAP.values()))
    block["hint"] = ("Unmapped types are discovered but not evaluated. Add them to RESOURCE_TYPE_MAP "
                     "(src/tag_read.py) or set COMPLIANCE_SCOPE=all to evaluate everything.")
    if block["inventory_source"] != "resource_explorer":
        block["hint"] += (" Resources that were never tagged are invisible to the tagging API; "
                          "set INVENTORY_SOURCE=resource_explorer to include them.")
    return jsonify(block)


@app.get("/api/leaderboard")
@require_auth
def api_leaderboard():
    from src.insights import DIMENSIONS, leaderboard
    dimension = request.args.get("dimension", "team")
    if dimension not in DIMENSIONS:
        raise APIError(f"dimension must be one of {list(DIMENSIONS)}", status_code=400, error_code="INVALID_REQUEST")
    try:
        days = max(1, min(int(request.args.get("compare_days", 7)), 90))
    except ValueError:
        days = 7
    return jsonify(leaderboard(dimension, days))


@app.get("/api/me/resources")
@require_auth
def api_my_resources():
    """Owner view. Admins may pass ?owner=<value> to see someone else's resources."""
    from src.insights import owner_identifiers, owner_view
    identifiers = owner_identifiers(g.identity)
    requested = (request.args.get("owner") or "").strip().lower()
    if requested:
        if not AuthorizationPolicy.can_manage_exemptions(g.identity):
            raise APIError("Only admins can view another owner's resources.", status_code=403, error_code="FORBIDDEN")
        identifiers = [requested]
    report = get_cached_report() or {"regions": {}}
    rows = _flatten_resources(report)
    active, _err = _exemptions_cache.get(exemption_manager.list_active_exemptions)
    include_cost = FINOPS_ENABLED and request.args.get("cost", "1") != "0"
    view = owner_view(identifiers, rows, active, exemption_manager, include_cost=include_cost)
    view["_meta"] = report.get("_meta", {})
    return jsonify(view)


# ─────────────────────────────────────────────────────────────────────
# Bulk fix (change sets) and suggestions
# ─────────────────────────────────────────────────────────────────────

def _bulk_targets(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """{"targets": [{arn, tags}]} or {"arns": [...], "tags": {...}}."""
    if payload.get("targets"):
        return payload["targets"]
    arns = payload.get("arns") or []
    tags = payload.get("tags") or {}
    if not isinstance(arns, list):
        raise APIError("'arns' must be a list", status_code=400, error_code="INVALID_REQUEST")
    return [{"arn": a, "tags": tags} for a in arns]


def _authorize_tag_write(arns: List[str], keys) -> None:
    """
    The single write check for every tag-changing endpoint:
    - admins/operators may write any resource, ApplicationOwners only resources whose *live*
      tags say they own them (never the tags in the request);
    - protected keys need SecurityAdmin or PlatformAdmin.
    """
    from src.authorization.ownership import OwnershipResolver
    from src.protected_tags import touched
    identity = g.identity
    if not AuthorizationPolicy.can_write_any_tags(identity):
        raise APIError("Forbidden. Insufficient permissions to modify tags.", status_code=403, error_code="FORBIDDEN")
    protected = touched(keys)
    if protected and not AuthorizationPolicy.can_manage_protected_tags(identity):
        raise APIError(f"Only SecurityAdmin or PlatformAdmin may change protected tags: {', '.join(sorted(protected))}",
                       status_code=403, error_code="FORBIDDEN", details={"protected_tags": sorted(protected)})
    if AuthorizationPolicy.can_write_all_resources(identity) or not arns:
        return
    from src.changesets import fetch_current_tags
    current = fetch_current_tags(list(arns))
    not_owned = [a for a, tags in current.items() if not OwnershipResolver.is_owner(identity, tags)]
    if not_owned:
        raise APIError(f"You don't own {len(not_owned)} of these resources.", status_code=403,
                       error_code="FORBIDDEN", details={"not_owned": not_owned[:20]})


def _authorize_bulk(targets: List[Dict[str, Any]]) -> None:
    _authorize_tag_write([t["arn"] for t in targets], {k for t in targets for k in (t.get("tags") or {})})


@app.post("/api/bulk/preview")
@require_auth
def api_bulk_preview():
    from src import changesets
    payload = request.get_json(force=True, silent=True) or {}
    targets = _bulk_targets(payload)
    return jsonify(changesets.preview(targets, bool(payload.get("overwrite"))))


@app.post("/api/bulk/apply")
@require_auth
def api_bulk_apply():
    from src import changesets
    payload = request.get_json(force=True, silent=True) or {}
    targets = _bulk_targets(payload)
    _authorize_bulk(changesets._validate_targets(targets))
    cs = changesets.apply(targets, bool(payload.get("overwrite")), _actor(), kind="bulk_fix",
                          description=str(payload.get("description") or "")[:300] or f"Bulk fix of {len(targets)} resources",
                          preview_token=payload.get("preview_token"), request_id=getattr(g, "request_id", None))
    return jsonify(cs), 201


@app.get("/api/changesets")
@require_auth
def api_list_changesets():
    from src import changesets
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 500))
    except ValueError:
        limit = 50
    return jsonify({"change_sets": changesets.list_change_sets(limit)})


@app.get("/api/changesets/<cs_id>")
@require_auth
def api_get_changeset(cs_id):
    from src import changesets
    cs = changesets.get_change_set(cs_id)
    if not cs:
        raise APIError("Change set not found", status_code=404, error_code="NOT_FOUND")
    return jsonify(cs)


@app.post("/api/changesets/<cs_id>/undo")
@require_tag_modify_permission()
def api_undo_changeset(cs_id):
    from src import changesets
    cs = changesets.get_change_set(cs_id)
    if cs:
        items = [i for i in cs["items"] if i.get("op") == "tags"]
        _authorize_tag_write([i["arn"] for i in items], {k for i in items for k in (i.get("after") or {})})
    return jsonify(changesets.undo(cs_id, _actor(), request_id=getattr(g, "request_id", None))), 201


@app.post("/api/suggestions")
@require_auth
def api_suggestions():
    from src.suggestions import suggest
    payload = request.get_json(force=True, silent=True) or {}
    arns = [a for a in payload.get("arns") or [] if isinstance(a, str)][:500]
    keys = [k for k in payload.get("keys") or [] if isinstance(k, str)][:20]
    if not arns or not keys:
        raise APIError("'arns' and 'keys' are required", status_code=400, error_code="INVALID_REQUEST")
    return jsonify(suggest(arns, keys, include_creator=bool(payload.get("include_creator"))))


# ─────────────────────────────────────────────────────────────────────
# Tag propagation
# ─────────────────────────────────────────────────────────────────────

@app.get("/api/propagation")
@require_auth
def api_propagation():
    from src import propagation
    run = propagation.latest_run()
    return jsonify({
        "rules": [{"name": k, "label": v["label"]} for k, v in propagation.RULES.items()],
        "is_running": propagation.is_running(),
        "last_error": propagation.last_error(),
        "run": run,
    })


@app.post("/api/propagation/run")
@require_auth
def api_propagation_run():
    from src import propagation
    from src.cache_manager import resolve_scan_regions
    payload = request.get_json(force=True, silent=True) or {}
    rules = payload.get("rules") or None
    if rules and any(r not in propagation.RULES for r in rules):
        raise APIError(f"rules must be within {sorted(propagation.RULES)}", status_code=400, error_code="INVALID_REQUEST")
    regions = payload.get("regions") or resolve_scan_regions()
    if isinstance(regions, str):
        regions = [r.strip() for r in regions.split(",") if r.strip()]
    started = propagation.run_in_background(rules, regions)
    return jsonify({"status": "started" if started else "already_running"}), 202 if started else 200


def _selected_findings(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    from src import propagation
    run = propagation.latest_run()
    if not run:
        raise APIError("Run a propagation check first.", status_code=409, error_code="NO_RUN")
    ids = set(payload.get("finding_ids") or [])
    findings = [f for f in run["findings"] if not ids or f["id"] in ids]
    if not findings:
        raise APIError("No matching findings.", status_code=400, error_code="INVALID_REQUEST")
    return findings


@app.post("/api/propagation/preview")
@require_auth
def api_propagation_preview():
    from src import changesets, propagation
    payload = request.get_json(force=True, silent=True) or {}
    plan = propagation.fix_plan(_selected_findings(payload), bool(payload.get("overwrite")))
    preview = changesets.preview(plan["targets"], bool(payload.get("overwrite"))) if plan["targets"] else \
        {"items": [], "summary": {"resources": 0}, "preview_token": changesets.plan_token([])}
    preview["config_changes"] = plan["ops"]
    return jsonify(preview)


@app.post("/api/propagation/apply")
@require_tag_modify_permission()
def api_propagation_apply():
    from src import changesets, propagation
    payload = request.get_json(force=True, silent=True) or {}
    overwrite = bool(payload.get("overwrite"))
    findings = _selected_findings(payload)
    plan = propagation.fix_plan(findings, overwrite)
    if plan["targets"]:
        _authorize_bulk(plan["targets"])
    cs = changesets.apply(plan["targets"], overwrite, _actor(), kind="propagation",
                          description=f"Propagation fix: {len(findings)} findings",
                          preview_token=payload.get("preview_token"), ops=plan["ops"],
                          request_id=getattr(g, "request_id", None))
    return jsonify(cs), 201


# ─────────────────────────────────────────────────────────────────────
# Remediation
# ─────────────────────────────────────────────────────────────────────

@app.get("/api/remediation")
@require_auth
def api_list_remediation():
    from src.enforcement import remediation_engine
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 500))
    except ValueError:
        limit = 100
    actions = remediation_engine.state_store.list_remediation_actions(limit=limit)
    pending = sum(1 for a in actions if a.get("status") in ("PENDING", "IN_PROGRESS", "FAILED_RETRYABLE"))
    return jsonify({"actions": actions, "pending": pending, "_meta": {"count": len(actions)}})


@app.get("/api/remediation/<action_id>")
@require_auth
def api_get_remediation(action_id):
    from src.enforcement import remediation_engine
    action = remediation_engine.state_store.get_remediation_action(action_id)
    if not action:
        raise APIError("Action not found", status_code=404, error_code="NOT_FOUND")
    return jsonify(action)


@app.post("/api/remediation")
@require_tag_modify_permission()
def api_post_remediation():
    if not GOVERNANCE_REMEDIATION_ENABLED:
        raise APIError("Remediation is disabled (GOVERNANCE_REMEDIATION_ENABLED=false).",
                       status_code=403, error_code="FEATURE_DISABLED")
    payload = request.get_json(force=True, silent=True)
    if not payload:
        raise APIError("Missing JSON payload", status_code=400, error_code="BAD_REQUEST")
    requested = payload.get("requested_tags") if isinstance(payload.get("requested_tags"), dict) else {}
    _authorize_tag_write([payload["resource_arn"]] if payload.get("resource_arn") else [], requested)

    from src.enforcement import remediation_engine
    req_id = getattr(g, 'request_id', 'unknown')

    try:
        res = remediation_engine.process_sync(payload, _actor(), req_id)
        return jsonify(res), 201 if res.get("status") == "COMPLETED" else 200
    except APIError as e:
        raise e
    except ValueError as e:
        raise APIError(str(e), status_code=400, error_code="INVALID_REQUEST")
    except Exception as e:
        logger.error("Failed to execute remediation: %s", e)
        raise APIError(str(e), status_code=500, error_code="INTERNAL_ERROR")


# ─────────────────────────────────────────────────────────────────────
# Exemptions
# ─────────────────────────────────────────────────────────────────────

@app.get("/api/exemptions")
@require_auth
def api_list_exemptions():
    active = exemption_manager.list_active_exemptions()
    return jsonify({"exemptions": active})


@app.post("/api/exemptions")
@require_auth
@require_permission(AuthorizationPolicy.can_manage_exemptions)
def api_create_exemption():
    payload = request.get_json(silent=True)
    if not payload or not isinstance(payload, dict):
        raise APIError("Missing JSON payload", status_code=400, error_code="BAD_REQUEST")

    from src.governance.audit import AuditLogger
    try:
        ex = exemption_manager.create_exemption(payload, g.identity.user_id)
        AuditLogger.log(
            event_type="EXEMPTION_CREATED",
            action="CREATE",
            account_id=ex.get("account_id") or "N/A",
            region="global",
            resource_id=ex.get("resource_id") or ex.get("resource_type") or ex.get("environment") or "N/A",
            resource_type="EXEMPTION",
            result="SUCCESS",
            actor=_actor(),
            details={"exemption_id": ex.get("id")}
        )
        _record_exemption_audit("EXEMPTION_CREATE", ex)
        _exemptions_cache.invalidate()
        metrics.record_exemption("create")
        return jsonify(ex), 201
    except APIError as e:
        raise e
    except Exception as e:
        logger.error("Failed to create exemption: %s", e)
        raise APIError(str(e), status_code=500, error_code="INTERNAL_ERROR")


@app.get("/api/exemptions/<exemption_id>")
@require_auth
def api_get_exemption(exemption_id):
    ex = exemption_manager.get_exemption(exemption_id)
    if not ex:
        raise APIError("Exemption not found", status_code=404, error_code="NOT_FOUND")
    return jsonify(ex)


@app.delete("/api/exemptions/<exemption_id>")
@require_auth
@require_permission(AuthorizationPolicy.can_manage_exemptions)
def api_delete_exemption(exemption_id):
    from src.governance.audit import AuditLogger
    ex = exemption_manager.get_exemption(exemption_id)
    if not ex:
        raise APIError("Exemption not found", status_code=404, error_code="NOT_FOUND")

    exemption_manager.revoke_exemption(exemption_id)
    AuditLogger.log(
        event_type="EXEMPTION_REVOKED",
        action="REVOKE",
        account_id=ex.get("account_id") or "N/A",
        region="global",
        resource_id=ex.get("resource_id") or ex.get("resource_type") or ex.get("environment") or "N/A",
        resource_type="EXEMPTION",
        result="SUCCESS",
        actor=_actor(),
        details={"exemption_id": exemption_id}
    )
    _record_exemption_audit("EXEMPTION_REVOKE", ex)
    _exemptions_cache.invalidate()
    metrics.record_exemption("revoke")
    return jsonify({"message": "Exemption revoked successfully", "id": exemption_id})


def _record_exemption_audit(action: str, ex: Dict[str, Any]) -> None:
    """Exemption changes also go to the SQLite audit log shown in the UI."""
    try:
        from src.db import insert_audit_log
        scope = ex.get("resource_id") or ex.get("resource_type") or ex.get("account_id") or ex.get("environment")
        insert_audit_log(_actor(), action, scope, "SUCCESS",
                         details={"exemption_id": ex.get("id"), "reason": ex.get("reason"),
                                  "expires_at": ex.get("expires_at")},
                         request_id=getattr(g, "request_id", None))
    except Exception as e:
        logger.error("Failed to persist exemption audit log: %s", e)


# ─────────────────────────────────────────────────────────────────────
# Audit & CI/CD
# ─────────────────────────────────────────────────────────────────────

@app.get("/api/audit")
@require_auth
def api_audit():
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 1000))
    except ValueError:
        limit = 50

    from src.db import get_recent_audit_logs
    records = get_recent_audit_logs(limit=limit)
    return jsonify({"audit_events": records, "_meta": {"count": len(records)}})


@app.get("/api/cicd")
@require_auth
def api_cicd():
    raise APIError("CI/CD metrics are not available in local run mode.", status_code=501, error_code="NOT_IMPLEMENTED")


# ─────────────────────────────────────────────────────────────────────
# Startup: metrics warm-up and optional scheduled scans
# ─────────────────────────────────────────────────────────────────────

_gauges_loaded = False
_gauges_lock = threading.Lock()


def _refresh_report_gauges_once() -> None:
    """After a restart, populate compliance gauges from the cached scan (first scrape only)."""
    global _gauges_loaded
    if _gauges_loaded:
        return
    with _gauges_lock:
        if _gauges_loaded:
            return
        try:
            report = get_cached_report()
            if report:
                metrics.record_compliance_report(report)
        except Exception as e:
            logger.warning("Could not load cached report into metrics: %s", e)
        _gauges_loaded = True


def _scheduler_loop(interval: int) -> None:
    logger.info("Scheduled compliance scans enabled every %ss", interval)
    time.sleep(min(30, interval))  # let the app finish starting
    while True:
        try:
            if is_cache_stale() and not is_refreshing():
                background_refresh()
        except Exception as e:
            logger.error("Scheduled compliance scan failed: %s", e)
        time.sleep(interval)


_scheduler_started = False


def start_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started or COMPLIANCE_SCAN_INTERVAL_SECONDS <= 0:
        return
    _scheduler_started = True
    threading.Thread(target=_scheduler_loop, args=(COMPLIANCE_SCAN_INTERVAL_SECONDS,),
                     daemon=True, name="compliance-scheduler").start()


start_scheduler()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
