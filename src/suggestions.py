"""
Tag value suggestions from neighbouring resources in the latest scan (no AWS calls).

For a resource missing `key`, look at related resources that do have `key` and vote:

    signal                          weight   example
    same CloudFormation stack         5      aws:cloudformation:stack-name = orders-api
    same EKS cluster                  4      aws:eks:cluster-name / eks:cluster-name / kubernetes.io/cluster/<x>
    same Name prefix in account       3      "orders-api-db", "orders-api-queue" → "orders"
    same account and service          1      every ec2 resource in 1111…

Only values valid for the schema (allowed_values / pattern) are suggested. Optionally the
CloudTrail creator is offered for the Owner tag (live lookup, a few ARNs at a time).
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

from src.arn_utils import account_of
from src.logging_config import get_logger

logger = get_logger(__name__)

_WEIGHTS = {"stack": 5.0, "eks": 4.0, "name_prefix": 3.0, "account_service": 1.0}
_REASONS = {
    "stack": "same CloudFormation stack ({group})",
    "eks": "same EKS cluster ({group})",
    "name_prefix": "same name prefix '{group}'",
    "account_service": "same account & service ({group})",
}


def _name_prefix(name: str) -> Optional[str]:
    tokens = [t for t in re.split(r"[-_./ ]+", name or "") if t]
    return tokens[0].lower() if len(tokens) > 1 and len(tokens[0]) >= 3 else None


def _signals(arn: str, tags: Dict[str, str]) -> Dict[str, str]:
    lowered = {k.lower(): v for k, v in tags.items()}
    out = {}
    if lowered.get("aws:cloudformation:stack-name"):
        out["stack"] = lowered["aws:cloudformation:stack-name"]
    eks = lowered.get("aws:eks:cluster-name") or lowered.get("eks:cluster-name")
    if not eks:
        eks = next((k.split("/", 2)[2] for k in tags if k.startswith("kubernetes.io/cluster/")), None)
    if eks:
        out["eks"] = eks
    prefix = _name_prefix(tags.get("Name", ""))
    acct = account_of(arn)
    if prefix:
        out["name_prefix"] = f"{acct}:{prefix}"
    service = arn.split(":")[2] if arn.count(":") >= 2 else "unknown"
    out["account_service"] = f"{acct}:{service}"
    return out


def _valid(key: str, value: str, schema: Dict[str, Any]) -> bool:
    rule = schema.get(key)
    if not rule:
        return True
    if rule.allowed_values and value not in rule.allowed_values:
        return False
    if rule.pattern:
        try:
            return re.match(rule.pattern, value) is not None
        except re.error:
            return True
    return True


class SuggestionIndex:
    def __init__(self, resources: Iterable[Dict[str, Any]], schema: Dict[str, Any]):
        self.schema = schema
        self.by_arn: Dict[str, Dict[str, str]] = {}
        # signal -> group -> key -> value -> count
        self.votes: Dict[str, Dict[str, Dict[str, Dict[str, int]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))))
        self.group_sizes: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for res in resources:
            arn, tags = res["ResourceARN"], res.get("Tags") or {}
            self.by_arn[arn] = tags
            for signal, group in _signals(arn, tags).items():
                self.group_sizes[signal][group] += 1
                for k, v in tags.items():
                    if v and not k.lower().startswith("aws:"):
                        self.votes[signal][group][k][v] += 1

    def suggest(self, arn: str, key: str, limit: int = 3) -> List[Dict[str, Any]]:
        tags = self.by_arn.get(arn, {})
        own_value = tags.get(key)
        scores: Dict[str, float] = defaultdict(float)
        support: Dict[str, int] = defaultdict(int)
        best_reason: Dict[str, tuple] = {}
        available = 0.0  # total weight of the signals that had any evidence for this key
        for signal, group in _signals(arn, tags).items():
            counts = dict(self.votes[signal][group].get(key, {}))
            if own_value and own_value in counts:  # don't count the resource itself
                counts[own_value] -= 1
            members = self.group_sizes[signal][group] - 1
            voters = sum(c for c in counts.values() if c > 0)
            if members <= 0 or voters <= 0:
                continue
            available += _WEIGHTS[signal]
            for value, n in counts.items():
                if n <= 0 or not _valid(key, value, self.schema):
                    continue
                agreement = n / voters
                coverage = voters / members
                scores[value] += _WEIGHTS[signal] * agreement * (0.5 + 0.5 * coverage)
                support[value] = max(support[value], n)
                strength = _WEIGHTS[signal] * agreement
                if value not in best_reason or strength > best_reason[value][0]:
                    label = group.split(":", 1)[-1] if signal not in ("stack", "eks") else group
                    best_reason[value] = (strength, f"{n} of {members} resources with "
                                          + _REASONS[signal].format(group=label) + f" have {key}={value}")
        if not scores:
            return []
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        # share of the available evidence, discounted for tiny samples (1 vote → ×0.5, 3 votes → ×0.875)
        return [{"value": v, "confidence": round(min(0.99, (sc / available) * (1 - 0.5 ** support[v])), 2),
                 "reason": best_reason[v][1]} for v, sc in ranked]


def build_index() -> Optional[SuggestionIndex]:
    from src.cache_manager import get_cached_report
    from src.tag_report import schema_provider
    report = get_cached_report()
    if not report:
        return None
    resources = [r for d in report.get("regions", {}).values() if not d.get("error") for r in d.get("resources", [])]
    return SuggestionIndex(resources, schema_provider.get_schema())


def suggest(arns: List[str], keys: List[str], include_creator: bool = False) -> Dict[str, Any]:
    """
    Per-resource suggestions and an aggregate per key (for bulk edits).
    {"resources": {arn: {key: [..]}}, "aggregate": {key: [{value, resources, avg_confidence}]}}
    """
    index = build_index()
    per_resource: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    agg: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for arn in arns:
        per_resource[arn] = {}
        for key in keys:
            sugg = index.suggest(arn, key) if index else []
            if include_creator and key.lower() == "owner" and len(arns) <= 5:
                creator = _creator(arn)
                if creator and all(s["value"] != creator for s in sugg):
                    sugg.insert(0, {"value": creator, "confidence": 0.9,
                                    "reason": "created this resource (CloudTrail)"})
            per_resource[arn][key] = sugg
            for s_ in sugg:
                agg[key][s_["value"]].append(s_["confidence"])
    aggregate = {
        key: sorted(({"value": v, "resources": len(c), "avg_confidence": round(sum(c) / len(c), 2)}
                     for v, c in values.items()), key=lambda r: (-r["resources"], -r["avg_confidence"]))[:5]
        for key, values in agg.items()
    }
    return {"resources": per_resource, "aggregate": aggregate, "index_available": index is not None}


def _creator(arn: str) -> Optional[str]:
    try:
        from src.tag_on_create import lookup_owner_for_resource
        from src.arn_utils import region_of
        from src.config import DEFAULT_REGION
        return lookup_owner_for_resource(region_of(arn, DEFAULT_REGION), arn)
    except Exception as e:
        logger.info("CloudTrail creator lookup failed for %s: %s", arn, e)
        return None
