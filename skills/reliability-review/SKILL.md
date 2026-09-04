---
name: reliability-review
description: Review code changes for timeout, retry, concurrency, resource-lifetime, idempotency, and operational failure risks. Use when a change adds asynchronous work, I/O, queues, caches, background jobs, or error recovery.
allowed-tools:
  - search_diff
  - search_repository
  - read_file
  - changed_line
  - symbol
  - ast_analyze
  - locate_tests
  - run_repository_checks
---

# Reliability review

## Mission

Find operational failure modes introduced or made reachable by the change: timeouts, retries, cancellation, concurrency, resource lifetime, idempotency, queue delivery, restart/resume, and dependency failure. Do not turn a generic hardening idea into a defect without a reachable failure sequence.

## Review procedure

1. Draw the operation lifecycle: acquire, validate, perform, commit/acknowledge, release, and report. Include every success, exception, timeout, cancellation, process restart, and partial-failure exit.
2. Inspect retry policy, backoff, maximum attempts, deadlines, cancellation propagation, and whether the operation is safe to repeat. Identify externally visible work that can be duplicated.
3. For concurrent execution, identify ownership, lock/atomic primitive, ordering, visibility, conflict policy, and shutdown behavior. Check queues for ack/lease/redelivery and dead-letter semantics.
4. Check resource lifetime for files, sockets, database sessions, locks, tasks, temporary data, and tracing spans on every exit path.
5. Compare callers and tests for assumptions about status transitions, idempotency keys, checkpoints, and resume. Reproduce with a minimal timeout/interleaving or failure injection where possible.

## Failure checklist

- Unbounded or synchronized retries, retrying non-idempotent work, or losing the original deadline.
- Acknowledging before durable commit, or committing before an external side effect that cannot be reconciled.
- Cancellation ignored, swallowed, or converted to success; task can resume from an inconsistent checkpoint.
- Double release, leaked resource, lock held across remote I/O, or shutdown that abandons work.
- Concurrent update loses data, observes stale state, deadlocks, or has no conflict policy.
- Queue message disappears, duplicates without deduplication, or poison message loops forever.

## Evidence and severity

Report exact changed-line evidence and a concrete event sequence (failure, retry, interleaving, or restart) that produces loss, duplicate work, stuck work, or outage. High severity covers corruption, broad stuck queues, or unsafe repeated side effects; medium covers common request/job failures with recovery impact; low covers constrained operational degradation.

## Remediation and verification

Recommend bounded deadline-aware retries, idempotency keys, atomic transitions, lease/ack ordering, `finally` cleanup, cancellation propagation, conflict detection, or resumable checkpoints. Specify a failure-injection, concurrency, or restart regression test and its expected terminal state.

## Output contract

Return `title`, `severity`, changed-line evidence, `lifecycle_step`, `failure_sequence`, `duplicate_or_loss_risk`, `recovery_behavior`, and `recommended_fix`. If clean, list timeout, retry, cancellation, resource, concurrency, and restart cases examined.

- Check resource acquisition and release on every exit path.
- Verify retries are bounded, idempotent where required, and do not duplicate externally visible work.
- Check that concurrent updates have a clear ownership, lock, atomic primitive, or conflict policy.
- Distinguish an operational improvement from a defect: report only a failure mode the diff introduces or makes reachable.
