import argparse
import os
import sys
import json
from typing import Dict, Any, List

from src.config import GOVERNANCE_SCHEMA_PATH, GOVERNANCE_UNKNOWN_TAGS, GOVERNANCE_NORMALIZATION
from src.governance.engine import TagGovernanceEngine
from src.governance.schema_provider import FileSchemaProvider
from src.cli.parsers import (UNKNOWN_VALUE, CloudFormationParser, TerraformParser,
                             TerraformPlanParser, terragrunt_module_dir)


SKIP_DIRS = {".terraform", ".terragrunt-cache", ".git", "node_modules", "__pycache__"}


def _evaluate(engine: TagGovernanceEngine, tags: Dict[str, str], resource_id: str):
    """Evaluate tags; keys whose value is an expression only need to be present."""
    unknown = {k for k, v in tags.items() if v == UNKNOWN_VALUE}
    res = engine.evaluate(tags, resource_id=resource_id)
    if unknown:
        res.violations = [v for v in res.violations if v.type == "MISSING_REQUIRED" or v.tag not in unknown]
        res.warnings = [w for w in res.warnings if w.tag not in unknown]
        res.compliant = not res.violations
    return res


def _load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def validate_directory(path: str, engine: TagGovernanceEngine) -> Dict[str, Any]:
    """
    Validate IaC under `path` (a directory or a single file): Terraform (.tf), Terraform plan
    JSON (terraform show -json), CloudFormation (YAML/JSON) and Terragrunt stacks, whose local
    `terraform.source` module is validated too. Resources whose tags can't be resolved are
    listed under "unresolved" and never counted as compliant.
    """
    cfn_parser = CloudFormationParser()
    tf_parser = TerraformParser()
    plan_parser = TerraformPlanParser()

    results: Dict[str, Any] = {"compliant": [], "non_compliant": [], "unresolved": [],
                               "files_scanned": 0, "notes": []}
    seen_files = set()
    module_dirs: List[str] = []

    def scan_file(file_path: str) -> None:
        real = os.path.realpath(file_path)
        if real in seen_files:
            return
        seen_files.add(real)
        name = os.path.basename(file_path)
        found, unresolved = {}, {}
        if name == "terragrunt.hcl":
            results["files_scanned"] += 1
            module, reason = terragrunt_module_dir(file_path)
            if module:
                module_dirs.append(module)
            else:
                results["notes"].append(f"{file_path}: module not checked ({reason}). "
                                        "Validate a plan instead: terragrunt plan -out tf.plan && "
                                        "terragrunt show -json tf.plan > plan.json")
            return
        if name.endswith(".tf"):
            found, unresolved = tf_parser.parse(file_path)
        elif name.endswith(".json"):
            data = _load_json(file_path)
            if plan_parser.is_plan(data):
                found, unresolved = plan_parser.parse_data(data)
            elif isinstance(data, dict) and "Resources" in data:
                found = cfn_parser.parse_tags(file_path)
            else:
                return
        elif name.endswith((".yaml", ".yml")):
            if "tag-schema" in name or "lock" in name:
                return
            found = cfn_parser.parse_tags(file_path)
            if not found:
                return
        else:
            return
        results["files_scanned"] += 1
        for resource_id, tags in found.items():
            res = _evaluate(engine, tags, f"{file_path}::{resource_id}")
            results["compliant" if res.compliant else "non_compliant"].append(res)
        for resource_id, reason in unresolved.items():
            results["unresolved"].append({"resource": f"{file_path}::{resource_id}", "reason": reason})

    def scan_tree(root_path: str) -> None:
        if os.path.isfile(root_path):
            scan_file(root_path)
            return
        for root, dirs, files in os.walk(root_path):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for file in sorted(files):
                scan_file(os.path.join(root, file))

    scan_tree(path)
    scanned_modules = set()
    while module_dirs:
        module = module_dirs.pop(0)
        if module not in scanned_modules:
            scanned_modules.add(module)
            results["notes"].append(f"Validated Terragrunt module {module} (values from inputs and "
                                    "default_tags are not visible; validate a plan for full accuracy)")
            scan_tree(module)
    return results


def _log(msg: str) -> None:
    """Progress and diagnostics go to stderr so stdout stays machine-readable."""
    print(msg, file=sys.stderr)


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
            _log("Error: path is required for validate command")
            return 1

        _log(f"Scanning {args.path_or_subcommand}...")

        engine = TagGovernanceEngine(
            schema_provider=schema_provider,
            unknown_tags_behavior=GOVERNANCE_UNKNOWN_TAGS,
            enable_normalization=GOVERNANCE_NORMALIZATION
        )

        if not os.path.exists(args.path_or_subcommand):
            _log(f"Error: Path '{args.path_or_subcommand}' does not exist.")
            return 1

        results = validate_directory(args.path_or_subcommand, engine)

        compliant_count = len(results["compliant"])
        non_compliant_count = len(results["non_compliant"])
        unresolved = results["unresolved"]
        evaluated = compliant_count + non_compliant_count
        nothing_checked = evaluated == 0
        if nothing_checked:
            results["notes"].append(
                f"No resources with tags were found to validate ({results['files_scanned']} files scanned). "
                "Supported: .tf, terraform show -json plan files, CloudFormation templates, and "
                "terragrunt.hcl with a local terraform.source.")
        # 1 = violations, 2 = nothing could be validated, 0 = everything evaluated is compliant
        exit_code = 1 if non_compliant_count else (2 if nothing_checked else 0)

        if args.format == "sarif":
            notifications = [{"level": "warning", "message": {"text": n}} for n in results["notes"]]
            notifications += [{"level": "warning", "message": {"text": f"{u['resource']}: {u['reason']}"}}
                              for u in unresolved]
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
                        "invocations": [{
                            "executionSuccessful": not nothing_checked,
                            "toolExecutionNotifications": notifications,
                        }],
                        "results": []
                    }
                ]
            }

            for res in results["non_compliant"]:
                file_path = res.resource.split("::", 1)[0]
                for v in res.violations:
                    rule_id = "TAG001" if v.type == "MISSING_REQUIRED" else "TAG002"
                    sarif["runs"][0]["results"].append({
                        "ruleId": rule_id,
                        "level": "error",
                        "message": {"text": f"{res.resource.split('::', 1)[-1]}: [{v.type}] {v.tag}: Expected {v.expected}, Actual: {v.actual}"},
                        "locations": [{"physicalLocation": {"artifactLocation": {"uri": file_path}}}]
                    })

            out_str = json.dumps(sarif, indent=2)
            if args.output:
                with open(args.output, 'w') as f:
                    f.write(out_str)
            else:
                print(out_str)

        elif args.format == "json":
            out_str = json.dumps({"compliant": [r.to_dict() for r in results["compliant"]],
                                  "non_compliant": [r.to_dict() for r in results["non_compliant"]],
                                  "unresolved": unresolved, "files_scanned": results["files_scanned"],
                                  "notes": results["notes"]}, indent=2)
            if args.output:
                with open(args.output, 'w') as f:
                    f.write(out_str)
            else:
                print(out_str)

        else: # Human readable
            for res in results["non_compliant"]:
                print(f"\n✗ {res.resource}")
                for v in res.violations:
                    print(f"  [{v.type}] {v.tag}: Expected {v.expected}, Actual: {v.actual}")

            for res in results["compliant"]:
                print(f"\n✓ {res.resource}")
                print("  All tags valid")

            for u in unresolved:
                print(f"\n? {u['resource']}")
                print(f"  Not checked: {u['reason']}")

            for n in results["notes"]:
                print(f"\nNote: {n}")

            print("\nSummary:")
            print(f"Files scanned: {results['files_scanned']}")
            print(f"Resources validated: {evaluated}")
            print(f"Compliant: {compliant_count}")
            print(f"Non-compliant: {non_compliant_count}")
            print(f"Not checked (dynamic tags): {len(unresolved)}")

        if nothing_checked:
            _log("Error: nothing was validated. " + results["notes"][-1])
        return exit_code

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
