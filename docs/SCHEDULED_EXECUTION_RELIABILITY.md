# Scheduled execution reliability and idempotency

## Delivery semantics

The scheduler persists job state and retries failed handlers with a stable execution ID for the same logical occurrence. The execution ledger adds a durable at-most-once invocation guard for side-effecting scheduled tool calls.

This is deliberately **not exactly-once external execution**. If a process exits after an external side effect but before the success checkpoint is durable, the result is ambiguous. The ledger records or recovers that invocation as `uncertain` and blocks automatic replay. The operator must reconcile the external system before creating a new logical execution.

## Execution ledger contract

1. The scheduler assigns a stable `execution_id` before firing a job and preserves it across retries and restart recovery. A successful recurring occurrence receives a new ID for the next occurrence.
2. Before the tool is invoked, `ScheduledExecutionLedger.claim` validates the ID/tool binding, acquires the interprocess lock and durably writes `started`.
3. The claim is scoped to the canonical ledger file path and execution ID. Independent ledger files cannot collide through the process-local active-execution set.
4. Each execution ID is permanently bound to the original tool name. Reusing an ID for a different tool is rejected.
5. A concurrent claim from the owning live process reports `in_progress`. A live different process is detected using PID plus process creation time. A stale/unknown owner becomes `uncertain`, never a new claim.
6. Completion writes `succeeded` or `uncertain` under the same interprocess lock. If persistence fails, the caller receives a ledger error and the invocation is not treated as safely completed.
7. If the ledger is malformed, unavailable or at its 20,000-entry capacity, scheduled side effects fail closed rather than running without a durable guard.

## Recovery and retries

- A crash-recovered scheduler job keeps its original execution ID.
- A prior `succeeded` execution is a safe no-op on replay.
- A prior `uncertain` execution is not automatically invoked again.
- A handler may explicitly defer while another in-process execution owns the same ID; this does not consume a retry attempt or rotate the ID.
- A failed/ambiguous side effect does not become safe to retry merely because the scheduler's retry timer elapsed.

## Important limitations

- The ledger prevents the application from knowingly replaying an ambiguous invocation; it cannot roll back or inspect arbitrary external systems.
- Tools that support idempotency keys should pass the execution ID to the external provider and implement reconciliation.
- If a tool's external side effect succeeds but the host never durably records success, the operator may need to verify the external state before manually creating a new execution.
- The 20,000-entry ledger currently fails closed when full; automatic compaction/retention is not enabled because deleting old IDs can permit replay after restore or delayed retries.
- The process-start-time check relies on `psutil`; if owner identity cannot be verified, the ledger chooses the conservative uncertain state.

## Regression coverage

The Windows CI suite covers persisted claims, success no-op, concurrent claims, process-restart ambiguity, persistence failures, capacity fail-closed behavior, duplicate approval callbacks, late approval after timeout, stable IDs across retries, crash recovery and tool-bound/path-scoped execution IDs.
