---
name: security-review
description: Review code changes for authorization failures, injection, secret exposure, unsafe deserialization, and dangerous data flows. Use when a change handles untrusted input, credentials, permissions, network boundaries, or code execution.
allowed-tools:
  - search_diff
  - search_repository
  - read_file
  - changed_line
  - symbol
  - ast_analyze
  - git_context
  - run_scanners
---

# Security review

## Mission

Identify exploitable security defects introduced by the diff. Trace untrusted data, identities, and capabilities to sensitive sinks, and verify authentication, authorization, tenant isolation, secrets handling, and integrity at every trust boundary. A suspicious API or naming pattern is only a lead until a reachable exploit path is demonstrated.

## Review procedure

1. Inventory new or changed trust boundaries: HTTP/webhook input, files, repositories, queues, environment/configuration, model output, deserialization, plugins, and external services.
2. For each boundary trace source → validation/normalization → authorization → sensitive sink. Record encoding/escaping context, privilege/tenant identity, and whether checks occur before side effects.
3. Inspect authentication separately from authorization. Confirm deny-by-default, object-level ownership, tenant scoping, method/path coverage, and protection against confused deputy or privilege escalation.
4. Check injection and execution classes: SQL/NoSQL, shell/OS, template, HTML/JS, path traversal, SSRF, unsafe deserialization, dynamic import/eval, and prompt/tool injection where model output controls tools.
5. Check secrets and sensitive data in logs, errors, URLs, telemetry, caches, artifacts, and tests. Review cryptographic use, webhook signatures, replay protection, TLS assumptions, and dependency/config changes.
6. Demonstrate exploitability with a concrete payload, identity, call chain, and resulting unauthorized action or disclosure. Use scanners only to support repository evidence.

## High-value checklist

- Authorization check missing, after the write/read, based on user-controlled fields, or applied to a collection but not each object.
- Tenant/repository/path scope can be bypassed with alternate identifiers, symlinks, encoding, case, or traversal.
- Input reaches a sink without context-appropriate parameterization, escaping, allowlisting, or size/resource limits.
- Secrets hard-coded, exposed through logs/reports/errors, or accepted through an unsafe fallback.
- Deserialization or file handling instantiates code, follows links, reads outside the package, or trusts attacker-controlled metadata.
- Webhook/token/session accepts forged, replayed, expired, cross-tenant, or over-privileged requests.

## Evidence and severity

Every finding must cite changed-line evidence plus source-to-sink/call-chain evidence and a reproducible exploit condition. High severity means remote code execution, authentication/authorization bypass, cross-tenant data exposure, or credential compromise; medium means meaningful user-scoped injection or disclosure; low means defense-in-depth weakness with a constrained exploit. Do not report theoretical risk without reachability.

## Remediation and verification

Prefer parameterized APIs, canonicalization plus allowlists, capability-scoped access, authorization before side effects, safe deserializers, secret managers, redaction, signature/replay checks, and least-privilege configuration. Require a regression test or security harness with the malicious input and an assertion that the sink is blocked while legitimate access still works.

## Output contract

Return `title`, `severity`, exact changed lines, `attacker_controlled_source`, `sensitive_sink`, `missing_control`, `exploit_payload_or_sequence`, `impact`, and `recommended_fix`. If clean, summarize trust boundaries and exploit classes checked.

- Prioritize injection, path traversal, unsafe deserialization, SSRF, command execution, insecure secrets, and privilege-boundary changes.
- Use repository evidence to establish a concrete source-to-sink path or missing authorization condition.
- Treat a suspicious API alone as a lead, not a finding; report only an exploitable defect introduced by the change.
- For high-severity findings, cite changed-line evidence plus a call chain or tool evidence.
