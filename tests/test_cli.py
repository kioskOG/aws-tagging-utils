import os
import tempfile
import pytest
from src.cli.parsers import TerraformParser, CloudFormationParser

def test_terraform_parser():
    tf_content = """
resource "aws_instance" "web" {
  ami           = "ami-12345"
  instance_type = "t2.micro"

  tags = { Owner = "devops", Environment = "dev" }
}
"""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".tf") as f:
        f.write(tf_content)
        temp_path = f.name

    try:
        parser = TerraformParser()
        result = parser.parse_tags(temp_path)
        
        assert result["tf/aws_instance.web"] == {"Owner": "devops", "Environment": "dev"}
    finally:
        os.remove(temp_path)

def test_cloudformation_parser():
    cf_content = """
    Resources:
      MyBucket:
        Type: AWS::S3::Bucket
        Properties:
          Tags:
            - Key: Owner
              Value: ops
    """
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml") as f:
        f.write(cf_content)
        temp_path = f.name

    try:
        parser = CloudFormationParser()
        result = parser.parse_tags(temp_path)
        
        assert "cfn/MyBucket" in result
        assert result["cfn/MyBucket"].get("Owner") == "ops"
    finally:
        os.remove(temp_path)


# ── validate: Terraform expressions, plans, Terragrunt, exit codes ──

import json
import subprocess
import sys
from pathlib import Path

from src.cli.parsers import UNKNOWN_VALUE, TerraformPlanParser, terragrunt_module_dir

REPO = Path(__file__).resolve().parent.parent
REQUIRED_OK = 'Owner = "platform"\n    Environment = "dev"'


def run_cli(*args):
    return subprocess.run([sys.executable, str(REPO / "aws-tagging-utils"), "validate", *args],
                          capture_output=True, text=True, cwd=REPO)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_terraform_parser_expressions_and_dynamic_tags(tmp_path):
    f = write(tmp_path / "main.tf", """
resource "aws_vpc" "main" {
  cidr_block = var.cidr
  tags = {
    Name        = "${var.name}-vpc"
    Environment = var.env
    "kubernetes.io/role/elb" = 1
    Owner       = "platform" # trailing comment
  }
  lifecycle {
    ignore_changes = [tags]
  }
}

resource "aws_subnet" "a" {
  tags = merge(var.tags, { Name = "a" })
}

resource "aws_route_table_association" "a" {
  subnet_id = "x"
}
""")
    tags, unresolved = TerraformParser().parse(str(f))
    assert tags == {"tf/aws_vpc.main": {"Name": UNKNOWN_VALUE, "Environment": UNKNOWN_VALUE,
                                        "kubernetes.io/role/elb": "1", "Owner": "platform"}}
    assert list(unresolved) == ["tf/aws_subnet.a"]


def test_terraform_parser_ignores_tags_in_nested_blocks(tmp_path):
    f = write(tmp_path / "asg.tf", """
resource "aws_launch_template" "lt" {
  tag_specifications {
    tags = { Owner = "nested" }
  }
}
""")
    assert TerraformParser().parse(str(f)) == ({}, {})


def test_plan_parser_uses_tags_all_and_flags_untagged():
    plan = {"resource_changes": [
        {"address": "aws_vpc.main", "mode": "managed",
         "change": {"actions": ["create"], "after": {"tags": {"Name": "x"}, "tags_all": {"Name": "x", "Owner": "o"}},
                    "after_unknown": {"tags_all": {"Environment": True}}}},
        {"address": "aws_subnet.a", "mode": "managed",
         "change": {"actions": ["create"], "after": {"tags": None, "tags_all": {}}, "after_unknown": {}}},
        {"address": "aws_eip.x", "mode": "managed",
         "change": {"actions": ["create"], "after": {"tags": None}, "after_unknown": {"tags_all": True}}},
        {"address": "aws_s3_bucket.old", "mode": "managed", "change": {"actions": ["delete"], "after": None}},
        {"address": "data.aws_ami.a", "mode": "data", "change": {"actions": ["read"], "after": {"tags": {}}}},
        {"address": "aws_route_table_association.a", "mode": "managed",
         "change": {"actions": ["create"], "after": {"subnet_id": "s"}}},
    ]}
    tags, unresolved = TerraformPlanParser().parse_data(plan)
    assert tags == {"plan/aws_vpc.main": {"Name": "x", "Owner": "o", "Environment": UNKNOWN_VALUE},
                    "plan/aws_subnet.a": {}}
    assert list(unresolved) == ["plan/aws_eip.x"]


def test_terragrunt_source_resolution(tmp_path):
    write(tmp_path / "modules" / "vpc" / "main.tf", "")
    tg = write(tmp_path / "live" / "dev" / "vpc" / "terragrunt.hcl",
               'terraform {\n  source = "../../..//modules/vpc/"\n}\n')
    assert terragrunt_module_dir(str(tg)) == (str(tmp_path / "modules" / "vpc"), None)
    remote = write(tmp_path / "r" / "terragrunt.hcl",
                   'terraform {\n  source = "git::https://example.com/m.git//vpc?ref=v1"\n}\n')
    path, reason = terragrunt_module_dir(str(remote))
    assert path is None and "remote" in reason


def test_validate_terragrunt_stack_checks_module_and_skips_cache(tmp_path):
    write(tmp_path / "modules" / "vpc" / "main.tf",
          'resource "aws_vpc" "main" {\n  tags = {\n    Name = "x"\n  }\n}\n')
    live = tmp_path / "live" / "vpc"
    write(live / "terragrunt.hcl", 'terraform {\n  source = "../..//modules/vpc"\n}\n')
    write(live / ".terragrunt-cache" / "abc" / "main.tf",
          'resource "aws_vpc" "copy" {\n  tags = {\n    ' + REQUIRED_OK + '\n  }\n}\n')
    r = run_cli(str(live), "--format", "json")
    assert r.returncode == 1, r.stderr
    out = json.loads(r.stdout)
    assert [x["resource"].rsplit("::", 1)[1] for x in out["non_compliant"]] == ["tf/aws_vpc.main"]
    assert out["compliant"] == []


def test_validate_with_nothing_to_check_exits_2_and_sarif_stays_valid(tmp_path):
    write(tmp_path / "terragrunt.hcl", 'terraform {\n  source = "tfr:///terraform-aws-modules/vpc/aws?version=5.0.0"\n}\n')
    r = run_cli(str(tmp_path), "--format", "sarif")
    assert r.returncode == 2
    sarif = json.loads(r.stdout)  # nothing but SARIF on stdout
    inv = sarif["runs"][0]["invocations"][0]
    assert inv["executionSuccessful"] is False
    assert any("remote module" in n["message"]["text"] for n in inv["toolExecutionNotifications"])
    assert "nothing was validated" in r.stderr


def test_validate_compliant_tree_exits_0(tmp_path):
    write(tmp_path / "main.tf", 'resource "aws_vpc" "main" {\n  tags = {\n    ' + REQUIRED_OK + '\n  }\n}\n')
    r = run_cli(str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Resources validated: 1" in r.stdout


def test_unknown_values_only_need_to_be_present(tmp_path):
    """Environment = var.env can't be checked against allowed values, but counts as present."""
    write(tmp_path / "main.tf", 'resource "aws_vpc" "main" {\n  tags = {\n    Owner = "platform"\n'
                                '    Environment = var.env\n  }\n}\n')
    r = run_cli(str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
