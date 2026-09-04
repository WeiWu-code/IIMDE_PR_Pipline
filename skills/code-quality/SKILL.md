---
name: code-quality
description: Review added production code for TODO or FIXME markers that may represent unfinished behavior. Use during code review and merge-readiness checks; ignore markers added under tests/.
allowed-tools:
  - search_diff
  - changed_line
---

# Review unfinished production behavior

## Mission

Find newly added production markers that disclose unfinished behavior (`TODO`, `FIXME`, and equivalent explicit placeholders) and could ship incomplete functionality. This is a narrow merge-readiness check, not a general style review.

## Review procedure

1. Inspect the diff, limiting the primary search to added lines. Use surrounding code to decide whether the marker is executable, operationally relevant, and introduced by this change.
2. Classify each marker as: unfinished behavior, a tracked intentional follow-up, a historical comment describing completed work, generated/vendor text, or test-only scaffolding.
3. For an unfinished marker, read the enclosing function and its callers to identify the missing branch, default, cleanup, validation, error handling, or integration step. Confirm that the path is reachable in production.
4. Search for an issue/owner/deadline or an existing feature flag. A reference is useful context but does not erase a correctness or safety defect when the path is reachable now.
5. Locate or run a focused regression test. Do not manufacture a finding merely because a marker lacks a test if behavior is complete.

## What counts as unfinished

- A return value, branch, exception handler, cleanup action, authorization check, persistence step, or external call is explicitly left for later.
- A placeholder implementation returns a constant, empty collection, success status, or swallowed exception on a real production path.
- A temporary bypass disables validation, observability, retry, migration, or a safety gate.

Do not flag wording such as “TODO: document the completed algorithm”, links to a completed issue, comments in `tests/`, generated files, or dead code that cannot be reached. Do not flag ordinary code that simply has no TODO comment.

## Evidence and severity

Each finding must cite the exact added line and explain the concrete input or operation that reaches it. Use high severity when the placeholder causes data loss, security exposure, or universal request failure; medium when a normal feature path is incomplete; low when impact is limited and explicitly deferred. Include the missing behavior, not just the marker text.

## Remediation and verification

Complete the behavior and add a regression test covering the path and its failure mode. If deferral is intentional, link a tracked owner and deadline, guard the path behind a safe feature flag, and make the current behavior explicit to callers. Re-run the focused test and confirm the marker is either removed or clearly non-production.

## Output contract

Return `title`, `severity`, exact line, `reachable_path`, `missing_behavior`, `impact`, `evidence`, and `recommended_fix` for each finding. If none qualify, state that added production lines were checked and list excluded marker categories.

- Ignore files under `tests/` and markers that only document completed behavior.
- Report a finding only when the marker represents unfinished production behavior.
- Cite the exact added line and explain the concrete behavior that remains incomplete.
- Recommend completing the behavior or linking a tracked owner and deadline.
- Require a regression test for the unfinished path.
