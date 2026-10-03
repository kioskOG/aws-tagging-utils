import re
import os
import yaml
from abc import ABC, abstractmethod
from typing import Dict, List, Any


class IacParser(ABC):
    @abstractmethod
    def parse_tags(self, file_path: str) -> Dict[str, Dict[str, str]]:
        """
        Parses a file and returns a mapping of resource identifier to its tags.
        Example: {"module.ec2": {"Owner": "devops"}}
        """
        pass

class CloudFormationParser(IacParser):
    def parse_tags(self, file_path: str) -> Dict[str, Dict[str, str]]:
        result = {}
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                # Use yaml.safe_load for both YAML and JSON
                data = yaml.safe_load(f)
                
            if not isinstance(data, dict):
                return result
                
            resources = data.get("Resources", {})
            for logical_id, resource in resources.items():
                if not isinstance(resource, dict):
                    continue
                
                props = resource.get("Properties", {})
                if not isinstance(props, dict):
                    continue
                
                # CFN tags are usually a list of Key/Value dicts
                tags_prop = props.get("Tags")
                tags = {}
                if isinstance(tags_prop, list):
                    for tag_obj in tags_prop:
                        if isinstance(tag_obj, dict) and "Key" in tag_obj and "Value" in tag_obj:
                            tags[tag_obj["Key"]] = str(tag_obj["Value"])
                elif isinstance(tags_prop, dict):
                    # Sometimes tags are just a map in certain pseudo-resources or older specs
                    for k, v in tags_prop.items():
                        tags[k] = str(v)
                        
                if tags:
                    result[f"cfn/{logical_id}"] = tags
                    
        except Exception:
            # Ignore parse errors for simple validation scanning
            pass
            
        return result

class TerraformParser(IacParser):
    def parse_tags(self, file_path: str) -> Dict[str, Dict[str, str]]:
        result = {}
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            # Basic regex to find resource/module blocks and extract tags
            # This is a naive implementation to avoid heavy dependencies (like python-hcl2)
            # Looks for: resource "type" "name" { ... tags = { key = "val" } ... }
            block_regex = re.compile(r'(?:resource|module)\s+"[^"]+"\s+"([^"]+)"\s*\{([\s\S]*?^})', re.MULTILINE)
            
            for match in block_regex.finditer(content):
                resource_name = match.group(1)
                block_body = match.group(2)
                
                # Try to find a tags = { ... } block
                tags_match = re.search(r'tags\s*=\s*\{([^}]+)\}', block_body)

                if tags_match:
                    tags_str = tags_match.group(1)
                    tags = {}
                    # Extract key-value pairs
                    kv_regex = re.compile(r'([a-zA-Z0-9_-]+)\s*=\s*"([^"]*)"')
                    for kv in kv_regex.finditer(tags_str):
                        tags[kv.group(1)] = kv.group(2)
                        
                    if tags:
                        result[f"tf/{resource_name}"] = tags
                        
        except Exception:
            pass
            
        return result
