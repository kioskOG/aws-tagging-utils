# CI/CD CLI Validator

The `aws-tagging-utils` CLI allows you to shift tagging governance to the left, validating Infrastructure as Code (IaC) templates before deployment.

## Usage

```bash
./aws-tagging-utils validate <directory-or-file> [--schema config/tag-schema.yaml]
```

## Example Output

```text
Scanning terraform/...

✗ tf/aws_instance
  [MISSING_REQUIRED] Owner: Expected Value is mandatory, Actual: Missing or empty
  [INVALID_FORMAT] CostCenter: Expected Must match regex: ^[0-9]{6}$, Actual: 1234

✓ cfn/MyBucket
  All tags valid

Summary:
Resources scanned: 2
Compliant: 1
Non-compliant: 1
```

## Exit Codes
- `0`: All scanned resources are compliant.
- `1`: One or more resources have tagging violations.

## CI/CD Integration
You can easily integrate this into GitHub Actions:

```yaml
jobs:
  validate-tags:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Validate Tags
        run: ./aws-tagging-utils validate terraform/
```
