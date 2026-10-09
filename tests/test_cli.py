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
        
        assert "tf/web" in result
        assert result["tf/web"].get("Owner") == "devops"
        assert result["tf/web"].get("Environment") == "dev"
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
