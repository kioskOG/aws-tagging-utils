import logging
import os
import json
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_REGION = os.environ.get("AWS_REGION", "us-east-2")

def get_tagging_client(region: str):
    return boto3.client("resourcegroupstaggingapi", region_name=region)

def get_ec2_client(region: str):
    return boto3.client("ec2", region_name=region)

def get_sts_client():
    return boto3.client("sts")

def get_account_id() -> str:
    return get_sts_client().get_caller_identity()["Account"]

def sync_vpc_tags(region: str, vpc_id: str) -> Dict[str, Any]:
    """
    Propagate tags from a VPC to its child resources (Subnets, SGs, RTs, etc.)
    """
    ec2 = get_ec2_client(region)
    tag_api = get_tagging_client(region)
    account_id = get_account_id()
    
    logger.info("Syncing tags for VPC %s in %s", vpc_id, region)
    
    # 1. Get VPC tags
    try:
        vpcs = ec2.describe_vpcs(VpcIds=[vpc_id])
        if not vpcs["Vpcs"]:
            return {"error": f"VPC {vpc_id} not found"}
        
        vpc_tags = {t["Key"]: t["Value"] for t in vpcs["Vpcs"][0].get("Tags", [])}
        if not vpc_tags:
            return {"message": "VPC has no tags to propagate"}
    except Exception as e:
        return {"error": f"Failed to describe VPC: {str(e)}"}
        
    results = {"vpc_id": vpc_id, "updated_resources": []}
    
    # 2. Find children and construct ARNs
    children_arns = []
    
    # Subnets
    subs = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    for s in subs["Subnets"]:
        children_arns.append(f"arn:aws:ec2:{region}:{account_id}:subnet/{s['SubnetId']}")
    
    # Security Groups
    sgs = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    for sg in sgs["SecurityGroups"]:
        # Skip default SG if needed? Usually we tag all.
        children_arns.append(f"arn:aws:ec2:{region}:{account_id}:security-group/{sg['GroupId']}")
        
    # Route Tables
    rts = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    for rt in rts["RouteTables"]:
        children_arns.append(f"arn:aws:ec2:{region}:{account_id}:route-table/{rt['RouteTableId']}")

    # Internet Gateways
    igws = ec2.describe_internet_gateways(Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}])
    for igw in igws["InternetGateways"]:
        children_arns.append(f"arn:aws:ec2:{region}:{account_id}:internet-gateway/{igw['InternetGatewayId']}")
        
    # NAT Gateways
    ngws = ec2.describe_nat_gateways(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    for ngw in ngws["NatGateways"]:
        children_arns.append(f"arn:aws:ec2:{region}:{account_id}:natgateway/{ngw['NatGatewayId']}")

    # 3. Apply tags in batches (max 20 per call for Resource Groups Tagging API)
    BATCH_SIZE = 20
    for i in range(0, len(children_arns), BATCH_SIZE):
        batch = children_arns[i : i + BATCH_SIZE]
        try:
            tag_api.tag_resources(ResourceARNList=batch, Tags=vpc_tags)
            results["updated_resources"].extend(batch)
            logger.info("Successfully tagged batch of %d resources", len(batch))
        except Exception as e:
            logger.error("Failed to tag batch: %s", e)
            results.setdefault("errors", []).append(str(e))
            
    return results

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main entry point for TagSync.
    
    Event Format:
    {
        "action": "sync_vpc",
        "vpc_id": "vpc-12345678",
        "region": "us-east-1"
    }
    """
    logger.info("Received event: %s", event)
    
    action = event.get("action", "sync_vpc")
    region = event.get("region", DEFAULT_REGION)
    
    try:
        if action == "sync_vpc":
            vpc_id = event.get("vpc_id")
            if not vpc_id:
                return {"statusCode": 400, "body": {"message": "vpc_id is required for sync_vpc action"}}
            
            result = sync_vpc_tags(region, vpc_id)
            return {"statusCode": 200, "body": result}
        
        else:
            return {"statusCode": 400, "body": {"message": f"Unsupported action: {action}"}}
            
    except Exception as e:
        logger.exception("Unexpected error in TagSync")
        return {"statusCode": 500, "body": {"message": str(e)}}

if __name__ == "__main__":
    # Example local execution:
    # python src/tag_sync.py vpc-12345678 us-east-1
    import sys
    if len(sys.argv) > 1:
        v_id = sys.argv[1]
        reg = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_REGION
        print(json.dumps(sync_vpc_tags(reg, v_id), indent=2))
