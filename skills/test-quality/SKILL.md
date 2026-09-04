---
name: test-quality
description: Review test changes for missing coverage of changed behavior, ineffective assertions, brittle fixtures, and untested failure paths. Use when a diff adds or changes production behavior, tests, CI configuration, or test helpers.
allowed-tools:
  - search_diff
  - search_repository
  - read_file
  - changed_line
  - symbol
  - locate_tests
  - run_repository_checks
---

# Test quality review

## Mission

Assess whether tests provide credible evidence for behavior changed by the diff. Review coverage, assertion strength, isolation, fixtures, failure paths, concurrency, and CI/test-helper changes. This is not a request for maximal line coverage; focus on the contract and risks actually changed.

## Review procedure

1. Map each behavior-changing production edit to an existing or new test. Identify the input, observable effect, invariant, and failure mode the test is meant to prove.
2. Read assertions, setup, mocks, fixtures, and cleanup. Verify the test exercises the changed implementation rather than mocking it away or accepting any result/exception.
3. Check representative boundaries: empty/null, invalid, duplicate, permission/tenant, timeout/retry, persistence rollback, concurrency, serialization, and compatibility cases relevant to the diff.
4. Evaluate determinism and isolation: stable ordering, time/randomness control, resource cleanup, unique data, no network dependence unless intentional, and no test-order coupling.
5. For CI/config/helper changes, verify the intended tests still run, failures still fail the job, markers/filters do not hide relevant suites, and environments match production assumptions.
6. Run or locate focused checks where possible. Report missing coverage only when the untested path can regress materially.

## Ineffective-test patterns

- Assertion only checks that code did not crash, returned a non-null value, or produced a broad truthy result.
- `try/except` or mock configured to accept the exact failure under review without asserting the contract.
- Fixture omits the state that triggers the changed branch, or test calls a helper that bypasses validation/authorization/persistence.
- Snapshot/golden test updated mechanically without checking the semantic change.
- Flaky sleeps, shared mutable globals, real external services, or unbounded data that make the test non-deterministic.

## Evidence and severity

Each finding must cite the changed production/test/CI line and explain a concrete regression the suite would miss or a false signal it could emit. High severity means security, data-loss, or release-blocking behavior has no credible test; medium means a common failure/compatibility path is unprotected; low means limited boundary or maintainability risk.

## Remediation and verification

Recommend the smallest focused test: arrange the triggering state, invoke the real changed path, assert the exact result/error/side effect, and clean up. Prefer contract, integration, or property/concurrency tests where unit mocks cannot observe the risk. Re-run the relevant command and ensure it fails when the defect is reintroduced.

## Output contract

Return `title`, `severity`, changed-line evidence, `behavior_at_risk`, `missing_or_ineffective_assertion`, `reproduction_setup`, `expected_assertion`, and `recommended_test`. If clean, list the changed behaviors and the tests that credibly cover them.

- Check that assertions observe the changed effect rather than merely executing code.
- Look for tests that mock away the behavior under review or accept exceptions without validating them.
- Prioritize authorization, validation, error handling, persistence, concurrency, and compatibility paths when the diff touches them.
- Do not demand tests for comments, formatting-only edits, or behavior already covered by an unchanged focused test.
