# Phase 2D-1 Worker Foundation

## Worker Purpose
The Phase 2D-1 worker acts as the foundation for asynchronous operations in the Tag Governance platform. It runs as a long-lived polling process, reading messages from an Amazon SQS queue. In future increments, it will orchestrate asynchronous compliance scans and scheduled remediations. 

The worker implements a robust envelope validation mechanism and routes standard JSON jobs to their appropriate handlers, ensuring the safety and atomicity guaranteed by the Phase 2C architecture.

## Message Envelope
All SQS messages must follow the versioned worker message envelope:

```json
{
  "version": "1",
  "job_id": "uuid-v4-string",
  "job_type": "WORKER_HEALTH_CHECK",
  "correlation_id": "trace-uuid",
  "created_at": "2026-10-04T12:00:00Z",
  "payload": {}
}
```

- **`version`**: Must be exactly `"1"`.
- **`job_id`**: A unique identifier for the specific invocation.
- **`job_type`**: Defines the target handler. (Only `WORKER_HEALTH_CHECK` is currently supported).
- **`correlation_id`**: Trace ID injected for structured log traceability.
- **`payload`**: An object containing type-specific execution arguments.

## SQS Behavior
- **Queue Type**: Standard SQS. At-least-once delivery semantics apply.
- **Polling**: Uses Long Polling (`WaitTimeSeconds` configuration) to minimize API costs and CPU overhead.
- **Concurrency**: Processes batches of up to `MaxNumberOfMessages` sequentially per polling iteration. 
- **DLQ**: Handled entirely natively by SQS via the `maxReceiveCount` policy. No manual DLQ routing logic is performed by the worker.

## Success and Failure Semantics
1. **Valid, Handled Successfully**: The worker executes the handler, logs success, and issues a `DeleteMessage` API call to SQS.
2. **Valid, Handled with Failure**: The handler raises an exception or returns false. The worker catches the failure, logs the exception, and **does not delete** the message. SQS will eventually make the message visible again after the visibility timeout.
3. **Invalid/Malformed Payload**: If the JSON is malformed or missing required envelope fields, the worker rejects it and logs an error, but **does not delete** the message. This treats bad payloads as "poison pills", delegating their eviction to the SQS DLQ configuration.

## Configuration
The following environment variables configure the worker (defined in `src/config.py`):

- `WORKER_ENABLED`: Enables worker capabilities.
- `SQS_QUEUE_URL`: The full URL of the SQS Queue.
- `SQS_WAIT_TIME_SECONDS`: SQS Long Polling duration (default `20`).
- `SQS_VISIBILITY_TIMEOUT_SECONDS`: Time granted to the worker to finish the job before SQS redelivers (default `900`).
- `SQS_MAX_MESSAGES`: Batch size per poll (default `10`).
- `WORKER_SHUTDOWN_TIMEOUT_SECONDS`: Allowed boundary time for shutdown operations.

## Local Testing
You can test the worker locally by mocking the SQS Queue or creating a local LocalStack/AWS sandbox queue. Run the worker directly via the python module:

```bash
export SQS_QUEUE_URL="https://sqs.REGION.amazonaws.com/12345/queue-name"
python3 -m src.worker
```

Unit tests leverage `unittest.mock` to validate behavior without requiring AWS credentials:
```bash
python3 -m pytest tests/test_worker.py
```

## Graceful Shutdown
ECS Fargate frequently sends `SIGTERM` before terminating a task during scaling or deployments. The worker captures `SIGTERM` and `SIGINT`, flags a graceful shutdown state, and refuses to pick up new messages in the batch or initiate new long polls. The currently executing handler is allowed to finish. This prevents SQS messages from being falsely deleted or orphaned indefinitely in a partially processed state.

## IAM Requirements
To operate securely, the ECS Task Role must grant the following minimal permissions on the target SQS queue:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:ChangeMessageVisibility",
        "sqs:GetQueueAttributes"
      ],
      "Resource": "arn:aws:sqs:REGION:ACCOUNT:QUEUE_NAME"
    }
  ]
}
```
**Crucially**, the worker inherits the exact same Tag-Only safety limitations from Phase 2C. It must not be granted permissions like `ec2:TerminateInstances` or wildcard `*` access.

## Current Limitations
- Supports only standard JSON messages.
- Does not currently parallelize execution within a single container (processes the fetched batch sequentially).

## What is Deliberately NOT Implemented
The following features are purposefully omitted from Phase 2D-1 and will be introduced in subsequent increments:
- Remediation worker execution or Phase 2C abstraction integrations.
- Phase 2C execution leases or orphan reconciliation sweeps.
- Compliance scan execution and `SCAN#` DynamoDB state tracking.
- `SCHEDULE#` DynamoDB state tracking or API endpoints.
- EventBridge Scheduler synchronization or SNS notifications.
- The distinction between `REPORT_ONLY` and `AUTO_REMEDIATE` modes.
