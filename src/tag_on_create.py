"""
Tag-on-Create: EventBridge + CloudTrail → tag new resources with Owner = IAM principal.

Trigger: EventBridge rule on `AWS API Call via CloudTrail` for supported create APIs.
Behavior: if the resource currently has no Owner tag, apply `Owner` (configurable) 
with the caller identity from CloudTrail.

Deploy: subscribe this Lambda to EventBridge; grant `tag:GetResources` and
`tag:TagResources`.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from botocore.exceptions import BotoCoreError, ClientError

from src.clients import get_cloudtrail_client, get_ec2_client, get_tagging_client
from src.config import (
    DEFAULT_REGION,
    OWNER_TAG_KEY,
    TAG_API_BATCH_SIZE as TAG_API_BATCH,
    TAG_LOOKUP_DELAY_SEC,
    TAG_LOOKUP_RETRIES,
)
from src.logging_config import get_logger

logger = get_logger(__name__)

DetailFn = Callable[[Dict[str, Any], Dict[str, Any]], List[str]]

_QUEUE_URL_ARN_RE = re.compile(
    r"^https://sqs\.([a-z0-9-]+)\.amazonaws\.com/(\d+)/(.+)$"
)


def _account_id(detail: Dict[str, Any], envelope: Dict[str, Any]) -> str:
    return str(
        detail.get("recipientAccountId") or envelope.get("account") or ""
    ).strip()


def _region(detail: Dict[str, Any], envelope: Dict[str, Any]) -> str:
    return str(
        detail.get("awsRegion") or envelope.get("region") or DEFAULT_REGION
    ).strip() or DEFAULT_REGION


def _req(detail: Dict[str, Any]) -> Dict[str, Any]:
    rp = detail.get("requestParameters")
    return rp if isinstance(rp, dict) else {}


def _resp(detail: Dict[str, Any]) -> Dict[str, Any]:
    re_el = detail.get("responseElements")
    return re_el if isinstance(re_el, dict) else {}


def extract_run_instances(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    account = _account_id(detail, envelope)
    region = _region(detail, envelope)
    items = (_resp(detail).get("instancesSet") or {}).get("items") or []
    out: List[str] = []
    for it in items:
        iid = (it or {}).get("instanceId")
        if iid:
            out.append(f"arn:aws:ec2:{region}:{account}:instance/{iid}")
    return out


_VOLUME_ARN_RE = re.compile(
    r"^arn:aws:ec2:[a-z0-9-]+:\d{12}:volume/vol-[a-f0-9]+$"
)


def _volume_arns_from_envelope(envelope: Dict[str, Any]) -> List[str]:
    raw = envelope.get("resources")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for r in raw:
        s = str(r).strip()
        if _VOLUME_ARN_RE.match(s):
            out.append(s)
    return out


def extract_create_volume(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    resp = _resp(detail)
    vid = resp.get("volumeId")
    if not vid and isinstance(resp.get("CreateVolumeResponse"), dict):
        vid = (resp.get("CreateVolumeResponse") or {}).get("volumeId")
    if vid:
        return [
            f"arn:aws:ec2:{_region(detail, envelope)}:{_account_id(detail, envelope)}:volume/{vid}"
        ]
    return _volume_arns_from_envelope(envelope)


def extract_create_function(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    arn = _resp(detail).get("functionArn")
    return [arn] if arn else []


def extract_create_bucket(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    name = _req(detail).get("bucketName")
    return [f"arn:aws:s3:::{name}"] if name else []


def extract_create_db_instance(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    arn = _resp(detail).get("dBInstanceArn")
    return [arn] if arn else []


def extract_create_db_cluster(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    arn = _resp(detail).get("dBClusterArn")
    return [arn] if arn else []


def extract_create_topic(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    arn = _resp(detail).get("topicArn")
    return [arn] if arn else []


def extract_create_queue(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    url = _resp(detail).get("queueUrl")
    if not url:
        return []
    m = _QUEUE_URL_ARN_RE.match(str(url).strip())
    if not m:
        logger.warning("Could not parse SQS queue URL: %s", url)
        return []
    reg, acct, qname = m.group(1), m.group(2), m.group(3)
    return [f"arn:aws:sqs:{reg}:{acct}:{qname}"]


def extract_create_table(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    r = _resp(detail)
    desc = r.get("tableDescription") or {}
    arn = desc.get("tableArn") if isinstance(desc, dict) else None
    return [arn] if arn else []


def extract_create_secret(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    arn = _resp(detail).get("ARN")
    return [arn] if arn else []


def extract_create_nat_gateway(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    ngw = (_resp(detail).get("natGateway") or {}) if _resp(detail) else {}
    nid = ngw.get("natGatewayId")
    if nid:
        return [f"arn:aws:ec2:{_region(detail, envelope)}:{_account_id(detail, envelope)}:natgateway/{nid}"]
    return []


def extract_create_subnet(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    sub = _resp(detail).get("subnet") or {}
    sid = sub.get("subnetId") if isinstance(sub, dict) else None
    if sid:
        return [f"arn:aws:ec2:{_region(detail, envelope)}:{_account_id(detail, envelope)}:subnet/{sid}"]
    return []


def extract_create_security_group(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    sg = _resp(detail).get("groupId")
    if sg:
        return [f"arn:aws:ec2:{_region(detail, envelope)}:{_account_id(detail, envelope)}:security-group/{sg}"]
    return []


def extract_create_elasticsearch_domain(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    ds = _resp(detail).get("domainStatus") or {}
    arn = ds.get("arn") or ds.get("ARN")
    return [arn] if arn else []


def extract_create_load_balancer(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    resp = _resp(detail)
    lbs = resp.get("loadBalancers")
    if lbs and isinstance(lbs, list):
        arn = lbs[0].get("loadBalancerArn")
        if arn:
            return [arn]
    name = _req(detail).get("loadBalancerName")
    if name:
        return [f"arn:aws:elasticloadbalancing:{_region(detail, envelope)}:{_account_id(detail, envelope)}:loadbalancer/{name}"]
    return []


def extract_create_snapshot(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    sid = _resp(detail).get("snapshotId")
    if sid:
        return [f"arn:aws:ec2:{_region(detail, envelope)}:{_account_id(detail, envelope)}:snapshot/{sid}"]
    return []


def extract_create_vpc(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    vid = (_resp(detail).get("vpc") or {}).get("vpcId")
    if vid:
        return [f"arn:aws:ec2:{_region(detail, envelope)}:{_account_id(detail, envelope)}:vpc/{vid}"]
    return []


def extract_create_internet_gateway(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    igw = (_resp(detail).get("internetGateway") or {}).get("internetGatewayId")
    if igw:
        return [f"arn:aws:ec2:{_region(detail, envelope)}:{_account_id(detail, envelope)}:internet-gateway/{igw}"]
    return []


def extract_create_route_table(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    rt = (_resp(detail).get("routeTable") or {}).get("routeTableId")
    if rt:
        return [f"arn:aws:ec2:{_region(detail, envelope)}:{_account_id(detail, envelope)}:route-table/{rt}"]
    return []


def extract_create_network_interface(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    eni = (_resp(detail).get("networkInterface") or {}).get("networkInterfaceId")
    if eni:
        return [f"arn:aws:ec2:{_region(detail, envelope)}:{_account_id(detail, envelope)}:network-interface/{eni}"]
    return []


def extract_create_cluster(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    r = _resp(detail)
    c = r.get("cluster") or {}
    if c.get("clusterArn"):
        return [c["clusterArn"]]  # ECS
    if c.get("arn"):
        return [c["arn"]]  # EKS
    if c.get("clusterIdentifier"):
        return [f"arn:aws:redshift:{_region(detail, envelope)}:{_account_id(detail, envelope)}:cluster:{c['clusterIdentifier']}"]
    return []


def extract_create_service(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    arn = (_resp(detail).get("service") or {}).get("serviceArn")
    return [arn] if arn else []


def extract_create_repository(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    arn = (_resp(detail).get("repository") or {}).get("repositoryArn")
    return [arn] if arn else []


def extract_create_stream(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    name = _req(detail).get("streamName")
    if name:
        return [f"arn:aws:kinesis:{_region(detail, envelope)}:{_account_id(detail, envelope)}:stream/{name}"]
    return []


def extract_create_key(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    arn = (_resp(detail).get("keyMetadata") or {}).get("arn")
    return [arn] if arn else []


def extract_create_log_group(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    name = _req(detail).get("logGroupName")
    if name:
        return [f"arn:aws:logs:{_region(detail, envelope)}:{_account_id(detail, envelope)}:log-group:{name}"]
    return []


def extract_create_api(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    r = _resp(detail)
    reg = _region(detail, envelope)
    if r.get("id"):
        return [f"arn:aws:apigateway:{reg}::/restapis/{r['id']}"]
    if r.get("apiId"):
        return [f"arn:aws:apigateway:{reg}::/apis/{r['apiId']}"]
    return []


def extract_create_elasticache(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    r = _resp(detail)
    reg, acc = _region(detail, envelope), _account_id(detail, envelope)
    cc = r.get("cacheCluster")
    if cc:
        return [f"arn:aws:elasticache:{reg}:{acc}:cluster:{cc.get('cacheClusterId')}"]
    rg = r.get("replicationGroup")
    if rg:
        return [f"arn:aws:elasticache:{reg}:{acc}:replicationgroup:{rg.get('replicationGroupId')}"]
    return []


def extract_create_work_group(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    name = _req(detail).get("workGroup")
    if name:
        return [f"arn:aws:athena:{_region(detail, envelope)}:{_account_id(detail, envelope)}:workgroup/{name}"]
    return []


def extract_create_glue_resource(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    req = _req(detail)
    name = req.get("name") or req.get("JobName") or req.get("CrawlerName") or req.get("DatabaseInput", {}).get("Name")
    ev = detail.get("eventName")
    res_type = "job"
    if "Crawler" in ev:
        res_type = "crawler"
    elif "Database" in ev:
        res_type = "database"
    elif "Trigger" in ev:
        res_type = "trigger"

    if name:
        return [f"arn:aws:glue:{_region(detail, envelope)}:{_account_id(detail, envelope)}:{res_type}/{name}"]
    return []


def extract_create_state_machine(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    arn = _resp(detail).get("stateMachineArn")
    return [arn] if arn else []


def extract_create_stack(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    arn = _resp(detail).get("stackId")
    return [arn] if arn else []


def extract_create_emr_cluster(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    cid = _resp(detail).get("clusterId")
    if cid:
        return [f"arn:aws:elasticmapreduce:{_region(detail, envelope)}:{_account_id(detail, envelope)}:cluster/{cid}"]
    return []


def extract_create_sagemaker_resource(detail: Dict[str, Any], envelope: Dict[str, Any]) -> List[str]:
    r = _resp(detail)
    arn = r.get("NotebookInstanceArn") or r.get("ModelArn") or r.get("TrainingJobArn") or r.get("EndpointArn")
    return [arn] if arn else []


EVENT_EXTRACTORS: Dict[str, DetailFn] = {
    "RunInstances": extract_run_instances,
    "CreateVolume": extract_create_volume,
    "CreateSnapshot": extract_create_snapshot,
    "CreateVpc": extract_create_vpc,
    "CreateSubnet": extract_create_subnet,
    "CreateSecurityGroup": extract_create_security_group,
    "CreateInternetGateway": extract_create_internet_gateway,
    "CreateNatGateway": extract_create_nat_gateway,
    "CreateRouteTable": extract_create_route_table,
    "CreateNetworkInterface": extract_create_network_interface,
    "CreateFunction": extract_create_function,
    "CreateFunction20150331": extract_create_function,
    "CreateCluster": extract_create_cluster,
    "CreateService": extract_create_service,
    "CreateRepository": extract_create_repository,
    "CreateBucket": extract_create_bucket,
    "CreateDBInstance": extract_create_db_instance,
    "CreateDBCluster": extract_create_db_cluster,
    "CreateTable": extract_create_table,
    "CreateCacheCluster": extract_create_elasticache,
    "CreateReplicationGroup": extract_create_elasticache,
    "CreateElasticsearchDomain": extract_create_elasticsearch_domain,
    "CreateDomain": extract_create_elasticsearch_domain,
    "CreateTopic": extract_create_topic,
    "CreateQueue": extract_create_queue,
    "CreateStream": extract_create_stream,
    "CreateSecret": extract_create_secret,
    "CreateKey": extract_create_key,
    "CreateLogGroup": extract_create_log_group,
    "CreateRestApi": extract_create_api,
    "CreateApi": extract_create_api,
    "CreateLoadBalancer": extract_create_load_balancer,
    "CreateWorkGroup": extract_create_work_group,
    "CreateJob": extract_create_glue_resource,
    "CreateCrawler": extract_create_glue_resource,
    "CreateDatabase": extract_create_glue_resource,
    "RunJobFlow": extract_create_emr_cluster,
    "CreateStateMachine": extract_create_state_machine,
    "CreateStack": extract_create_stack,
    "CreateNotebookInstance": extract_create_sagemaker_resource,
    "CreateModel": extract_create_sagemaker_resource,
    "CreateTrainingJob": extract_create_sagemaker_resource,
}


def owner_from_user_identity(uid: Any) -> Optional[str]:
    if not isinstance(uid, dict):
        return None
    ut = uid.get("type")
    if ut == "IAMUser":
        un = uid.get("userName")
        if un:
            return str(un)[:256]
        pid = str(uid.get("principalId") or "")
        if pid and ":" in pid:
            return pid.split(":")[-1][:256]
        return pid[:256] if pid else None
    if ut == "AssumedRole":
        name = str(uid.get("userName") or "")
        if name:
            return name[:256]
        arn = str(uid.get("arn") or "")
        if "assumed-role/" in arn:
            parts = arn.split("/")
            if len(parts) >= 3:
                return f"{parts[1]}/{parts[2]}"[:256]
            if len(parts) >= 2:
                return parts[-1][:256]
        return str(uid.get("principalId") or "assumed-role")[:256]
    if ut == "Root":
        return "root"
    if ut in ("WebIdentityUser", "IdentityCenterUser", "SAMLUser"):
        return (str(uid.get("userName") or uid.get("principalId") or "federated"))[:256]
    if ut in ("AWSService", "AWSAccount", "Unknown"):
        return None
    pid = uid.get("principalId")
    return str(pid)[:256] if pid else None


def _get_tagging_client(region: str):
    return get_tagging_client(region)


def _get_cloudtrail_client(region: str):
    return get_cloudtrail_client(region)


def fetch_tags_for_arns(client, arns: List[str]) -> Dict[str, Dict[str, str]]:
    result: Dict[str, Dict[str, str]] = {}
    pending = list(arns)
    for attempt in range(TAG_LOOKUP_RETRIES):
        if not pending:
            break
        next_pending: List[str] = []
        for i in range(0, len(pending), TAG_API_BATCH):
            batch = pending[i : i + TAG_API_BATCH]
            try:
                resp = client.get_resources(ResourceARNList=batch)
            except (ClientError, BotoCoreError) as e:
                logger.warning("get_resources batch failed: %s", e)
                next_pending.extend(batch)
                continue
            
            batch_found = set()
            for item in resp.get("ResourceTagMappingList") or []:
                arn = item.get("ResourceARN")
                if not arn: continue
                tags = {t["Key"]: t["Value"] for t in (item.get("Tags") or [])}
                result[str(arn)] = tags
                batch_found.add(str(arn))
            
            for a in batch:
                if a not in batch_found:
                    next_pending.append(a)
        
        pending = next_pending
        if pending and attempt < TAG_LOOKUP_RETRIES - 1:
            time.sleep(TAG_LOOKUP_DELAY_SEC)

    return result


def is_missing_owner_tag(tags: Dict[str, str]) -> bool:
    return OWNER_TAG_KEY not in tags or not str(tags.get(OWNER_TAG_KEY, "")).strip()


def tag_untagged_arns(client, arns: List[str], owner_value: str) -> Tuple[List[str], Dict[str, Any]]:
    tagged: List[str] = []
    failed: Dict[str, Any] = {}
    tags = {OWNER_TAG_KEY: owner_value}
    for i in range(0, len(arns), TAG_API_BATCH):
        batch = arns[i : i + TAG_API_BATCH]
        try:
            resp = client.tag_resources(ResourceARNList=batch, Tags=tags)
        except (ClientError, BotoCoreError) as e:
            for a in batch:
                failed[a] = {"ErrorMessage": str(e)}
            continue
        fm = resp.get("FailedResourcesMap") or {}
        failed.update(fm)
        for a in batch:
            if a not in fm:
                tagged.append(a)
    return tagged, failed


def lookup_owner_for_resource(region: str, resource_arn: str) -> Optional[str]:
    client = _get_cloudtrail_client(region)
    resource_id = resource_arn.split(":")[-1].split("/")[-1]
    
    for search_val in [resource_id, resource_arn]:
        try:
            paginator = client.get_paginator("lookup_events")
            pages = paginator.paginate(
                LookupAttributes=[{"AttributeKey": "ResourceName", "AttributeValue": search_val}],
                MaxResults=50
            )
            for page in pages:
                events = page.get("Events", [])
                for ev in sorted(events, key=lambda x: x.get("EventTime", 0)):
                    name = ev.get("EventName", "")
                    if any(x.lower() in name.lower() for x in ["create", "run", "deploy", "putbucket", "import", "allocate"]):
                        uid = json.loads(ev.get("CloudTrailEvent", "{}")).get("userIdentity", {})
                        owner = owner_from_user_identity(uid)
                        if owner:
                            return owner
        except Exception:
            continue
    return None


def get_region_from_arn(arn: str, default: str) -> str:
    if not arn or not isinstance(arn, str):
        return default
    parts = arn.split(":")
    if len(parts) >= 4 and parts[3]:
        return parts[3]
    return default


def _parse_json_if_string(value: Any) -> Any:
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return value
    return value


def parse_detail(event: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not isinstance(event, dict):
        return {}, {}
    raw_detail = event.get("detail")
    parsed = _parse_json_if_string(raw_detail)
    if isinstance(parsed, dict):
        return parsed, event
    if event.get("eventName"):
        return event, {}
    return {}, event


def get_all_regions() -> List[str]:
    try:
        ec2 = get_ec2_client(DEFAULT_REGION)
        regs = ec2.describe_regions()
        return [r["RegionName"] for r in regs["Regions"]]
    except Exception:
        return [DEFAULT_REGION]


def process_discovery_scan(regions: List[str], target_types: List[str] = None) -> Dict[str, Any]:
    try:
        from tag_read import RESOURCE_TYPE_MAP
    except ImportError:
        try:
            from src.tag_read import RESOURCE_TYPE_MAP
        except ImportError:
            RESOURCE_TYPE_MAP = {"EC2Instance": "ec2:instance", "S3": "s3:bucket"}
    
    results = {}
    total_tagged = 0
    
    for reg in regions:
        client = get_tagging_client(reg)
        reg_results = {"tagged": [], "failed": {}, "unidentified": []}
        
        if target_types:
            all_types = [v for k, v in RESOURCE_TYPE_MAP.items() if v in target_types or k in target_types]
        else:
            all_types = list(RESOURCE_TYPE_MAP.values())
        try:
            paginator = client.get_paginator("get_resources")
            pages = paginator.paginate(ResourceTypeFilters=all_types)
            
            to_process = []
            for page in pages:
                for item in page.get("ResourceTagMappingList", []):
                    arn = item.get("ResourceARN")
                    tags = {t["Key"]: t["Value"] for t in item.get("Tags", [])}
                    if is_missing_owner_tag(tags):
                        to_process.append(arn)
            
            for arn in to_process:
                owner = lookup_owner_for_resource(reg, arn)
                if owner:
                    t, f = tag_untagged_arns(client, [arn], owner)
                    if t:
                        reg_results["tagged"].append(arn)
                        total_tagged += 1
                    if f:
                        reg_results["failed"].update(f)
                else:
                    reg_results["unidentified"].append(arn)
        except Exception as e:
            reg_results["error"] = str(e)
            
        results[reg] = reg_results
        
    return {"total_tagged": total_tagged, "regions": results}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if isinstance(event, dict) and event.get("action") == "scan":
        target_regions = event.get("regions")
        target_types = event.get("types", [])
        if target_regions == "all":
            target_regions = get_all_regions()
        elif not isinstance(target_regions, list):
            target_regions = [DEFAULT_REGION]
        summary = process_discovery_scan(target_regions, target_types)
        return {"statusCode": 200, "body": summary}

    detail, envelope = parse_detail(event)
    event_name = str(detail.get("eventName") or "")
    extractor = EVENT_EXTRACTORS.get(event_name)
    if not extractor:
        return {"statusCode": 200, "message": "ignored"}

    uid = detail.get("userIdentity")
    owner = owner_from_user_identity(uid)
    if not owner:
        return {"statusCode": 200, "message": "skipped_no_owner"}

    arns = extractor(detail, envelope)
    arns = [a for a in arns if a]
    if not arns:
        return {"statusCode": 200, "message": "no_arns"}

    default_event_region = _region(detail, envelope)
    region_groups: Dict[str, List[str]] = {}
    for arn in arns:
        reg = get_region_from_arn(arn, default_event_region)
        region_groups.setdefault(reg, []).append(arn)

    results = []
    for reg, reg_arns in region_groups.items():
        client = get_tagging_client(reg)
        tag_maps = fetch_tags_for_arns(client, reg_arns)
        to_tag = [a for a in reg_arns if a not in tag_maps or is_missing_owner_tag(tag_maps[a])]
        
        if to_tag:
            tagged, failed = tag_untagged_arns(client, to_tag, owner)
            results.append({"region": reg, "tagged": tagged, "failed": failed})
    
    return {"statusCode": 200, "body": results}
