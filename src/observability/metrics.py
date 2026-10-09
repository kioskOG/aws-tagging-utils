"""
Metrics for AWS Tagging Utilities.

Two sinks:
  * Prometheus — process-local registry exposed by the web app at GET /metrics.
  * CloudWatch EMF — structured stdout lines for Lambda / ECS (opt-in via METRICS_EMF_ENABLED).

All helpers are safe to call from any module (Lambda handlers, worker, web) and never raise.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, Optional

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

from src.config import METRICS_EMF_ENABLED, METRICS_NAMESPACE

REGISTRY = CollectorRegistry(auto_describe=True)
_P = "tagging_utils"

# ── HTTP ─────────────────────────────────────────────────────────────
HTTP_REQUESTS = Counter(
    f"{_P}_http_requests_total", "HTTP requests handled",
    ["method", "endpoint", "status"], registry=REGISTRY)
HTTP_LATENCY = Histogram(
    f"{_P}_http_request_duration_seconds", "HTTP request latency",
    ["method", "endpoint"], registry=REGISTRY,
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))

# ── AWS API ──────────────────────────────────────────────────────────
AWS_CALLS = Counter(
    f"{_P}_aws_api_calls_total", "AWS API calls made",
    ["service", "operation", "outcome"], registry=REGISTRY)
AWS_LATENCY = Histogram(
    f"{_P}_aws_api_call_duration_seconds", "AWS API call latency (incl. retries)",
    ["service", "operation"], registry=REGISTRY,
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))

# ── Compliance (gauges reflect the latest completed scan) ────────────
COMPLIANCE_SCORE = Gauge(
    f"{_P}_compliance_score_percent", "Compliance score of the latest scan", registry=REGISTRY)
COMPLIANCE_RESOURCES = Gauge(
    f"{_P}_compliance_resources", "Resources in the latest scan",
    ["region", "resource_type", "status"], registry=REGISTRY)
COMPLIANCE_VIOLATIONS = Gauge(
    f"{_P}_compliance_violations", "Violations in the latest scan",
    ["tag", "type"], registry=REGISTRY)
COMPLIANCE_LAST_SCAN = Gauge(
    f"{_P}_compliance_last_scan_timestamp_seconds", "Unix time of the latest completed scan",
    registry=REGISTRY)
SCAN_DURATION = Histogram(
    f"{_P}_compliance_scan_duration_seconds", "Compliance scan duration", registry=REGISTRY,
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1200))
SCANS = Counter(
    f"{_P}_compliance_scans_total", "Compliance scans run", ["outcome"], registry=REGISTRY)
REGION_SCAN_ERRORS = Counter(
    f"{_P}_compliance_region_errors_total", "Regions that failed during a scan",
    ["region"], registry=REGISTRY)

# ── Mutations ────────────────────────────────────────────────────────
TAG_WRITES = Counter(
    f"{_P}_tag_writes_total", "Resources processed by tag writes", ["outcome"], registry=REGISTRY)
REMEDIATIONS = Counter(
    f"{_P}_remediation_actions_total", "Remediation actions by final status",
    ["status"], registry=REGISTRY)
AUTO_TAGGED = Counter(
    f"{_P}_auto_tagged_resources_total", "Resources auto-tagged with an owner", registry=REGISTRY)
EXEMPTION_CHANGES = Counter(
    f"{_P}_exemption_changes_total", "Exemptions created or revoked", ["action"], registry=REGISTRY)

# ── FinOps ───────────────────────────────────────────────────────────
FINOPS_SPEND = Gauge(
    f"{_P}_finops_spend_usd", "Spend from the latest FinOps snapshot", ["kind"], registry=REGISTRY)

# ── Worker ───────────────────────────────────────────────────────────
WORKER_MESSAGES = Counter(
    f"{_P}_worker_messages_total", "SQS messages handled by the worker",
    ["job_type", "outcome"], registry=REGISTRY)

BUILD_INFO = Info(f"{_P}_build", "Build information", registry=REGISTRY)


def render_prometheus() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def _safe(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # metrics must never break business logic
            return None
    return wrapper


# ── CloudWatch EMF ───────────────────────────────────────────────────
class CloudWatchMetrics:
    """
    AWS CloudWatch Embedded Metric Format (EMF): metrics are emitted as structured
    stdout lines that CloudWatch Logs converts to metrics without extra API calls.
    """
    @staticmethod
    def put_metric(namespace: str, metric_name: str, value: float,
                   dimensions: Optional[Dict[str, str]] = None, unit: str = "Count") -> None:
        dimensions = dimensions or {}
        emf_payload: Dict[str, Any] = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [{
                    "Namespace": namespace,
                    "Dimensions": [list(dimensions.keys())],
                    "Metrics": [{"Name": metric_name, "Unit": unit}],
                }],
            },
            metric_name: value,
        }
        emf_payload.update(dimensions)
        print(json.dumps(emf_payload), flush=True)


def _emf(metric_name: str, value: float, dimensions: Optional[Dict[str, str]] = None, unit: str = "Count") -> None:
    if METRICS_EMF_ENABLED:
        CloudWatchMetrics.put_metric(METRICS_NAMESPACE, metric_name, value, dimensions, unit)


# ── Recording helpers ────────────────────────────────────────────────
@_safe
def record_http(method: str, endpoint: str, status: int, duration_s: float) -> None:
    HTTP_REQUESTS.labels(method, endpoint, str(status)).inc()
    HTTP_LATENCY.labels(method, endpoint).observe(duration_s)


@_safe
def record_aws_call(service: str, operation: str, outcome: str, duration_s: Optional[float]) -> None:
    AWS_CALLS.labels(service, operation, outcome).inc()
    if duration_s is not None:
        AWS_LATENCY.labels(service, operation).observe(duration_s)


@_safe
def record_compliance_report(report: Dict[str, Any], duration_s: Optional[float] = None) -> None:
    """Refresh compliance gauges from a full report (generated or loaded from cache)."""
    summary = report.get("summary", {}) or {}
    COMPLIANCE_SCORE.set(float(summary.get("compliance_score", 0.0) or 0.0))
    COMPLIANCE_RESOURCES.clear()
    COMPLIANCE_VIOLATIONS.clear()

    res_counts: Dict[tuple, int] = {}
    viol_counts: Dict[tuple, int] = {}
    for region, region_data in (report.get("regions") or {}).items():
        for res in region_data.get("resources", []) or []:
            arn = res.get("ResourceARN", "")
            parts = arn.split(":")
            rtype = parts[2] if len(parts) > 2 else "unknown"
            status = "compliant" if res.get("IsCompliant") else "non_compliant"
            res_counts[(region, rtype, status)] = res_counts.get((region, rtype, status), 0) + 1
            for v in res.get("Violations", []) or []:
                key = (str(v.get("tag")), str(v.get("type")))
                viol_counts[key] = viol_counts.get(key, 0) + 1

    for labels, n in res_counts.items():
        COMPLIANCE_RESOURCES.labels(*labels).set(n)
    for labels, n in viol_counts.items():
        COMPLIANCE_VIOLATIONS.labels(*labels).set(n)

    ts = report.get("_meta", {}).get("generated_at") if isinstance(report.get("_meta"), dict) else None
    COMPLIANCE_LAST_SCAN.set(float(ts) if ts else time.time())
    if duration_s is not None:
        SCAN_DURATION.observe(duration_s)

    _emf("ComplianceScore", float(summary.get("compliance_score", 0.0) or 0.0), unit="Percent")
    _emf("ResourcesScanned", float(summary.get("total_resources", 0) or 0))
    _emf("NonCompliantResources", float(summary.get("non_compliant", 0) or 0))


@_safe
def record_scan_outcome(outcome: str, failed_regions: Iterable[str] = ()) -> None:
    SCANS.labels(outcome).inc()
    for r in failed_regions:
        REGION_SCAN_ERRORS.labels(r).inc()


@_safe
def record_tag_writes(succeeded: int, failed: int) -> None:
    if succeeded:
        TAG_WRITES.labels("success").inc(succeeded)
    if failed:
        TAG_WRITES.labels("failed").inc(failed)
    _emf("TagWrites", float(succeeded))


@_safe
def record_remediation(status: str) -> None:
    REMEDIATIONS.labels(status).inc()
    _emf("RemediationsExecuted", 1, {"Status": status})


@_safe
def record_auto_tagged(n: int) -> None:
    if n:
        AUTO_TAGGED.inc(n)
        _emf("AutoTaggedResources", float(n))


@_safe
def record_exemption(action: str) -> None:
    EXEMPTION_CHANGES.labels(action).inc()


@_safe
def record_finops(report: Dict[str, Any]) -> None:
    for kind, key in (("total", "TotalSpend"), ("tagged", "TaggedSpend"),
                      ("untagged", "UntaggedSpend"), ("unallocated", "PotentiallyUnallocated")):
        val = report.get(key)
        if val is not None:
            FINOPS_SPEND.labels(kind).set(float(val))


@_safe
def record_worker_message(job_type: str, outcome: str) -> None:
    WORKER_MESSAGES.labels(job_type, outcome).inc()


@_safe
def set_build_info(**info: str) -> None:
    BUILD_INFO.info({k: str(v) for k, v in info.items()})
