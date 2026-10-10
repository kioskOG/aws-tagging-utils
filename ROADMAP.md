# Roadmap — from "good tagging tool" to "the tagging platform"

Ordered by impact ÷ effort. Each item says *why it matters* and *where it plugs in*.

## Delivered in v0.4

- **Full inventory** — `INVENTORY_SOURCE=resource_explorer` finds never-tagged resources; coverage card + "discovered but not evaluated" list (services outside `RESOURCE_TYPE_MAP`).
- **Bulk fix with preview, undo and value suggestions** — every change is a change set; undo is conflict-safe.
- **Multi-account scanning** (`COMPLIANCE_ACCOUNTS`) and **leaderboards** by team / account / OU / service with week-over-week change.
- **My Resources** owner view with expiring exemptions and unallocated spend share.
- **Tag propagation** checks and fixes for 10 parent types, incl. ASG `PropagateAtLaunch` and ECS `propagateTags` config fixes; VPC sync no longer copies `Name`.

## Delivered in v0.5

- **Protected tags enforced** on every write path (SecurityAdmin / PlatformAdmin only), with **drift detection and auto-revert** from `Tag Change on Resource` events against stored baselines (`DRIFT_ENABLED`, `DRIFT_AUTO_REVERT`).
- **Write authorization fixes**: ApplicationOwner writes are checked against live tags (not the request); `/api/gov` needs TagOperator/PlatformAdmin and accepts scans only.
- **Strict CSP** (`script-src 'self'`): no inline scripts or handlers left in the UI.
- **Auth hardening**: strict ALB key-id validation; IdP groups claim configurable (`AUTH_GROUPS_CLAIM`).
- One ownership rule for the owner view and write checks; scratch files and the compliance cache removed from git (still in history).

## Known gaps (fix before calling it production-grade)

| Gap | Impact | Plug-in point |
|-----|--------|---------------|
| Resource Explorer is opt-in and needs an index per region (or an aggregator) | Default installs still miss never-tagged resources | Provide a Terraform/CloudFormation snippet that enables RE org-wide; consider making it the default |
| Snapshot ARNs carry no account ID, so cross-account fixes on snapshots run with the central credentials | Fixes on member-account snapshots fail | Carry the owning account through findings into change-set items |
| Security and CI/CD tabs return 501 | Half-empty UI | See items 5, 8 below |
| Parallel first requests on a fresh SQLite DB can fail (`OperationalError` on `/api/finops`) | Errors on a brand-new install's first page load | Initialise the DB once at startup instead of lazily per module |
| Web app keeps refresh state in-process (one gunicorn worker) | No horizontal scaling / HA | Move scans to the SQS worker (`job_type: COMPLIANCE_SCAN`), store state in Postgres/DynamoDB, make the web tier stateless |

## Differentiators (what nobody else does well together)

1. **Owner inference with confidence scores** *(v0.4 ships the first part: suggestions from stack / EKS cluster / name prefix / account neighbours, plus CloudTrail creator for single resources)*. Combine CloudTrail creator, CloudFormation/Terraform stack, IAM role session tags, EKS namespace and Git blame of the IaC repo into a *suggested* `Owner`/`Application` value with a confidence %, then one-click bulk apply from the Compliance tab. Turns "find who owns this" from days into seconds.
2. **Dollar-ranked remediation queue.** Join violations with Cost Explorer / CUR 2.0 resource-level costs: "fix these 12 resources to allocate $41k/month". Sort the Compliance table by unallocated spend. FinOps teams buy this.
3. **Conditional policy-as-code.** Rules like "`DataClassification` required when `Environment=prod`", "`CostCenter` must exist in the CMDB", per-account overrides. Evaluate with CEL/Rego, reuse the same rules in the CLI, the API and the enforcement Lambda (one engine everywhere is already the architecture: `TagGovernanceEngine`).
4. **AWS Organizations Tag Policies round-trip.** Generate Tag Policies from `tag-schema.yaml` (like the existing SCP/Config generators), deploy them, and import `GetComplianceSummary` so the Organization tab shows per-account/OU scorecards and a leaderboard.
5. **Shift-left on real plans, not HCL text.** Evaluate `terraform show -json` plan output (including `default_tags` and module inputs) and CloudFormation change sets; a GitHub App comments on PRs with fix suggestions. The CI/CD tab then lists real runs instead of 501.
6. **Owner notifications that close the loop.** Weekly Slack/Teams/email digest per owner ("you own 14 non-compliant resources, 3 expire their exemption this week"), Jira ticket creation, SLA timers using `GOVERNANCE_GRACE_PERIOD_DAYS`.
7. **Kubernetes-aware tagging** *(v0.4 covers EKS cluster → node groups and ASG → instances)*. Propagate namespace/app labels to AWS resources created by EKS controllers (Karpenter nodes, Load Balancer Controller ALBs/NLBs, EBS CSI volumes), which are the usual untagged long tail.
8. **Conversational governance via MCP.** The MCP server already exists: add `explain_violation`, `suggest_tags`, `create_exemption` (behind `MCP_READ_ONLY`) so an agent can answer "who owns the untagged prod spend?" and fix it with approval.
9. **Immutable audit trail.** Ship the audit log to S3 with Object Lock (WORM) + Athena table, so it satisfies SOC2/ISO evidence requirements.
10. **Exemption lifecycle.** Reminder before expiry, approval workflow (requester ≠ approver), auto-expire to re-open violations, exemption metrics on the dashboard.

## Observability follow-ups

- Grafana dashboard JSON for the metrics in RUN_AND_TEST_GUIDE.md §12 (score trend, violations by tag, AWS throttling, scan duration).
- OpenTelemetry traces across web → worker → AWS calls (request IDs already propagate through logs).
- SLOs: scan freshness < 6h, API p95 < 500 ms, scan success rate > 99%.
