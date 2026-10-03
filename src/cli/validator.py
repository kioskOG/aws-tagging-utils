import argparse
import os
import sys
import json
from typing import Dict, Any, List

from src.config import GOVERNANCE_SCHEMA_PATH, GOVERNANCE_UNKNOWN_TAGS, GOVERNANCE_NORMALIZATION
from src.governance.engine import TagGovernanceEngine
from src.governance.schema_provider import FileSchemaProvider
from src.cli.parsers import CloudFormationParser, TerraformParser


def validate_directory(path: str, engine: TagGovernanceEngine) -> Dict[str, Any]:
    cfn_parser = CloudFormationParser()
    tf_parser = TerraformParser()
    
    results = {
        "compliant": [],
        "non_compliant": []
    }
    
    for root, _, files in os.walk(path):
        for file in files:
            file_path = os.path.join(root, file)
            tags_to_validate = {}
            
            if file.endswith((".tf", ".tf.json")):
                tags_to_validate = tf_parser.parse_tags(file_path)
            elif file.endswith((".yaml", ".yml", ".json")):
                # Avoid trying to parse lock files and our own schema
                if "tag-schema" in file or "lock" in file:
                    continue
                tags_to_validate = cfn_parser.parse_tags(file_path)
                
            for resource_id, tags in tags_to_validate.items():
                val_res = engine.evaluate(tags, resource_id=f"{file_path}::{resource_id}")
                if val_res.compliant:
                    results["compliant"].append(val_res)
                else:
                    results["non_compliant"].append(val_res)
                    
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="AWS Tagging Utilities - Governance CLI")
    parser.add_argument("command", choices=["validate", "generate", "report", "finops", "drift", "compliance"], help="Command to run")
    parser.add_argument("path_or_subcommand", nargs="?", help="Path to scan (validate) or artifact to generate (config/scp) or target (organization)")
    parser.add_argument("--schema", default=GOVERNANCE_SCHEMA_PATH, help="Path to tag schema file")
    parser.add_argument("--format", choices=["human", "json", "sarif"], default="human", help="Output format for validate")
    parser.add_argument("--output", help="Output file path (for sarif/json)")
    
    args = parser.parse_args()
    
    schema_provider = FileSchemaProvider(args.schema)
    
    if args.command == "validate":
        if not args.path_or_subcommand:
            print("Error: path is required for validate command")
            return 1
            
        print(f"Scanning {args.path_or_subcommand}...")
        
        engine = TagGovernanceEngine(
            schema_provider=schema_provider,
            unknown_tags_behavior=GOVERNANCE_UNKNOWN_TAGS,
            enable_normalization=GOVERNANCE_NORMALIZATION
        )
        
        if not os.path.exists(args.path_or_subcommand):
            print(f"Error: Path '{args.path_or_subcommand}' does not exist.")
            return 1
            
        results = validate_directory(args.path_or_subcommand, engine)
        
        compliant_count = len(results["compliant"])
        non_compliant_count = len(results["non_compliant"])
        
        if args.format == "sarif":
            sarif = {
                "version": "2.1.0",
                "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
                "runs": [
                    {
                        "tool": {
                            "driver": {
                                "name": "aws-tagging-utils",
                                "informationUri": "https://github.com/opstree/aws-tagging-utils",
                                "rules": [
                                    {"id": "TAG001", "name": "MissingRequiredTag", "shortDescription": {"text": "A required tag is missing."}},
                                    {"id": "TAG002", "name": "InvalidTagValue", "shortDescription": {"text": "A tag value is invalid."}}
                                ]
                            }
                        },
                        "results": []
                    }
                ]
            }
            
            for res in results["non_compliant"]:
                for v in res.violations:
                    rule_id = "TAG001" if v.type == "MISSING_REQUIRED" else "TAG002"
                    sarif["runs"][0]["results"].append({
                        "ruleId": rule_id,
                        "level": "error",
                        "message": {"text": f"[{v.type}] {v.tag}: Expected {v.expected}, Actual: {v.actual}"},
                        "locations": [{"physicalLocation": {"artifactLocation": {"uri": res.resource}}}]
                    })
                    
            out_str = json.dumps(sarif, indent=2)
            if args.output:
                with open(args.output, 'w') as f:
                    f.write(out_str)
            else:
                print(out_str)
            return 1 if non_compliant_count > 0 else 0
            
        elif args.format == "json":
            out_str = json.dumps({"compliant": [r.to_dict() for r in results["compliant"]], "non_compliant": [r.to_dict() for r in results["non_compliant"]]}, indent=2)
            if args.output:
                with open(args.output, 'w') as f:
                    f.write(out_str)
            else:
                print(out_str)
            return 1 if non_compliant_count > 0 else 0
            
        else: # Human readable
            for res in results["non_compliant"]:
                print(f"\n✗ {res.resource}")
                for v in res.violations:
                    print(f"  [{v.type}] {v.tag}: Expected {v.expected}, Actual: {v.actual}")
                    
            for res in results["compliant"]:
                print(f"\n✓ {res.resource}")
                print("  All tags valid")
                
            print("\nSummary:")
            print(f"Resources scanned: {compliant_count + non_compliant_count}")
            print(f"Compliant: {compliant_count}")
            print(f"Non-compliant: {non_compliant_count}")
            
            if non_compliant_count > 0:
                return 1

            
    elif args.command == "generate":
        from src.cli.generators import ConfigRuleGenerator, SCPGenerator
        schema = schema_provider.get_schema()
        
        if args.path_or_subcommand == "config":
            gen = ConfigRuleGenerator(schema)
            print(json.dumps(gen.generate(), indent=2))
        elif args.path_or_subcommand == "scp":
            gen = SCPGenerator(schema)
            print(json.dumps(gen.generate(), indent=2))
        else:
            print("Unknown generate target. Use 'config' or 'scp'.")
            return 1
            
    elif args.command == "finops":
        if args.path_or_subcommand == "report":
            from src.finops.report import FinOpsReportGenerator
            from src.finops.cost_explorer import CostExplorerClient
            gen = FinOpsReportGenerator(CostExplorerClient(), schema_provider)
            print(json.dumps(gen.generate_report(), indent=2))
        else:
            print("Unknown finops command. Try 'finops report'.")
            
    elif args.command == "drift":
        print("Starting manual drift scan... (Simulated CLI entrypoint)")
        
    elif args.command == "compliance":
        print("Showing overall compliance metrics... (Simulated CLI entrypoint)")
            
    return 0


if __name__ == "__main__":
    sys.exit(main())
