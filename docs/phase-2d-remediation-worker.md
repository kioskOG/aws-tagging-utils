# Phase 2D: Async Remediation Worker

## Architecture & Leasing Model

Phase 2D-2 implements asynchronous execution of remediation actions triggered by SQS. A long-running worker consumes messages from an SQS queue and delegates the remediation to the existing Phase 2C `RemediationEngine`.

### Concurrency and Safe Execution
To prevent duplicate/concurrent tag operations from SQS duplicate delivery or competing workers, a lease-based locking mechanism is implemented on the existing `ACTION#<id>` DynamoDB record.

**Lease Acquisition:**
The worker claims a remediation action atomically in `DynamoDBStateStore.claim_remediation_action()`. It uses `ConditionExpression` to acquire the lease only if:
1. The status is `PENDING` or `FAILED_RETRYABLE`, OR
2. The status is `IN_PROGRESS` but the `lease_until` timestamp has expired (to recover from crashed workers).

The lease updates the status to `IN_PROGRESS`, sets `worker_id`, increments `attempt_count`, and sets a `lease_until` timestamp (current time + 300s). This strictly guarantees that only one worker can process the AWS mutation at a time.

### SQS Message Contract
The message sent to SQS for remediation contains the `action_id` created by the synchronous `POST /api/remediation` API (which acts in a deferred creation mode when asynchronous execution is invoked).

```json
{
  "version": "1",
  "job_id": "job-<uuid>",
  "job_type": "REMEDIATION",
  "correlation_id": "req-123",
  "payload": {
    "action_id": "act-<uuid>"
  }
}
```

### Execution Flow & Idempotency

1. **Read Message**: SQS provides the `action_id`.
2. **Verify Action**: The worker fetches the action from DynamoDB. If missing, it drops the message (Poison Pill). If it's already in a terminal state (`COMPLETED`, `FAILED`, `SKIPPED_EXEMPT`), the worker assumes duplicate delivery and deletes the message from SQS.
3. **Claim Lease**: The worker invokes `claim_remediation_action`. If the lease cannot be claimed:
   - If `attempt_count` reached `MAX_REMEDIATION_ATTEMPTS` on a `FAILED_RETRYABLE` action, it transitions to `FAILED` with `MAX_RETRIES_EXHAUSTED` and the message is deleted.
   - Otherwise, the message remains in SQS to be retried (VisibilityTimeout).
4. **AWS Mutation**: The worker delegates to `_execute_tag_mutation()` inside the Phase 2C engine, strictly performing tag manipulation.
5. **Update State**: Action state is updated to `COMPLETED` or `FAILED`/`FAILED_RETRYABLE`.
6. **SQS Acknowledgement**: If successful or terminal failure, the message is deleted from SQS. If `FAILED_RETRYABLE`, it is kept in SQS to be processed again.

## Retry Model
Retries in SQS are decoupled from `attempt_count`. The attempt count is incremented atomically when the lease is acquired, strictly guarding AWS mutations. SQS redeliveries do not increment the attempt count unless a lease is successfully claimed.

## Phase 2C Preservation
The Phase 2C strict boundaries have been preserved:
- Idempotency matches based on semantic payload hash and `idempotency_key`.
- Exemptions are processed synchronously before the action enters the queue (if deferred) or inside `process_sync`.
- Safe tag execution via strict mode evaluations remain the identical abstraction.
