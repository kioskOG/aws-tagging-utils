# Remediation & Enforcement

Resources that fail the `TagGovernanceEngine` validation enter a remediation lifecycle.

## Grace Period
By default, the platform does not immediately terminate resources. 
When a non-compliant resource is detected, a DynamoDB entry is created with a `remediation_deadline` (default: 7 days).
An SNS notification is dispatched to alert the owner.

## Termination Safety Checks
If termination is explicitly enabled (`GOVERNANCE_TERMINATION_ENABLED=true`), the system will check:
1. Is the resource still non-compliant?
2. Has the grace period expired?
3. Is there a valid exemption?

If all conditions are met, the resource is terminated.
