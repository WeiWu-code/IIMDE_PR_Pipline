---
name: observability-review
description: Review code changes for missing actionable logs, metrics, traces, and error context in important operational paths. Use when a change adds external I/O, background work, retries, failures, queues, authentication, or state transitions.
allowed-tools:
  - search_diff
  - search_repository
  - read_file
  - changed_line
  - symbol
  - locate_tests
---

# Observability review

## Mission

Ensure operators can detect, diagnose, and safely correlate meaningful failures introduced by the changed path. Cover logs, metrics, traces, audit events, health signals, and alert inputs only where they support an operational decision.

## Review procedure

1. Map the changed request/job/event from entry to completion, including remote calls, queues, retries, persistence, and user-visible outcomes.
2. For each failure boundary ask: can an operator tell that it happened, identify the affected operation/tenant/request, distinguish retryable from terminal failure, and find the preserved cause?
3. Check success, failure, latency, retry, queue depth, timeout, cancellation, and partial-result signals. Verify metric names, labels, cardinality, sampling, and aggregation match repository conventions.
4. Verify trace/span propagation across asynchronous or remote boundaries and correlation IDs across logs and audit records.
5. Check redaction: never log tokens, passwords, raw credentials, sensitive payloads, or unbounded user input. Prefer safe identifiers and bounded structured fields.

## What to require

- Actionable error context at a new I/O or background boundary, preserving the original exception/cause.
- Outcome and latency visibility for externally visible remote work, retries, queues, or state transitions.
- An audit trail for security-sensitive decisions or durable administrative actions when the repository uses one.
- Alerts only when a threshold or signal maps to a documented operator action.

Do not demand logs or metrics for trivial local computation, duplicate signals already emitted by a lower layer, or high-cardinality labels that create a new outage risk.

## Evidence and severity

Report the changed boundary, the failure an operator cannot detect or correlate, the existing signal that is insufficient, and the operational decision blocked. High severity means silent security/durability failure or inability to detect a broad outage; medium means diagnosis or remediation is materially delayed; low means useful context is missing but a safe fallback exists.

## Remediation and verification

Recommend structured, bounded fields; causal exception logging; stable low-cardinality metrics; trace propagation; or an audit event consistent with existing conventions. Include a redaction check and a test or local inspection showing the signal on success and failure without secrets.

## Output contract

Return `title`, `severity`, exact changed-line evidence, `failure_boundary`, `missing_signal`, `operator_decision_blocked`, `safety_or_redaction_check`, and `recommended_fix`. If clean, list the boundaries and existing signals that make them diagnosable.

- Preserve error causes and relevant safe identifiers at failure boundaries without logging secrets or sensitive payloads.
- Look for missing outcome metrics or trace boundaries on new externally visible asynchronous or remote work.
- Do not request logs or metrics for trivial local computations with no operational decision value.
- Report missing observability only when it prevents diagnosing a meaningful changed failure or correctness outcome.
