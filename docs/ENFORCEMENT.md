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
