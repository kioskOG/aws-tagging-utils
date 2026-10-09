import json
from typing import Dict, Any, List
from src.governance.models import TagSchema

class ConfigRuleGenerator:
    def __init__(self, schema: Dict[str, TagSchema]):
        self.schema = schema
        
    def generate(self) -> Dict[str, Any]:
        """
        Generates AWS Config Rules (CloudFormation) for required tags.
        """
        resources = {}
        for key, rule in self.schema.items():
            if rule.required:
                rule_name = f"RequiredTag{key.replace('-', '')}"
                resources[rule_name] = {
                    "Type": "AWS::Config::ConfigRule",
                    "Properties": {
                        "ConfigRuleName": f"required-tag-{key.lower()}",
                        "Description": f"Checks whether resources have the required tag: {key}",
                        "Source": {
                            "Owner": "AWS",
                            "SourceIdentifier": "REQUIRED_TAGS"
                        },
                        "InputParameters": {
                            "tag1Key": key
                        }
                    }
                }
                
        return {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Description": "AWS Config Rules for Tag Governance",
            "Resources": resources
        }


class SCPGenerator:
    def __init__(self, schema: Dict[str, TagSchema]):
        self.schema = schema
        
    def generate(self) -> Dict[str, Any]:
        """
        Generates an AWS Service Control Policy (SCP) to prevent resource creation 
        if mandatory tags are missing. (Preventative Control)
        Note: SCPs for tagging on creation depend heavily on the AWS service supporting it.
        We generate a generic SCP that can be refined.
        """
        statements = []
        
        required_keys = [k for k, v in self.schema.items() if v.required]
        
        if required_keys:
            # Example SCP blocking EC2 RunInstances without required tags
            # (In reality, a robust SCP needs multiple statements per service)
            statements.append({
                "Sid": "RequireTagsOnCreate",
                "Effect": "Deny",
                "Action": [
                    "ec2:RunInstances",
                    "s3:CreateBucket"
                ],
                "Resource": "*",
                "Condition": {
                    "Null": {
                        f"aws:RequestTag/{key}": "true" for key in required_keys
                    }
                }
            })
            
        return {
            "Version": "2012-10-17",
            "Statement": statements
        }
