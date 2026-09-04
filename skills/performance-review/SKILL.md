---
name: performance-review
description: Review code changes for avoidable latency, unbounded work, excessive memory use, blocking I/O, and inefficient repeated operations. Use when a change adds loops, collection processing, queries, network calls, serialization, caching, or hot request paths.
allowed-tools:
  - search_diff
  - search_repository
  - read_file
  - changed_line
  - symbol
  - ast_analyze
  - run_repository_checks
---

# Performance review

## Mission

Find a material regression in latency, throughput, CPU, memory, I/O, or cost that is introduced by the diff and has a plausible growth path. Review request, worker, queue, batch, and startup paths according to their actual call frequency and limits.

## Review procedure

1. Locate the changed path and estimate work as a function of input size, number of records, calls, retries, and concurrency. Identify whether it is hot, user-facing, background, or one-time.
2. Compare before/after complexity and operation counts. Look for nested iteration, repeated parsing/serialization, N+1 queries, duplicate remote calls, synchronous blocking in async code, unbounded buffering, and cache invalidation storms.
3. Inspect existing pagination, batch sizes, timeouts, concurrency limits, indexes, caches, streaming APIs, and workload tests before making assumptions.
4. Quantify an amplification mechanism: an input size, request rate, fan-out, or retry pattern where the new work exceeds a documented budget or causes queue/memory growth.
5. Use benchmarks, query plans, profiling hooks, or focused tests when available. Avoid reporting a theoretical micro-optimization with no material path.

## Failure checklist

- O(n²) or worse behavior where n is user-controlled or repository-scale.
- Per-item network/database calls instead of a bounded batch or set-based operation.
- Entire payload/materialized result held in memory where streaming or pagination exists.
- Blocking filesystem/network/CPU work on an event loop or latency-sensitive thread.
- Cache key/cardinality change causing stampedes, unbounded growth, or ineffective reuse.
- Serialization/compression/retry repeated more times than the old path.

## Evidence and severity

Each finding must include changed lines, the operation-count/complexity argument, concrete scale or call frequency, and the resulting user or SLO impact. High severity means likely outage, exhaustion, or severe latency at documented scale; medium means a common path degrades materially; low means a bounded but avoidable regression. State assumptions and distinguish measured facts from inference.

## Remediation and verification

Recommend the smallest bounded, batched, cached, indexed, streaming, asynchronous, or deduplicated alternative that preserves semantics. Name a benchmark/load/query-plan regression with representative input and an acceptance threshold.

## Output contract

Return `title`, `severity`, exact changed-line evidence, `hot_path`, `growth_model`, `amplification_example`, `resource_or_slo_impact`, `assumptions`, and `recommended_fix`. If clean, summarize complexity and limits checked.

- Check nested iteration, repeated remote calls, N+1 queries, unbounded buffering, repeated serialization, and synchronous blocking in async paths.
- Use existing limits, pagination, batching, and cache semantics as evidence; do not invent workload assumptions.
- Report only a regression with a plausible growth path or request amplification mechanism.
- Recommend the smallest bounded, batched, cached, or streaming alternative consistent with current semantics.
