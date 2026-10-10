# Enforcement

The platform uses an event-driven architecture for continuous enforcement.

## EventBridge
`EventBridge` rules forward resource creation events (via CloudTrail) and Tag change events (`aws.tag`) to the central enforcement Lambda.

## Exemptions
Exemptions are configured to skip enforcement logic. An exemption requires:
- `id`
- `reason`
- `owner`
- `status` (ACTIVE or REVOKED)
- `expires_at` (Optional)

Exemptions can match by Account ID, Resource Type, Resource ID, or Environment tag.

## Protected tags

Mark a schema tag `protected: true` in `config/tag-schema.yaml` to lock it down:

```yaml
tags:
  DataClassification:
    required: true
    protected: true
```

### Who may change them

Only **SecurityAdmin** and **PlatformAdmin** can set or remove a protected tag through the app.
The check runs on every tag-writing endpoint (`/api/write`, bulk apply, propagation apply and
sync, change-set undo, remediation and auto-tagging). Keys are matched case-insensitively, so
`dataclassification` is treated as protected too.

### Drift detection and auto-revert

AWS emits a `Tag Change on Resource` event (source `aws.tag`) when tags change. The
event-forwarder StackSet sends these to the enforcement Lambda, which compares each changed
protected key with its **baseline**, meaning the last value the app authorized. Baselines live
in the governance DynamoDB table (item `RES#<arn>`, field `protected_tags`). They are updated:

- after every successful app write of a protected key;
- after each compliance scan, for keys that have no baseline yet;
- when a drifted key is on an exempted resource (the change is accepted).

The event carries only the tags *after* the change. A key with no baseline yet is therefore
learned from the event, not flagged. Run a compliance scan once after enabling protection so
that every resource has a baseline.

| `DRIFT_ENABLED` | `DRIFT_AUTO_REVERT` | Behaviour |
|---|---|---|
| `false` | any | Tag change events are ignored |
| `true` | `false` (default) | Audit `PROTECTED_TAG_DRIFT / DETECT` + `HIGH` SNS notification; resource unchanged |
| `true` | `true` | Also writes the baseline value back (`REVERT`); failures are audited as `REVERT_FAILED` |

Reverting in a member account uses `MULTI_ACCOUNT_ROLE_NAME`, which needs `tag:TagResources`
there. A revert raises its own tag change event; it matches the baseline and does nothing.
