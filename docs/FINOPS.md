# FinOps Integration

The Tag Governance Platform provides deep FinOps integration by extending the Tag Schema with billing metadata.

## Cost Allocation Tags

To mark a tag for FinOps billing integration, update `config/tag-schema.yaml`:
```yaml
tags:
  CostCenter:
    required: true
    finops:
      cost_allocation: true
```

## Cost Explorer Report

Use the CLI to generate a FinOps report combining Tag Compliance with AWS `ce:GetCostAndUsage` data.
```bash
aws-tagging-utils finops report
```

### Attribution Limitations
AWS Cost Explorer data is up to 24 hours delayed. Note that not all AWS services support cost allocation tagging, and unblended costs may differ from invoiced costs due to taxes and credits.
