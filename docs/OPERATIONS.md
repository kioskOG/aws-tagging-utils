# Operations

## Configuration
All behaviors are configurable via environment variables (see `src/config.py`):
- `GOVERNANCE_TERMINATION_ENABLED`: Default `false`. Set to `true` ONLY after testing.
- `GOVERNANCE_GRACE_PERIOD_DAYS`: Default `7`.

## Audit Logs
All automated actions are logged to CloudWatch Logs as structured JSON lines.
Example query in CloudWatch Logs Insights:
```
fields @timestamp, event_type, action, resource_id, result
| filter event_type = "TAG_REMEDIATION"
| sort @timestamp desc
```
