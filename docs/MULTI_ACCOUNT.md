# Multi-Account Architecture

The tagging governance platform runs centrally from the management or security account.

## Cross-Account Access
The `TagReport` and `Enforcement` Lambdas use `STS AssumeRole` to access member accounts.
- **Role Name**: `AWSOrganizationTagGovernanceRole`
- **Deployment**: Configured via AWS CloudFormation StackSets (`deploy/stacksets/governance-role.yaml`).

## Event Forwarding
Member accounts forward events to the central EventBridge bus via `deploy/stacksets/event-forwarder.yaml`.
