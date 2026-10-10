"""
Propagation rule discovery against moto, key selection, check() orchestration and the
ASG / ECS configuration ops that change sets apply and undo.
"""
import json
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from src import propagation

REGION = "us-east-1"
PARENT_TAGS = {"Owner": "platform", "Environment": "prod", "Name": "parent", "aws:createdBy": "x"}


@pytest.fixture
def aws():
    with mock_aws():
        yield


@pytest.fixture(autouse=True)
def propagate_all(monkeypatch):
    monkeypatch.setattr(propagation, "PROPAGATE_KEYS", "all")
    monkeypatch.setattr(propagation, "PROPAGATE_EXCLUDE_KEYS", ["Name"])


def _tag_list(tags):
    return [{"Key": k, "Value": v} for k, v in tags.items()]


def _only(rels, arn_part):
    [rel] = [r for r in rels if arn_part in r["parent_arn"]]
    return rel


# ── Which keys propagate ────────────────────────────────────────────

def test_propagated_keys_all_skips_excluded_aws_and_empty():
    assert propagation.propagated_keys({**PARENT_TAGS, "Empty": ""}) == {"Owner": "platform", "Environment": "prod"}


def test_propagated_keys_comma_list(monkeypatch):
    monkeypatch.setattr(propagation, "PROPAGATE_KEYS", "owner, Team")
    assert propagation.propagated_keys({"Owner": "a", "Team": "b", "Environment": "c"}) == {"Owner": "a", "Team": "b"}


def test_propagated_keys_schema(monkeypatch):
    monkeypatch.setattr(propagation, "PROPAGATE_KEYS", "schema")
    out = propagation.propagated_keys({"Owner": "a", "NotInSchema": "b", "Name": "n"})
    assert out == {"Owner": "a"}


# ── Rule discovery ──────────────────────────────────────────────────

def test_vpc_rule_finds_children_and_missing_tags(aws):
    ec2 = boto3.client("ec2", region_name=REGION)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    ec2.create_tags(Resources=[vpc], Tags=_tag_list(PARENT_TAGS))
    subnet = ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.1.0/24")["Subnet"]["SubnetId"]
    ec2.create_tags(Resources=[subnet], Tags=[{"Key": "Owner", "Value": "platform"}, {"Key": "Environment", "Value": "dev"}])
    igw = ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]
    ec2.attach_internet_gateway(InternetGatewayId=igw, VpcId=vpc)

    rel = _only(propagation._vpc(REGION, None), vpc)
    kinds = {c["type"] for c in rel["children"]}
    assert {"ec2:subnet", "ec2:security-group", "ec2:route-table", "ec2:network-acl", "ec2:internet-gateway"} <= kinds

    findings = propagation.findings_for("vpc", [rel], REGION)
    by_child = {f["child_arn"].rsplit("/", 1)[1]: f for f in findings}
    assert by_child[subnet]["mismatched"] == {"Environment": {"expected": "prod", "actual": "dev"}}
    assert by_child[subnet]["missing"] == {}
    assert by_child[igw]["missing"] == {"Owner": "platform", "Environment": "prod"}
    assert all("Name" not in f["missing"] for f in findings)


def test_ec2_instance_rule_links_volumes_and_enis(aws):
    ec2 = boto3.client("ec2", region_name=REGION)
    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    inst = ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1,
                             TagSpecifications=[{"ResourceType": "instance", "Tags": _tag_list(PARENT_TAGS)}])
    iid = inst["Instances"][0]["InstanceId"]

    rel = _only(propagation._ec2_instance(REGION, None), iid)
    types = sorted(c["type"] for c in rel["children"])
    assert "ec2:volume" in types and "ec2:network-interface" in types
    assert rel["parent_tags"]["Owner"] == "platform"
    assert all(f["missing"] for f in propagation.findings_for("ec2_instance", [rel], REGION))


def test_ebs_volume_rule_links_snapshots(aws):
    ec2 = boto3.client("ec2", region_name=REGION)
    vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=1,
                            TagSpecifications=[{"ResourceType": "volume", "Tags": _tag_list(PARENT_TAGS)}])["VolumeId"]
    snap = ec2.create_snapshot(VolumeId=vol)["SnapshotId"]

    rel = _only(propagation._ebs_volume(REGION, None), vol)
    assert [c["arn"] for c in rel["children"]] == [f"arn:aws:ec2:{REGION}::snapshot/{snap}"]


def test_asg_rule_flags_propagate_at_launch(aws):
    ec2 = boto3.client("ec2", region_name=REGION)
    asc = boto3.client("autoscaling", region_name=REGION)
    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    asc.create_launch_configuration(LaunchConfigurationName="lc", ImageId=ami, InstanceType="t3.micro")
    asc.create_auto_scaling_group(
        AutoScalingGroupName="web", LaunchConfigurationName="lc", MinSize=1, MaxSize=1,
        AvailabilityZones=[f"{REGION}a"],
        Tags=[{"Key": "Owner", "Value": "platform", "PropagateAtLaunch": True},
              {"Key": "Environment", "Value": "prod", "PropagateAtLaunch": False}])

    [rel] = propagation._asg(REGION, None)
    assert len(rel["children"]) == 1
    [issue] = rel["config"]
    assert issue["op"]["tags"] == {"Environment": "prod"}
    assert issue["op"]["before_flags"] == {"Environment": False}


def test_asg_op_apply_and_undo_toggle_flag(aws):
    asc = boto3.client("autoscaling", region_name=REGION)
    ec2 = boto3.client("ec2", region_name=REGION)
    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    asc.create_launch_configuration(LaunchConfigurationName="lc", ImageId=ami, InstanceType="t3.micro")
    asc.create_auto_scaling_group(AutoScalingGroupName="web", LaunchConfigurationName="lc", MinSize=0, MaxSize=1,
                                  AvailabilityZones=[f"{REGION}a"],
                                  Tags=[{"Key": "Environment", "Value": "prod", "PropagateAtLaunch": False}])
    [rel] = propagation._asg(REGION, None)
    op = rel["config"][0]["op"]

    def flag():
        g = asc.describe_auto_scaling_groups(AutoScalingGroupNames=["web"])["AutoScalingGroups"][0]
        return {t["Key"]: t["PropagateAtLaunch"] for t in g["Tags"]}["Environment"]

    with patch("src.propagation.session_for", return_value=None):
        propagation._asg_apply(op)
        assert flag() is True
        propagation._asg_undo(op)
        assert flag() is False


def test_ecs_service_rule_flags_propagate_tags_and_ops(aws):
    ecs = boto3.client("ecs", region_name=REGION)
    ecs.create_cluster(clusterName="c")
    ecs.register_task_definition(family="t", containerDefinitions=[{"name": "x", "image": "nginx", "memory": 128}])
    ecs.create_service(cluster="c", serviceName="api", taskDefinition="t", desiredCount=0,
                       tags=[{"key": "Owner", "value": "platform"}])

    [rel] = propagation._ecs_service(REGION, None)
    [issue] = rel["config"]
    op = issue["op"]
    assert op["before"]["propagateTags"] == "NONE"

    def svc():
        return ecs.describe_services(cluster="c", services=["api"])["services"][0]

    with patch("src.propagation.session_for", return_value=None):
        propagation._ecs_apply(op)
        assert svc()["propagateTags"] == "SERVICE"
        propagation._ecs_undo(op)
        assert svc()["propagateTags"] == "NONE"


def test_elbv2_rule_links_target_groups(aws):
    ec2 = boto3.client("ec2", region_name=REGION)
    elb = boto3.client("elbv2", region_name=REGION)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnets = [ec2.create_subnet(VpcId=vpc, CidrBlock=f"10.0.{i}.0/24", AvailabilityZone=f"{REGION}{az}")["Subnet"]["SubnetId"]
               for i, az in ((1, "a"), (2, "b"))]
    lb = elb.create_load_balancer(Name="lb", Subnets=subnets, Tags=_tag_list({"Owner": "platform"}))["LoadBalancers"][0]
    tg = elb.create_target_group(Name="tg", Protocol="HTTP", Port=80, VpcId=vpc)["TargetGroups"][0]
    elb.create_listener(LoadBalancerArn=lb["LoadBalancerArn"], Protocol="HTTP", Port=80,
                        DefaultActions=[{"Type": "forward", "TargetGroupArn": tg["TargetGroupArn"]}])

    [rel] = propagation._elbv2(REGION, None)
    assert rel["parent_tags"] == {"Owner": "platform"}
    assert [c["arn"] for c in rel["children"]] == [tg["TargetGroupArn"]]
    [f] = propagation.findings_for("elbv2", [rel], REGION)
    assert f["missing"] == {"Owner": "platform"}


def test_rds_cluster_rule_links_members(aws):
    rds = boto3.client("rds", region_name=REGION)
    rds.create_db_cluster(DBClusterIdentifier="db", Engine="aurora-postgresql", MasterUsername="u",
                          MasterUserPassword="password123", Tags=_tag_list({"Owner": "data"}))
    rds.create_db_instance(DBInstanceIdentifier="db-1", DBClusterIdentifier="db", Engine="aurora-postgresql",
                           DBInstanceClass="db.r5.large")

    [rel] = propagation._rds_cluster(REGION, None)
    assert rel["parent_tags"] == {"Owner": "data"}
    assert [c["type"] for c in rel["children"]] == ["rds:db"]


def test_eks_cluster_rule_links_nodegroups(aws):
    eks = boto3.client("eks", region_name=REGION)
    role = "arn:aws:iam::123456789012:role/eks"
    eks.create_cluster(name="k", roleArn=role, resourcesVpcConfig={"subnetIds": ["subnet-1"]}, tags={"Owner": "platform"})
    eks.create_nodegroup(clusterName="k", nodegroupName="ng", nodeRole=role, subnets=["subnet-1"])

    [rel] = propagation._eks_cluster(REGION, None)
    [child] = rel["children"]
    assert child["type"] == "eks:nodegroup"
    [f] = propagation.findings_for("eks_cluster", [rel], REGION)
    assert f["missing"] == {"Owner": "platform"}


def test_lambda_rule_maps_function_to_log_group():
    fn = "arn:aws:lambda:us-east-1:123456789012:function:job"
    lg = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/job"

    class Tagging:
        def get_paginator(self, op):
            return self

        def paginate(self, ResourceTypeFilters, **_):
            items = {"lambda:function": [{"ResourceARN": fn, "Tags": _tag_list({"Owner": "jobs"})}],
                     "logs:log-group": [{"ResourceARN": lg + ":*", "Tags": []}]}
            return [{"ResourceTagMappingList": items[ResourceTypeFilters[0]]}]

    with patch("src.propagation.get_client", return_value=Tagging()):
        [rel] = propagation._lambda(REGION, None)
    assert rel["children"] == [{"arn": lg, "type": "logs:log-group", "tags": {}}]


# ── check() ─────────────────────────────────────────────────────────

def test_check_rejects_unknown_rule():
    with pytest.raises(ValueError):
        propagation.check(["nope"], [REGION], accounts=[None])


def test_check_records_rule_errors_and_filters_parent():
    def boom(region, session):
        raise RuntimeError("AccessDenied")

    rels = [propagation._rel("arn:aws:ec2:us-east-1:1:vpc/vpc-a", "a", {"Owner": "x"},
                             [{"arn": "arn:aws:ec2:us-east-1:1:subnet/s1", "type": "ec2:subnet", "tags": {}}]),
            propagation._rel("arn:aws:ec2:us-east-1:1:vpc/vpc-b", "b", {"Owner": "y"},
                             [{"arn": "arn:aws:ec2:us-east-1:1:subnet/s2", "type": "ec2:subnet", "tags": {}}])]
    rules = {"vpc": {"label": "x", "fn": lambda region, session: rels}, "asg": {"label": "y", "fn": boom}}
    with patch.dict(propagation.RULES, rules, clear=True), patch("src.propagation.session_for", return_value=None):
        result = propagation.check(None, [REGION], accounts=[None], parent="vpc-b")

    s = result["summary"]
    assert s["parents_checked"] == {"vpc": 1, "asg": 0}
    assert [f["parent_name"] for f in result["findings"]] == ["b"]
    assert s["errors"] == [{"rule": "asg", "region": REGION, "account": "default", "error": "AccessDenied"}]
    json.dumps(result)  # persisted as JSON


def test_save_run_keeps_last_ten():
    for i in range(12):
        propagation.save_run({"summary": {"timestamp": f"2026-01-{i + 1:02d}T00:00:00+00:00", "n": i}, "findings": []})
    assert propagation.latest_run()["summary"]["n"] == 11
    from src.db import get_connection
    assert get_connection().execute("SELECT COUNT(*) FROM propagation_runs").fetchone()[0] == 10
