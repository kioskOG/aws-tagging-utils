# CI/CD CLI Validator

The `aws-tagging-utils` CLI allows you to shift tagging governance to the left, validating Infrastructure as Code (IaC) templates before deployment.

## Usage

```bash
./aws-tagging-utils validate <directory-or-file> [--schema config/tag-schema.yaml] [--format human|json|sarif] [--output file]
```

Run it from anywhere by calling the script by its path, or by adding the repo directory to `PATH`.
Progress messages and logs go to stderr, so stdout carries only the report and
`--format sarif > results.sarif` produces a valid SARIF file.

## What it reads

| Input | How tags are found |
|---|---|
| `*.tf` | Literal `tags = { ... }` on `resource` blocks. A value that is an expression (`var.env`, `"${var.name}-vpc"`) counts as present; its value is not checked. Tags built with `merge(...)` or `var.tags` are reported as **not checked**, never as compliant. |
| `terraform show -json` plan | `tags_all` of every taggable resource, including provider `default_tags`. **The accurate option**: resources with no tags at all are flagged too. |
| `terragrunt.hcl` | The local module in `terraform { source = "..." }` is validated as `*.tf`. Remote sources (`git::`, `tfr://`, ...) are listed in the notes. |
| CloudFormation YAML/JSON | `Properties.Tags` of each resource. |

`.terraform`, `.terragrunt-cache` and `.git` directories are skipped.

### Terragrunt: validate the plan

Static parsing can't see values passed through `inputs` or provider `default_tags`. For an exact
result, validate the plan:

```bash
cd live/accounts/mlops/us-east-2/vpc
terragrunt plan -out tf.plan
terragrunt show -json tf.plan > plan.json
aws-tagging-utils validate plan.json --format sarif > results.sarif
```

## Example Output

```text
✗ modules/vpc/vpc.tf::tf/aws_vpc.main
  [MISSING_REQUIRED] Owner: Expected Value is mandatory, Actual: Missing or empty

✓ modules/vpc/sg.tf::tf/aws_security_group.sg
  All tags valid

? modules/vpc/subnet.tf::tf/aws_subnet.private_subnet
  Not checked: tags = merge( can't be resolved statically

Summary:
Files scanned: 11
Resources validated: 2
Compliant: 1
Non-compliant: 1
Not checked (dynamic tags): 1
```

## Exit Codes
- `0`: Every validated resource is compliant.
- `1`: One or more resources have tagging violations.
- `2`: Nothing could be validated (no supported files, or only remote Terragrunt modules). Not treated as a pass.

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
