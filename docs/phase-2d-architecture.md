# Phase 2D Architecture

## 1. Current-State Assessment

An inspection of the repository reveals the following baseline:

*   **Compliance Scans**: **PARTIAL**. `src/tag_report.py` implements a synchronous scan via the Tagging API paginator. There is no DynamoDB tracking (`SCAN#` state is missing) and it cannot run asynchronously.
*   **Remediation**: **PARTIAL**. The Phase 2C synchronous engine (`process_sync`) in `src/governance/remediation.py` is fully implemented with DynamoDB state tracking, idempotency, and bounded retries. However, it lacks an async worker infrastructure to process large batches offline.
*   **DynamoDB State**: **PARTIAL**. `ACTION#` and `EXEMPTION#` records are implemented securely in `src/governance/state.py`. `SCAN#` and `SCHEDULE#` records do not exist.
*   **EventBridge**: **PARTIAL**. The enforcement lambda (`src/enforcement.py`) parses real-time `aws.tag` and CloudTrail tag mutation events. However, there are no cron/scheduled rules or SQS targets for operational tasks.
*   **SQS**: **MISSING**. No queues are defined or used in the application.
*   **SNS**: **PARTIAL**. Configuration (`GOVERNANCE_SNS_TOPIC_ARN`) and a placeholder `SNSNotificationProvider` exist, but comprehensive notification logic is unimplemented.
*   **Scheduled Execution**: **MISSING**. No components handle recurring jobs.
*   **Workers**: **MISSING**. There is no background consumer process for long-running tasks.
*   **Audit**: **EXISTS**. SQLite audit logging (`src/db.py`) is implemented and actively tracks remediation outcomes.
*   **Configuration**: **PARTIAL**. Application configurations are centralized in `src/config.py`, but async/worker infrastructure configurations (e.g., SQS queue URLs) are missing.
*   **Observability**: **PARTIAL**. Structured logging is implemented (`src/logging_config.py`), but deep metrics around queue depth and scan durations are missing.

---

## Architecture Decisions

### Decision 1 — Worker Model

**Decision**: A long-running ECS Fargate Worker Service polling SQS.

```text
EventBridge Scheduler
         ↓
    Amazon SQS
         ↓
Long-Running ECS Fargate Worker Service
```

**Rationale**: Launching a dedicated ECS task per SQS message (the "task-per-message" model) incurs significant AWS API overhead (e.g. `RunTask` limits), high cold-start latency, and IP/ENI exhaustion risks during large bursts. A long-running worker service polling SQS is resource-efficient, auto-scales smoothly via Target Tracking (Queue Depth), and provides continuous throughput without startup penalties.

### Decision 2 — Scheduling

**Decision**: EventBridge Scheduler → SQS. 

*   **Pattern**: We will use AWS EventBridge Scheduler for all recurring governance scans and remediation jobs. The scheduler will push messages exclusively into SQS.
*   **Isolation**: The scheduler MUST NOT directly invoke the worker container, nor directly invoke Lambda. SQS buffers the load.
*   **Realtime**: Existing real-time enforcement logic (`aws.tag` via CloudTrail) remains completely separate from this scheduled batch architecture.

### Decision 3 — SQS

**Decision**: Use SQS Standard.

*   **Delivery**: At-least-once delivery.
*   **Duplicates Expected**: Duplicate messages will occur and are treated as standard operational behavior.
*   **Idempotency Boundary**: SQS is **NOT** the idempotency mechanism. The DynamoDB state store (`ACTION#` and `SCAN#`) remains authoritative. Duplicate messages will be naturally absorbed and rejected by the DynamoDB conditional updates (as implemented in Phase 2C).
*   **DLQ**: A standard DLQ is attached. If a message is received repeatedly beyond `maxReceiveCount` (e.g., 3 times) and fails to be deleted (due to application crashes, not idempotency skips), it is routed to the DLQ. 

### Decision 4 — Scan Remediation Mode

**Decision**: Introduce `REPORT_ONLY` and `AUTO_REMEDIATE` execution modes.

*   **Default**: The absolute default for any scan must be `REPORT_ONLY`.
*   **Explicit Opt-In**: `AUTO_REMEDIATE` must be explicitly defined on the schedule payload or manual scan trigger.
*   **Safety**: Scheduled scans must never implicitly gain permission to mutate AWS resources just because the remediation engine is accessible. 

### Decision 5 — IN_PROGRESS Recovery

**Decision**: Implement an execution lease model rather than timestamp-based resetting. 

We will augment the DynamoDB `ACTION#` and `SCAN#` tables with:
*   `worker_id` (UUID unique to the specific worker process)
*   `claimed_at`
*   `lease_until` (e.g., `now() + 5 minutes`)

**Lease Lifecycle**:
*   **Lease Acquisition**: A worker claims a task by updating `status = IN_PROGRESS` and setting `worker_id` and `lease_until` atomically.
*   **Lease Renewal**: Long-running jobs must periodically update `lease_until`.
*   **Worker Crash**: If a worker crashes, the `lease_until` timestamp expires. 
*   **Safe Recovery**: Another worker evaluating the action can claim it ONLY if `status == IN_PROGRESS AND lease_until < now()`. The recovering worker generates a new `worker_id` and new `lease_until`.
*   **Duplicate Delivery**: If SQS delivers a duplicate, the second thread fails lease acquisition because `lease_until >= now()`. Two valid workers cannot execute the same action simultaneously.
*   **AWS Success / State Failure**: If AWS mutation succeeds but the subsequent state update fails, the lease naturally expires. The recovering worker retries. The Phase 2C tagging idempotency ensures the redundant AWS call is harmless, eventually leading to a successful state update to `COMPLETED`.
*   **Phase 2C Parity**: The Phase 2C state machine transitions are strictly preserved; the lease fields simply enrich the `IN_PROGRESS` state.

### Decision 6 — DynamoDB Access Patterns

**Decision**: Avoid `Scan` operations for high-frequency workflows. Access patterns are explicitly mapped to keys and GSIs.

*   **Get scan by ID**: `GetItem(PK="SCAN#<id>")`
*   **Recent scan history**: `Query` against `GSI1 (PK="TYPE#SCAN", SK="created_at")`
*   **Currently running scan**: `Query` against `GSI2 (PK="TYPE#SCAN", SK="status")` filtered for `IN_PROGRESS`.
*   **Get schedule**: `GetItem(PK="SCHEDULE#<id>")`
*   **List schedules**: `Query` against `GSI1 (PK="TYPE#SCHEDULE", SK="created_at")`
*   **Prevent overlapping schedule execution**: Atomic `ConditionExpression` checking for existing `IN_PROGRESS` scans of the same scope.
*   **Identify recoverable stale actions**: Handled dynamically during SQS processing. A worker dequeues the action, reads the record, identifies an expired `lease_until`, and atomically re-claims it.

---

## Implementation Increments

### 2D-1 Worker foundation
*   **Scope**: Create the background polling loop, SQS consumer, DLQ configuration, and gracefully handle termination signals.
*   **Dependencies**: Terraform/IaC for the SQS queue and DLQ.
*   **Tests**: Unit tests for SQS polling mock, message deletion, and DLQ routing logic.
*   **Infrastructure**: SQS Queue, SQS DLQ, ECS Worker Service definition.
*   **Rollback Strategy**: Stop the ECS Worker Service; messages safely pile up in SQS.

### 2D-2 Async remediation
*   **Scope**: Connect the worker to `remediation_engine.process_sync`. Implement the lease model (`worker_id`, `lease_until`) in `src/governance/state.py` for `ACTION#` records and build safe lease recovery logic.
*   **Dependencies**: 2D-1.
*   **Tests**: Lease acquisition, lease expiration recovery, duplicate worker rejection.
*   **Infrastructure**: None additional.
*   **Rollback Strategy**: Revert application code deployment; existing Phase 2C synchronous paths remain unharmed.

### 2D-3 Async compliance scans
*   **Scope**: Port `src/tag_report.py` logic to the worker. Introduce `SCAN#` DynamoDB records, GSIs for history/running state, and `REPORT_ONLY` vs `AUTO_REMEDIATE` execution boundaries.
*   **Dependencies**: 2D-2.
*   **Tests**: Overlapping scan rejection, scan completion tracking, reporting output integrity.
*   **Infrastructure**: DynamoDB GSI provisioning.
*   **Rollback Strategy**: Delete GSI; revert worker code.

### 2D-4 Scheduling
*   **Scope**: Create EventBridge Scheduler payload generation and `SCHEDULE#` DynamoDB records. Expose CRUD APIs in `web/app.py` for schedules.
*   **Dependencies**: 2D-3.
*   **Tests**: API integration tests, Scheduler payload format validation.
*   **Infrastructure**: IAM Role for EventBridge Scheduler to push to SQS.
*   **Rollback Strategy**: Disable EventBridge schedules via API/CLI.

### 2D-5 Notifications
*   **Scope**: Wire SNS topic publishing for terminal states (Scan Completed/Failed, Max Remediation Retries Exhausted).
*   **Dependencies**: 2D-4.
*   **Tests**: SNS payload validation, idempotency of notification (preventing duplicate sends).
*   **Infrastructure**: SNS Topic (if not already existing).
*   **Rollback Strategy**: Remove `GOVERNANCE_SNS_TOPIC_ARN` from config to dynamically disable.

---

## 15. Phase 2D Boundaries (Out of Scope)

*   PostgreSQL migration
*   Cross-account execution (Organizations assumption)
*   Drift detection event rules
*   Full FinOps reporting
*   Multi-region parallel execution within a single scan job
*   CI/CD git hooks

## Important Note

**DECISIONS RESOLVED**: All required architectural decisions have been explicitly mapped and safely resolved against the repository's current state and Phase 2C's operational guarantees. **NO DECISIONS REQUIRE USER INPUT.** Implementation may proceed in the defined sequence.
