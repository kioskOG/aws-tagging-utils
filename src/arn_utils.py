"""
ARN helpers shared by inventory, coverage and propagation.

resource_type_from_arn() returns the same "service:type" strings used by the
Resource Groups Tagging API filters and AWS Resource Explorer (e.g. "ec2:instance").
"""
from __future__ import annotations

from typing import Iterable, NamedTuple, Optional

# Services whose ARN resource part is only a name (no "type/" prefix)
_NAME_ONLY_TYPES = {"s3": "bucket", "sns": "topic", "sqs": "queue"}


class Arn(NamedTuple):
    partition: str
    service: str
    region: str
    account: str
    resource: str


def parse_arn(arn: str) -> Optional[Arn]:
    if not arn or not isinstance(arn, str) or not arn.startswith("arn:"):
        return None
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return None
    return Arn(parts[1], parts[2], parts[3], parts[4], parts[5])


def resource_type_from_arn(arn: str) -> str:
    """'arn:aws:ec2:r:a:instance/i-1' -> 'ec2:instance'; 'arn:aws:s3:::b' -> 's3:bucket'."""
    p = parse_arn(arn)
    if not p:
        return "unknown"
    if p.service in _NAME_ONLY_TYPES:
        return f"{p.service}:{_NAME_ONLY_TYPES[p.service]}"
    resource = p.resource.lstrip("/")
    head = resource.split("/", 1)[0].split(":", 1)[0]
    if not head or head == resource and ":" not in p.resource and "/" not in p.resource:
        # name-only resource part we don't know the type of
        return p.service
    return f"{p.service}:{head}"


def type_matches(resource_type: str, filters: Iterable[str]) -> bool:
    """
    True when `resource_type` is selected by any filter.
    'ec2' matches every ec2 type; 'elasticloadbalancing:loadbalancer' also matches
    'elasticloadbalancing:loadbalancer/app' (Resource Explorer sub-types).
    """
    service = resource_type.split(":", 1)[0]
    for f in filters:
        if not f:
            continue
        if ":" not in f:
            if service == f:
                return True
        elif resource_type == f or resource_type.startswith(f + "/"):
            return True
    return False


def account_of(arn: str) -> str:
    p = parse_arn(arn)
    return p.account if p and p.account else "unknown"


def region_of(arn: str, default: str = "") -> str:
    p = parse_arn(arn)
    return p.region if p and p.region else default
