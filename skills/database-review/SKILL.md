---
name: database-review
description: Review database queries, transactions, migrations, and persistence changes for data integrity, locking, performance, and rollback safety. Use when code changes schemas, SQL, ORM queries, transaction boundaries, or durable records.
allowed-tools:
  - search_diff
  - search_repository
  - read_file
  - changed_line
  - symbol
  - locate_tests
  - read_project_controls
---

# Database review

## Mission

Review changed SQL, ORM, migrations, transaction boundaries, and durable-record code for integrity, availability, rollback, and material query regressions. Evaluate the actual database engine and deployment order used by the repository; do not apply generic ORM advice without a failure mechanism.

## Review procedure

1. Identify the tables/collections, keys, constraints, transaction scope, isolation level, retry policy, and callers affected by the diff.
2. For reads, inspect predicates, joins, ordering, pagination, cardinality, parameter binding, and indexes. For writes, inspect uniqueness, foreign keys, nullability, upserts, lost-update protection, and affected-row checks.
3. Trace commit/rollback behavior across every exception, timeout, cancellation, and process restart. Confirm external side effects are not acknowledged before durable commit or repeated after retry without idempotency.
4. For migrations, check expand/contract sequencing, old/new binary coexistence, backfill batching and resumability, lock duration, defaults, data transformation, and a feasible rollback or forward fix.
5. Use schema files, query plans, fixtures, and focused tests as evidence. Distinguish a real unbounded or N+1 path from a bounded administrative query.

## Failure checklist

- Lost updates, write skew, duplicate records, orphaned references, or partial multi-row writes.
- Missing predicate, tenant scope, or authorization filter; accidental full-table update/delete.
- Read-modify-write race without atomic update, lock, version column, or conflict policy.
- Unbounded result/batch, N+1 query, non-sargable predicate, or lock escalation on a hot path.
- Migration that requires a new column/index before old code tolerates it, or cannot be resumed safely.
- Incorrect timezone, precision, encoding, collation, NULL semantics, or transaction isolation assumption.

## Evidence and severity

Report exact changed lines plus the schema/query/call sequence that fails. High severity covers corruption, cross-tenant exposure, irreversible migration failure, or broad outage; medium covers common duplicate/lost-write or material latency paths; low covers constrained operational risk. State database engine/version assumptions when relevant.

## Remediation and verification

Recommend parameterized queries, correct predicates, constraints, atomic statements, optimistic/pessimistic locking, bounded batches, indexes, or expand/contract rollout in the smallest compatible form. Require a migration rehearsal and rollback/forward-fix note when schema changes. Name a concurrency, integrity, or query-plan regression test.

## Output contract

Return `title`, `severity`, changed-line evidence, `database_surface`, `failure_sequence`, `invariant_or_slo`, `impact`, `rollout_risk`, and `recommended_fix`. If clean, state transaction, constraint, migration, and query-scaling checks performed.

- Check lost updates, partial writes, isolation assumptions, and missing uniqueness or foreign-key enforcement.
- Identify unbounded reads, N+1 query paths, missing predicates, and index-sensitive new queries only when they affect the changed path.
- For migrations, verify backward-compatible rollout, data backfill safety, and a feasible rollback or forward fix.
- Report a concrete integrity, availability, or performance failure rather than a generic ORM preference.
