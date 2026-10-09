"""
Tag propagation: check and fix parent → child tag inheritance.

Rules (parent → children)
-------------------------
vpc             VPC → subnets, security groups, route tables, internet/NAT gateways, network ACLs, VPC endpoints
ec2_instance    EC2 instance → attached EBS volumes and network interfaces
ebs_volume      EBS volume → its snapshots
asg             Auto Scaling group → running instances  (+ config check: PropagateAtLaunch)
ecs_service     ECS service → running tasks              (+ config check: propagateTags / managed tags)
cloudformation  CloudFormation stack → resources it created (aws:cloudformation:stack-name)
rds_cluster     Aurora/RDS cluster → member DB instances
eks_cluster     EKS cluster → managed node groups
elbv2           ALB/NLB → target groups
lambda          Lambda function → its CloudWatch log group (/aws/lambda/<name>)

Which keys propagate: PROPAGATE_KEYS ("schema" = tags defined in tag-schema.yaml, "all", or a
comma list), never PROPAGATE_EXCLUDE_KEYS (default "Name") or aws:* tags.

A check produces findings; fixes go through src.changesets, so every fix is previewable and
undoable. Configuration fixes (ASG PropagateAtLaunch, ECS propagateTags) are change-set "ops"
that undo restores too.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from src.aws_session import session_for
from src.changesets import register_op
from src.clients import get_client
from src.config import PROPAGATE_EXCLUDE_KEYS, PROPAGATE_KEYS
from src.logging_config import get_logger

logger = get_logger(__name__)


def _tags(raw: Optional[Iterable[Dict[str, Any]]], key: str = "Key", value: str = "Value") -> Dict[str, str]:
    return {t[key]: t.get(value, "") for t in raw or [] if key in t}


def _lower_tags(raw) -> Dict[str, str]:
    """ECS returns tags as [{key, value}]."""
    return _tags(raw, "key", "value")


def _chunks(seq: List[Any], n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _paginate(client, op: str, key: str, **kwargs) -> List[Any]:
    out = []
    for page in client.get_paginator(op).paginate(**kwargs):
        out.extend(page.get(key, []))
    return out


# ── Which keys propagate ────────────────────────────────────────────

def propagated_keys(parent_tags: Dict[str, str]) -> Dict[str, str]:
    excluded = {k.lower() for k in PROPAGATE_EXCLUDE_KEYS}
    mode = PROPAGATE_KEYS.lower()
    if mode == "all":
        allowed = None
    elif mode == "schema":
        from src.tag_report import schema_provider
        allowed = {k.lower() for k in schema_provider.get_schema()}
    else:
        allowed = {k.strip().lower() for k in PROPAGATE_KEYS.split(",") if k.strip()}
    return {k: v for k, v in parent_tags.items()
            if v and not k.lower().startswith("aws:") and k.lower() not in excluded
            and (allowed is None or k.lower() in allowed)}


# ── Rule discovery (one function per parent type) ───────────────────
# Each returns relations: {"parent_arn", "parent_name", "parent_tags",
#                          "children": [{"arn", "type", "tags"}], "config": [issue...]}

def _rel(parent_arn: str, name: str, tags: Dict[str, str], children: List[Dict[str, Any]],
         config: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"parent_arn": parent_arn, "parent_name": name, "parent_tags": tags,
            "children": children, "config": config or []}


def _vpc(region: str, session) -> List[Dict[str, Any]]:
    ec2 = get_client("ec2", region, session)
    vpcs = _paginate(ec2, "describe_vpcs", "Vpcs")
    owners = {v["VpcId"]: v.get("OwnerId", "") for v in vpcs}
    children: Dict[str, List[Dict[str, Any]]] = {v["VpcId"]: [] for v in vpcs}

    def add(vpc_id, kind, rid, owner, tags):
        if vpc_id in children:
            owner = owner or owners.get(vpc_id, "")
            children[vpc_id].append({"arn": f"arn:aws:ec2:{region}:{owner}:{kind}/{rid}", "type": f"ec2:{kind}",
                                     "tags": _tags(tags)})

    for s in _paginate(ec2, "describe_subnets", "Subnets"):
        add(s["VpcId"], "subnet", s["SubnetId"], s.get("OwnerId"), s.get("Tags"))
    for g in _paginate(ec2, "describe_security_groups", "SecurityGroups"):
        add(g.get("VpcId"), "security-group", g["GroupId"], g.get("OwnerId"), g.get("Tags"))
    for r in _paginate(ec2, "describe_route_tables", "RouteTables"):
        add(r.get("VpcId"), "route-table", r["RouteTableId"], r.get("OwnerId"), r.get("Tags"))
    for n in _paginate(ec2, "describe_network_acls", "NetworkAcls"):
        add(n.get("VpcId"), "network-acl", n["NetworkAclId"], n.get("OwnerId"), n.get("Tags"))
    for i in _paginate(ec2, "describe_internet_gateways", "InternetGateways"):
        for att in i.get("Attachments", []):
            add(att.get("VpcId"), "internet-gateway", i["InternetGatewayId"], i.get("OwnerId"), i.get("Tags"))
    for n in _paginate(ec2, "describe_nat_gateways", "NatGateways"):
        if n.get("State") not in ("deleted", "deleting"):
            add(n.get("VpcId"), "natgateway", n["NatGatewayId"], None, n.get("Tags"))
    for e in _paginate(ec2, "describe_vpc_endpoints", "VpcEndpoints"):
        add(e.get("VpcId"), "vpc-endpoint", e["VpcEndpointId"], e.get("OwnerId"), e.get("Tags"))
    return [_rel(f"arn:aws:ec2:{region}:{v.get('OwnerId', '')}:vpc/{v['VpcId']}",
                 _tags(v.get("Tags")).get("Name", v["VpcId"]), _tags(v.get("Tags")), children[v["VpcId"]])
            for v in vpcs]


def _instances(ec2) -> List[Dict[str, Any]]:
    out = []
    for res in _paginate(ec2, "describe_instances", "Reservations",
                         Filters=[{"Name": "instance-state-name",
                                   "Values": ["pending", "running", "stopping", "stopped"]}]):
        for inst in res.get("Instances", []):
            inst["_owner"] = res.get("OwnerId", "")
            out.append(inst)
    return out


def _ec2_instance(region: str, session) -> List[Dict[str, Any]]:
    ec2 = get_client("ec2", region, session)
    volumes = {v["VolumeId"]: _tags(v.get("Tags")) for v in _paginate(ec2, "describe_volumes", "Volumes")}
    enis = {n["NetworkInterfaceId"]: (_tags(n.get("TagSet")), n.get("OwnerId", ""))
            for n in _paginate(ec2, "describe_network_interfaces", "NetworkInterfaces")}
    rels = []
    for inst in _instances(ec2):
        owner, iid = inst["_owner"], inst["InstanceId"]
        tags = _tags(inst.get("Tags"))
        kids = []
        for bdm in inst.get("BlockDeviceMappings", []):
            vid = (bdm.get("Ebs") or {}).get("VolumeId")
            if vid:
                kids.append({"arn": f"arn:aws:ec2:{region}:{owner}:volume/{vid}", "type": "ec2:volume",
                             "tags": volumes.get(vid, {})})
        for ni in inst.get("NetworkInterfaces", []):
            nid = ni.get("NetworkInterfaceId")
            if nid:
                kids.append({"arn": f"arn:aws:ec2:{region}:{enis.get(nid, ({}, owner))[1] or owner}:network-interface/{nid}",
                             "type": "ec2:network-interface", "tags": enis.get(nid, ({}, owner))[0]})
        rels.append(_rel(f"arn:aws:ec2:{region}:{owner}:instance/{iid}", tags.get("Name", iid), tags, kids))
    return rels


def _ebs_volume(region: str, session) -> List[Dict[str, Any]]:
    ec2 = get_client("ec2", region, session)
    vols = {v["VolumeId"]: v for v in _paginate(ec2, "describe_volumes", "Volumes")}
    snaps: Dict[str, List[Dict[str, Any]]] = {}
    owners: Dict[str, str] = {}
    for s in _paginate(ec2, "describe_snapshots", "Snapshots", OwnerIds=["self"]):
        if s.get("VolumeId") in vols:
            owners[s["VolumeId"]] = s.get("OwnerId", "")
            # Snapshot ARNs have no account ID: arn:aws:ec2:<region>::snapshot/<id>
            snaps.setdefault(s["VolumeId"], []).append(
                {"arn": f"arn:aws:ec2:{region}::snapshot/{s['SnapshotId']}", "type": "ec2:snapshot",
                 "tags": _tags(s.get("Tags"))})
    rels = []
    for vid, kids in snaps.items():
        tags = _tags(vols[vid].get("Tags"))
        rels.append(_rel(f"arn:aws:ec2:{region}:{owners.get(vid, '')}:volume/{vid}", tags.get("Name", vid), tags, kids))
    return rels


def _asg(region: str, session) -> List[Dict[str, Any]]:
    asc = get_client("autoscaling", region, session)
    ec2 = get_client("ec2", region, session)
    inst_tags = {i["InstanceId"]: (_tags(i.get("Tags")), i["_owner"]) for i in _instances(ec2)}
    rels = []
    for g in _paginate(asc, "describe_auto_scaling_groups", "AutoScalingGroups"):
        tags = {t["Key"]: t.get("Value", "") for t in g.get("Tags", [])}
        flags = {t["Key"]: bool(t.get("PropagateAtLaunch")) for t in g.get("Tags", [])}
        expected = propagated_keys(tags)
        not_propagating = sorted(k for k in expected if not flags.get(k))
        config = []
        if not_propagating:
            config.append({
                "issue": "PropagateAtLaunch is off",
                "detail": f"New instances won't get: {', '.join(not_propagating)}",
                "op": {"op": "asg_propagate", "target": g["AutoScalingGroupARN"], "region": region,
                       "asg_name": g["AutoScalingGroupName"],
                       "tags": {k: tags[k] for k in not_propagating},
                       "before_flags": {k: flags.get(k, False) for k in not_propagating}},
            })
        kids = []
        for i in g.get("Instances", []):
            itags, owner = inst_tags.get(i["InstanceId"], ({}, ""))
            kids.append({"arn": f"arn:aws:ec2:{region}:{owner}:instance/{i['InstanceId']}", "type": "ec2:instance",
                         "tags": itags})
        rels.append(_rel(g["AutoScalingGroupARN"], g["AutoScalingGroupName"], tags, kids, config))
    return rels


def _ecs_service(region: str, session, max_tasks: int = 100) -> List[Dict[str, Any]]:
    ecs = get_client("ecs", region, session)
    rels = []
    for cluster in _paginate(ecs, "list_clusters", "clusterArns"):
        service_arns = _paginate(ecs, "list_services", "serviceArns", cluster=cluster)
        for chunk in _chunks(service_arns, 10):
            for svc in ecs.describe_services(cluster=cluster, services=chunk, include=["TAGS"]).get("services", []):
                tags = _lower_tags(svc.get("tags"))
                config = []
                propagate = svc.get("propagateTags") or "NONE"
                if propagate == "NONE" and propagated_keys(tags):
                    config.append({
                        "issue": "propagateTags is NONE",
                        "detail": "Tasks started by this service don't inherit its tags",
                        "op": {"op": "ecs_propagate", "target": svc["serviceArn"], "region": region,
                               "cluster": cluster, "service": svc["serviceName"],
                               "before": {"propagateTags": propagate,
                                          "enableECSManagedTags": bool(svc.get("enableECSManagedTags"))}},
                    })
                task_arns = _paginate(ecs, "list_tasks", "taskArns", cluster=cluster,
                                      serviceName=svc["serviceName"])[:max_tasks]
                kids = []
                for tchunk in _chunks(task_arns, 100):
                    for t in ecs.describe_tasks(cluster=cluster, tasks=tchunk, include=["TAGS"]).get("tasks", []):
                        kids.append({"arn": t["taskArn"], "type": "ecs:task", "tags": _lower_tags(t.get("tags"))})
                rels.append(_rel(svc["serviceArn"], svc["serviceName"], tags, kids, config))
    return rels


def _cloudformation(region: str, session) -> List[Dict[str, Any]]:
    cfn = get_client("cloudformation", region, session)
    tagging = get_client("resourcegroupstaggingapi", region, session)
    by_stack: Dict[str, List[Dict[str, Any]]] = {}
    for item in _paginate(tagging, "get_resources", "ResourceTagMappingList",
                          TagFilters=[{"Key": "aws:cloudformation:stack-name"}]):
        tags = _tags(item.get("Tags"))
        stack = tags.get("aws:cloudformation:stack-name")
        if stack:
            from src.arn_utils import resource_type_from_arn
            by_stack.setdefault(stack, []).append({"arn": item["ResourceARN"],
                                                   "type": resource_type_from_arn(item["ResourceARN"]),
                                                   "tags": tags})
    rels = []
    for st in _paginate(cfn, "describe_stacks", "Stacks"):
        if st.get("StackStatus", "").startswith("DELETE"):
            continue
        rels.append(_rel(st["StackId"], st["StackName"], _tags(st.get("Tags")), by_stack.get(st["StackName"], [])))
    return rels


def _rds_cluster(region: str, session) -> List[Dict[str, Any]]:
    rds = get_client("rds", region, session)
    instances = {i["DBInstanceIdentifier"]: i for i in _paginate(rds, "describe_db_instances", "DBInstances")}
    rels = []
    for c in _paginate(rds, "describe_db_clusters", "DBClusters"):
        kids = []
        for m in c.get("DBClusterMembers", []):
            inst = instances.get(m["DBInstanceIdentifier"])
            if inst:
                kids.append({"arn": inst["DBInstanceArn"], "type": "rds:db", "tags": _tags(inst.get("TagList"))})
        rels.append(_rel(c["DBClusterArn"], c["DBClusterIdentifier"], _tags(c.get("TagList")), kids))
    return rels


def _eks_cluster(region: str, session) -> List[Dict[str, Any]]:
    eks = get_client("eks", region, session)
    rels = []
    for name in _paginate(eks, "list_clusters", "clusters"):
        cluster = eks.describe_cluster(name=name)["cluster"]
        kids = []
        for ng in _paginate(eks, "list_nodegroups", "nodegroups", clusterName=name):
            d = eks.describe_nodegroup(clusterName=name, nodegroupName=ng)["nodegroup"]
            kids.append({"arn": d["nodegroupArn"], "type": "eks:nodegroup", "tags": d.get("tags") or {}})
        rels.append(_rel(cluster["arn"], name, cluster.get("tags") or {}, kids))
    return rels


def _elbv2(region: str, session) -> List[Dict[str, Any]]:
    elb = get_client("elbv2", region, session)
    lbs = _paginate(elb, "describe_load_balancers", "LoadBalancers")
    tgs_by_lb: Dict[str, List[str]] = {}
    for tg in _paginate(elb, "describe_target_groups", "TargetGroups"):
        for lb_arn in tg.get("LoadBalancerArns", []):
            tgs_by_lb.setdefault(lb_arn, []).append(tg["TargetGroupArn"])
    all_arns = [lb["LoadBalancerArn"] for lb in lbs] + [a for v in tgs_by_lb.values() for a in v]
    tags: Dict[str, Dict[str, str]] = {}
    for chunk in _chunks(sorted(set(all_arns)), 20):
        for d in elb.describe_tags(ResourceArns=chunk).get("TagDescriptions", []):
            tags[d["ResourceArn"]] = _tags(d.get("Tags"))
    return [_rel(lb["LoadBalancerArn"], lb["LoadBalancerName"], tags.get(lb["LoadBalancerArn"], {}),
                 [{"arn": a, "type": "elasticloadbalancing:targetgroup", "tags": tags.get(a, {})}
                  for a in tgs_by_lb.get(lb["LoadBalancerArn"], [])])
            for lb in lbs]


def _lambda(region: str, session) -> List[Dict[str, Any]]:
    tagging = get_client("resourcegroupstaggingapi", region, session)
    fns = {i["ResourceARN"]: _tags(i.get("Tags")) for i in
           _paginate(tagging, "get_resources", "ResourceTagMappingList", ResourceTypeFilters=["lambda:function"])}
    logs = {i["ResourceARN"].removesuffix(":*"): _tags(i.get("Tags")) for i in
            _paginate(tagging, "get_resources", "ResourceTagMappingList", ResourceTypeFilters=["logs:log-group"])}
    rels = []
    for arn, tags in fns.items():
        parts = arn.split(":")
        if len(parts) < 7:
            continue
        name = parts[6]
        lg = f"arn:aws:logs:{parts[3]}:{parts[4]}:log-group:/aws/lambda/{name}"
        rels.append(_rel(arn, name, tags, [{"arn": lg, "type": "logs:log-group", "tags": logs.get(lg, {})}]))
    return rels


RULES: Dict[str, Dict[str, Any]] = {
    "vpc": {"label": "VPC → network resources", "fn": _vpc},
    "ec2_instance": {"label": "EC2 instance → EBS volumes & ENIs", "fn": _ec2_instance},
    "ebs_volume": {"label": "EBS volume → snapshots", "fn": _ebs_volume},
    "asg": {"label": "Auto Scaling group → instances", "fn": _asg},
    "ecs_service": {"label": "ECS service → tasks", "fn": _ecs_service},
    "cloudformation": {"label": "CloudFormation stack → resources", "fn": _cloudformation},
    "rds_cluster": {"label": "RDS cluster → DB instances", "fn": _rds_cluster},
    "eks_cluster": {"label": "EKS cluster → node groups", "fn": _eks_cluster},
    "elbv2": {"label": "Load balancer → target groups", "fn": _elbv2},
    "lambda": {"label": "Lambda function → log group", "fn": _lambda},
}


# ── Findings ────────────────────────────────────────────────────────

def _finding_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def findings_for(rule: str, relations: List[Dict[str, Any]], region: str) -> List[Dict[str, Any]]:
    out = []
    for rel in relations:
        expected = propagated_keys(rel["parent_tags"])
        base = {"rule": rule, "region": region, "parent_arn": rel["parent_arn"], "parent_name": rel["parent_name"]}
        for issue in rel.get("config", []):
            out.append(dict(base, kind="config", id=_finding_id(rule, rel["parent_arn"], issue["issue"]),
                            issue=issue["issue"], detail=issue["detail"], op=issue["op"]))
        if not expected:
            continue
        for child in rel["children"]:
            missing = {k: v for k, v in expected.items() if k not in child["tags"]}
            mismatched = {k: {"expected": v, "actual": child["tags"][k]}
                          for k, v in expected.items() if k in child["tags"] and child["tags"][k] != v}
            if missing or mismatched:
                out.append(dict(base, kind="tags", id=_finding_id(rule, rel["parent_arn"], child["arn"]),
                                child_arn=child["arn"], child_type=child["type"],
                                missing=missing, mismatched=mismatched))
    return out


def check(rules: Optional[List[str]], regions: List[str],
          accounts: Optional[List[Optional[str]]] = None,
          parent: Optional[str] = None) -> Dict[str, Any]:
    """
    Run propagation checks. `parent` (ARN, or a resource ID/name contained in the ARN) limits the
    result to one parent resource.
    """
    from src.aws_session import target_accounts
    rules = rules or list(RULES)
    unknown = [r for r in rules if r not in RULES]
    if unknown:
        raise ValueError(f"Unknown propagation rules: {unknown}. Known: {sorted(RULES)}")
    accounts = accounts if accounts is not None else target_accounts()
    findings: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    parents_checked: Dict[str, int] = {r: 0 for r in rules}
    for account in accounts:
        session = session_for(account)
        for region in regions:
            for rule in rules:
                try:
                    rels = RULES[rule]["fn"](region, session)
                except Exception as e:
                    logger.warning("Propagation rule %s failed in %s/%s: %s", rule, account or "default", region, e)
                    errors.append({"rule": rule, "region": region, "account": account or "default", "error": str(e)})
                    continue
                if parent:
                    rels = [r for r in rels if parent == r["parent_arn"] or parent in r["parent_arn"]
                            or parent == r["parent_name"]]
                parents_checked[rule] += len(rels)
                findings.extend(findings_for(rule, rels, region))
    by_rule: Dict[str, Dict[str, int]] = {}
    for f in findings:
        b = by_rule.setdefault(f["rule"], {"tags": 0, "config": 0})
        b[f["kind"]] += 1
    summary = {"rules": rules, "regions": regions, "parents_checked": parents_checked,
               "findings": len(findings), "by_rule": by_rule, "errors": errors,
               "timestamp": datetime.now(timezone.utc).isoformat()}
    return {"summary": summary, "findings": findings}


# ── Persistence & background runs ───────────────────────────────────

_state_lock = threading.Lock()
_running = False
_last_error: Optional[str] = None


def save_run(result: Dict[str, Any]) -> int:
    from src.db import get_connection, init_db
    init_db()
    conn = get_connection()
    with conn:
        cur = conn.execute("INSERT INTO propagation_runs (timestamp, summary_json, findings_json) VALUES (?,?,?)",
                           (result["summary"]["timestamp"], json.dumps(result["summary"]), json.dumps(result["findings"])))
        conn.execute("DELETE FROM propagation_runs WHERE id NOT IN (SELECT id FROM propagation_runs ORDER BY id DESC LIMIT 10)")
    return cur.lastrowid


def latest_run() -> Optional[Dict[str, Any]]:
    from src.db import get_connection, init_db
    init_db()
    row = get_connection().execute(
        "SELECT id, summary_json, findings_json FROM propagation_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    return {"run_id": row["id"], "summary": json.loads(row["summary_json"]), "findings": json.loads(row["findings_json"])}


def is_running() -> bool:
    return _running


def last_error() -> Optional[str]:
    return _last_error


def run_in_background(rules: Optional[List[str]], regions: List[str]) -> bool:
    """Start a check in a thread. Returns False if one is already running."""
    global _running, _last_error
    with _state_lock:
        if _running:
            return False
        _running = True
        _last_error = None

    def work():
        global _running, _last_error
        try:
            save_run(check(rules, regions))
        except Exception as e:
            _last_error = str(e)
            logger.error("Propagation check failed: %s", e)
        finally:
            _running = False

    threading.Thread(target=work, daemon=True, name="propagation-check").start()
    return True


# ── Fixing ──────────────────────────────────────────────────────────

def fix_plan(findings: List[Dict[str, Any]], overwrite: bool = False) -> Dict[str, Any]:
    """Findings → change-set targets (child tags) and ops (config fixes)."""
    targets: Dict[str, Dict[str, str]] = {}
    ops = []
    for f in findings:
        if f["kind"] == "config":
            ops.append(dict(f["op"], finding_id=f["id"]))
            continue
        tags = dict(f.get("missing", {}))
        if overwrite:
            tags.update({k: v["expected"] for k, v in f.get("mismatched", {}).items()})
        if tags:
            targets.setdefault(f["child_arn"], {}).update(tags)
    return {"targets": [{"arn": a, "tags": t} for a, t in targets.items()], "ops": ops}


def _op_client(item: Dict[str, Any], service: str):
    from src.arn_utils import account_of
    acct = account_of(item["target"])
    try:
        session = session_for(None if acct in ("", "unknown") else acct)
    except Exception:
        session = None
    return get_client(service, item["region"], session)


def _asg_apply(item: Dict[str, Any]) -> None:
    _op_client(item, "autoscaling").create_or_update_tags(Tags=[
        {"ResourceId": item["asg_name"], "ResourceType": "auto-scaling-group", "Key": k, "Value": v,
         "PropagateAtLaunch": True} for k, v in item["tags"].items()])


def _asg_undo(item: Dict[str, Any]) -> None:
    _op_client(item, "autoscaling").create_or_update_tags(Tags=[
        {"ResourceId": item["asg_name"], "ResourceType": "auto-scaling-group", "Key": k, "Value": v,
         "PropagateAtLaunch": bool(item["before_flags"].get(k))} for k, v in item["tags"].items()])


def _ecs_apply(item: Dict[str, Any]) -> None:
    _op_client(item, "ecs").update_service(cluster=item["cluster"], service=item["service"],
                                           propagateTags="SERVICE", enableECSManagedTags=True)


def _ecs_undo(item: Dict[str, Any]) -> None:
    before = item.get("before", {})
    _op_client(item, "ecs").update_service(cluster=item["cluster"], service=item["service"],
                                           propagateTags=before.get("propagateTags", "NONE"),
                                           enableECSManagedTags=bool(before.get("enableECSManagedTags")))


register_op("asg_propagate", _asg_apply, _asg_undo)
register_op("ecs_propagate", _ecs_apply, _ecs_undo)
