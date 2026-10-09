"""
Resource inventory for compliance scans.

Sources
-------
tagging            Resource Groups Tagging API GetResources. Fast and cheap, but it only
                   returns resources that are tagged or were tagged at some point.
resource_explorer  AWS Resource Explorer ListResources (the full inventory, including
                   resources that never had a tag), merged with GetResources tags by ARN.
                   Works whether or not the RE view includes the "tags" property.

If Resource Explorer fails for a region (no index, no permission), that region falls back
to the tagging source and the scan records a warning instead of failing.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.arn_utils import resource_type_from_arn, type_matches
from src.aws_session import session_for
from src.clients import get_client
from src.config import INVENTORY_SOURCE, RESOURCE_EXPLORER_REGION, RESOURCE_EXPLORER_VIEW_ARN
from src.logging_config import get_logger

logger = get_logger(__name__)


def _tags(raw: List[Dict[str, str]]) -> Dict[str, str]:
    return {t["Key"]: t.get("Value", "") for t in raw or [] if "Key" in t}


def tagging_api_resources(region: str, session=None, type_filters: Optional[List[str]] = None) -> Dict[str, Dict[str, str]]:
    """ARN -> tags for every resource the tagging API knows in `region`."""
    client = get_client("resourcegroupstaggingapi", region, session)
    kwargs: Dict[str, Any] = {}
    if type_filters:
        kwargs["ResourceTypeFilters"] = list(type_filters)
    out: Dict[str, Dict[str, str]] = {}
    for page in client.get_paginator("get_resources").paginate(**kwargs):
        for item in page.get("ResourceTagMappingList", []):
            arn = item.get("ResourceARN")
            if arn:
                out[arn] = _tags(item.get("Tags", []))
    return out


def resource_explorer_resources(region: str, session=None) -> Dict[str, Dict[str, Any]]:
    """ARN -> {type, tags|None} from Resource Explorer for resources located in `region`."""
    client = get_client("resource-explorer-2", RESOURCE_EXPLORER_REGION or region, session)
    kwargs: Dict[str, Any] = {"Filters": {"FilterString": f"region:{region}"}}
    if RESOURCE_EXPLORER_VIEW_ARN:
        kwargs["ViewArn"] = RESOURCE_EXPLORER_VIEW_ARN
    out: Dict[str, Dict[str, Any]] = {}
    for page in client.get_paginator("list_resources").paginate(**kwargs):
        for r in page.get("Resources", []):
            arn = r.get("Arn")
            if not arn:
                continue
            tags = None
            for prop in r.get("Properties", []) or []:
                if prop.get("Name") == "tags" and isinstance(prop.get("Data"), list):
                    tags = _tags(prop["Data"])
            out[arn] = {"type": r.get("ResourceType") or resource_type_from_arn(arn), "tags": tags}
    return out


def collect(region: str, account_id: Optional[str] = None,
            type_filters: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Inventory of one account/region.

    Returns {"resources": [{arn, tags, resource_type, never_tagged}], "source": str, "warning": str|None}.
    `type_filters` (tagging API style, e.g. ["ec2:instance", "es"]) limits the result when given.
    """
    session = session_for(account_id)
    tagged = tagging_api_resources(region, session, type_filters)
    source, warning = "tagging", None
    explorer: Dict[str, Dict[str, Any]] = {}

    if INVENTORY_SOURCE == "resource_explorer":
        try:
            explorer = resource_explorer_resources(region, session)
            source = "resource_explorer"
        except Exception as e:
            warning = f"Resource Explorer unavailable in {region}, used the tagging API instead: {e}"
            logger.warning(warning)

    resources = []
    seen = set()
    for arn, info in explorer.items():
        rtype = info["type"]
        if type_filters and not type_matches(rtype, type_filters):
            continue
        in_tagging = arn in tagged
        tags = tagged.get(arn) if in_tagging else (info["tags"] or {})
        resources.append({
            "arn": arn, "tags": tags, "resource_type": rtype,
            # Not known to the tagging API and no tags in RE: this resource was never tagged
            "never_tagged": not in_tagging and not info["tags"],
        })
        seen.add(arn)
    for arn, tags in tagged.items():  # tagging-only (e.g. Resource Explorer index lag)
        if arn in seen:
            continue
        rtype = resource_type_from_arn(arn)
        if type_filters and not type_matches(rtype, type_filters):
            continue
        resources.append({"arn": arn, "tags": tags, "resource_type": rtype, "never_tagged": False})

    return {"resources": resources, "source": source, "warning": warning}
