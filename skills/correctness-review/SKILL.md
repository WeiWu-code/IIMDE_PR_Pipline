---
name: correctness-review
description: Review code changes for incorrect state transitions, boundary conditions, error handling, data loss, and broken invariants. Use when behavior, business logic, parsing, validation, or persistence changes.
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

# Correctness review

## Mission

Prove or disprove that the diff introduces an incorrect observable result, broken invariant, invalid state transition, data loss, or mishandled failure. Review behavior, business rules, parsing, validation, caching, persistence, and orchestration; do not report a preference or an unproven suspicion.

## Review procedure

1. Summarize the changed contract: inputs, preconditions, state, outputs, side effects, and ownership of each mutated value.
2. Build a small state-transition table for normal success, empty/null input, malformed input, duplicate input, boundary values, cancellation, exception, retry, and partial failure. Follow values across helper calls, caches, and persistence.
3. Compare the new path with callers, sibling implementations, schema constraints, and existing tests. Identify assumptions about ordering, uniqueness, time, locale, encoding, or transactionality.
4. Check invariants before and after each mutation: counts, referential links, status transitions, monotonicity, idempotency, and consistency between memory and durable state.
5. Reproduce the suspected outcome with a minimal counterexample or focused test. If the path depends on an unstated precondition, show where that precondition is absent or can be violated.

## Boundary checklist

- Empty, null, zero, negative, minimum, maximum, overflow, and very large values.
- Duplicate, out-of-order, missing, unknown, or repeated events/records.
- First call versus retry, concurrent calls, timeout, cancellation, restart, and resume.
- Partial success, exception after a write, stale cache, and parse/serialization round trips.
- Authorization/validation decisions occurring before versus after side effects.

## Evidence and severity

A finding requires exact changed-line evidence, a reachable input/state, the incorrect output or invariant violation, and why existing guards do not prevent it. High severity means corruption, loss, privilege-impacting behavior, or broad request failure; medium means a common feature path is wrong; low means a constrained edge case with limited impact. Avoid “could be wrong” language without a counterexample.

## Remediation and verification

Recommend the smallest fix that restores the invariant: correct the predicate/order, validate at the boundary, make the transition atomic, preserve the old contract, or handle the failure explicitly. Name a regression test with setup, action, and expected result. Prefer tests that exercise the real changed path rather than mocks that bypass it.

## Output contract

Return `title`, `severity`, exact changed lines, `precondition`, `counterexample`, `observed_result`, `violated_invariant`, `impact`, and `recommended_fix`. If no issue is reproducible, summarize the boundary cases and invariants checked.

- Check empty, null, duplicate, maximum, minimum, ordering, retry, and partial-failure boundaries relevant to the diff.
- Verify exceptions cannot leave state, caches, or persisted records inconsistent.
- Compare callers and existing tests when an API contract or validation rule changed.
- Report only a reproducible incorrect outcome introduced by the change, with the missing precondition or counterexample.
