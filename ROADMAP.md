# Roadmap — from "good tagging tool" to "the tagging platform"

Ordered by impact ÷ effort. Each item says *why it matters* and *where it plugs in*.

## Known gaps (fix before calling it production-grade)

| Gap | Impact | Plug-in point |
|-----|--------|---------------|
| **Never-tagged resources are invisible.** `GetResources` (Resource Groups Tagging API) only returns resources that are or were tagged, so the compliance score is optimistic. | The #1 accuracy problem | Add an inventory source: AWS Resource Explorer (`Search` with `-tag:none`) or AWS Config `ListDiscoveredResources`, merged into `generate_report()` |
| Security, Organization and CI/CD tabs return 501 | Half-empty UI | See items 4, 6, 8 below |
| Protected-tag drift + auto-revert are placeholders (`handle_tag_change_event`) | Promised feature missing | EventBridge `Tag Change on Resource` → compare with last known tags → revert protected keys, audit, notify |
| Web app keeps refresh state in-process (one gunicorn worker) | No horizontal scaling / HA | Move scans to the SQS worker (`job_type: COMPLIANCE_SCAN`), store state in Postgres/DynamoDB, make the web tier stateless |
| `app.db` and `.compliance_cache.json` are committed to git | Leaks ARNs/audit data; stale data migrates into new installs | `git rm --cached app.db .compliance_cache.json` (now in `.gitignore`) |
| CSP header absent (UI uses inline handlers) | XSS defense-in-depth | Move inline `onclick` to listeners, then add a strict `Content-Security-Policy` |

## Differentiators (what nobody else does well together)

1. **Owner inference with confidence scores.** Combine CloudTrail creator, CloudFormation/Terraform stack, IAM role session tags, EKS namespace and Git blame of the IaC repo into a *suggested* `Owner`/`Application` value with a confidence %, then one-click bulk apply from the Compliance tab. Turns "find who owns this" from days into seconds.
2. **Dollar-ranked remediation queue.** Join violations with Cost Explorer / CUR 2.0 resource-level costs: "fix these 12 resources to allocate $41k/month". Sort the Compliance table by unallocated spend. FinOps teams buy this.
3. **Conditional policy-as-code.** Rules like "`DataClassification` required when `Environment=prod`", "`CostCenter` must exist in the CMDB", per-account overrides. Evaluate with CEL/Rego, reuse the same rules in the CLI, the API and the enforcement Lambda (one engine everywhere is already the architecture: `TagGovernanceEngine`).
4. **AWS Organizations Tag Policies round-trip.** Generate Tag Policies from `tag-schema.yaml` (like the existing SCP/Config generators), deploy them, and import `GetComplianceSummary` so the Organization tab shows per-account/OU scorecards and a leaderboard.
5. **Shift-left on real plans, not HCL text.** Evaluate `terraform show -json` plan output (including `default_tags` and module inputs) and CloudFormation change sets; a GitHub App comments on PRs with fix suggestions. The CI/CD tab then lists real runs instead of 501.
6. **Owner notifications that close the loop.** Weekly Slack/Teams/email digest per owner ("you own 14 non-compliant resources, 3 expire their exemption this week"), Jira ticket creation, SLA timers using `GOVERNANCE_GRACE_PERIOD_DAYS`.
7. **Kubernetes-aware tagging.** Propagate namespace/app labels to AWS resources created by EKS controllers (Karpenter nodes, Load Balancer Controller ALBs/NLBs, EBS CSI volumes), which are the usual untagged long tail.
8. **Conversational governance via MCP.** The MCP server already exists: add `explain_violation`, `suggest_tags`, `create_exemption` (behind `MCP_READ_ONLY`) so an agent can answer "who owns the untagged prod spend?" and fix it with approval.
9. **Immutable audit trail.** Ship the audit log to S3 with Object Lock (WORM) + Athena table, so it satisfies SOC2/ISO evidence requirements.
10. **Exemption lifecycle.** Reminder before expiry, approval workflow (requester ≠ approver), auto-expire to re-open violations, exemption metrics on the dashboard.

## Observability follow-ups

- Grafana dashboard JSON for the metrics in RUN_AND_TEST_GUIDE.md §12 (score trend, violations by tag, AWS throttling, scan duration).
- OpenTelemetry traces across web → worker → AWS calls (request IDs already propagate through logs).
- SLOs: scan freshness < 6h, API p95 < 500 ms, scan success rate > 99%.
