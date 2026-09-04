---
name: api-compatibility
description: Review public API, CLI, configuration, event, and schema changes for backward-compatibility breaks. Use when a change modifies request or response fields, defaults, endpoints, command options, serialized data, or integration contracts.
allowed-tools:
  - search_diff
  - search_repository
  - read_file
  - changed_line
  - symbol
  - locate_tests
  - read_project_controls
---

# API compatibility review

## Mission

Determine whether the change breaks a contract that an external or separately deployed consumer can observe. Treat HTTP/GraphQL/RPC endpoints, public Python/JavaScript APIs, CLI commands, configuration files, environment variables, events, webhooks, database-backed serialized records, and generated schemas as contracts. Internal refactoring is not a finding unless it changes one of those observations.

## Review procedure

1. Establish the baseline contract from the parent version, callers, fixtures, schemas, documentation, and compatibility adapters. Record accepted inputs, defaults, output shape and ordering, status/error codes, side effects, and versioning rules.
2. Trace every changed boundary from parser/validator through handler and serializer to the consumer. Inspect both direct callers and examples/fixtures that act as consumers.
3. Compare old and new behavior for valid, omitted, null, empty, malformed, duplicated, and unknown fields. Check type coercion, enum values, nullability, requiredness, default values, pagination, ordering, and idempotency.
4. Check deployment sequencing: whether old and new clients/servers can coexist, whether a migration is reversible, and whether an adapter or feature flag already preserves compatibility.
5. Locate focused tests. If a claimed break cannot be reproduced from repository evidence, treat it as a lead rather than a finding.

## Contract-break checklist

- Request, response, event, or config field renamed, removed, reordered in an order-sensitive format, or made required.
- Type, units, encoding, precision, case sensitivity, enum set, nullability, or validation range narrowed.
- Default, timeout, retry, pagination, sorting, filtering, or idempotency semantics changed.
- HTTP method/path, CLI flag, exit code, status code, error code/message relied upon by clients, or webhook signature behavior changed.
- Serialized data, cache keys, message versions, or database records can no longer be read by the previous version.
- A compatibility shim exists but is bypassed, applied after the side effect, or only covers one caller.

## Evidence and severity

Report a finding only with (a) the changed line, (b) the affected consumer or version, (c) the old/new behavior, and (d) a concrete request, command, payload, or rollout sequence that fails. Classify as high when existing clients lose access or data, medium when a common but recoverable client path breaks, and low when a documented edge contract changes. Do not report stylistic API preferences.

## Remediation patterns

Prefer additive fields and endpoints, tolerant readers, explicit versioning, dual-read/dual-write migrations, aliases for renamed options, and deprecation windows. If a break is intentional, require a migration note, feature flag or major-version boundary, updated contract tests, and a rollback/forward-fix plan. State the smallest compatible fallback in every finding.

## Output contract

For each finding return: `title`, `severity`, exact changed-line evidence, `consumer`, `old_behavior`, `new_behavior`, `reproduction`, `impact`, and `recommended_fix`. If no break is found, say which contract surfaces were checked and why the change remains compatible.

- Look for renamed or removed fields, changed nullability or types, narrowed validation, changed defaults, and error-code changes.
- Inspect callers, fixtures, documentation, and compatibility adapters before claiming a break.
- Treat internal-only refactors as out of scope unless they alter an externally consumed contract.
- State the affected consumer and a compatible migration or fallback in every finding.
