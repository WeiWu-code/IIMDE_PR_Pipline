"""Product-backed agentic evaluation suite for labelled PRs."""
from collections import Counter
import json
import random
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from .agentic_core import AgenticReviewer
from .evaluation_harness import EndToEndEvaluationHarness, dataset_fingerprint, one_to_one_match
from .finding_identity import canonical_identity
from .llm import JsonChatClient
from .models import Finding, Severity
from .reviewer import LocalRuleReviewer, Reviewer
from .review_rules import ContextRuleReviewer
from .skills import AgentSkill
from .telemetry import ExecutionLedger


REQUIRED_ARMS = (
    "multi-llm-no-critic", "full-agentic",
)

EXPERIMENT_ARMS = (
    "single-llm",
    "single-llm-scanner",
    "multi-llm-no-critic",
    "full-agentic",
    "full-agentic-evolved-skill",
)

ARM_TOPOLOGY = {
    "single-llm": {
        "mode": "single",
        "roles": ("single-reviewer",),
        "deterministic_scanners": False,
    },
    "single-llm-scanner": {
        "mode": "single",
        "roles": ("single-reviewer",),
        "deterministic_scanners": True,
    },
    "multi-llm-no-critic": {
        "mode": "agentic",
        "roles": ("lead", "security", "correctness-reliability"),
        "deterministic_scanners": True,
    },
    "full-agentic": {
        "mode": "agentic",
        "roles": ("lead", "security", "correctness-reliability", "critic"),
        "deterministic_scanners": True,
    },
    "full-agentic-evolved-skill": {
        "mode": "agentic",
        "roles": ("lead", "security", "correctness-reliability", "critic"),
        "deterministic_scanners": True,
    },
}


SINGLE_REVIEW_PROMPT = """You are a single-model code reviewer. Review only defects introduced
by added lines in the supplied unified diff. Return actionable findings, not style comments. Use
the exact changed path and line. Return JSON only:
{"findings":[{"cwe":"CWE-...","rule_id":"...","severity":"critical|high|medium|low",
"title":"...","explanation":"...","path":"...","line":1,"evidence":"exact code",
"fix":"...","test":"...","confidence":0.0}]}"""


def validate_real_dataset(cases: List[dict], minimum_cases: int = 300) -> Dict[str, Any]:
    repositories_by_split = {}
    cases_by_split = Counter()
    source_kinds = set()
    for case in cases:
        split = str(case.get("split", ""))
        if split not in {"train", "validation", "holdout"}:
            raise ValueError("every case must use train, validation or holdout split")
        repositories_by_split.setdefault(split, set()).add(str(case.get("repository", "")))
        cases_by_split[split] += 1
        source_kinds.add(str((case.get("source") or {}).get("kind", "unknown")))
        if not isinstance(case.get("expected_findings"), list):
            raise ValueError("every real PR must include human expected_findings")
        for finding in case["expected_findings"]:
            if "should_comment" not in finding:
                raise ValueError("human labels must include should_comment")
            if not all(key in finding for key in ("severity", "path")) or not (
                "line" in finding or "start_line" in finding
            ):
                raise ValueError(
                    "human labels require severity, path and line/start_line"
                )
    overlaps = {}
    splits = sorted(repositories_by_split)
    for index, left in enumerate(splits):
        for right in splits[index + 1:]:
            shared = repositories_by_split[left].intersection(repositories_by_split[right])
            if shared:
                overlaps["%s:%s" % (left, right)] = sorted(shared)
    public_or_historical = source_kinds.issubset({
        "public-github-pr", "private-historical-pr",
    }) and bool(source_kinds)
    gates = {
        "minimum_300_cases": len(cases) >= minimum_cases,
        "real_provenance": public_or_historical,
        "repository_isolation": not overlaps,
        "train_present": bool(repositories_by_split.get("train")),
        "validation_present": bool(repositories_by_split.get("validation")),
        "hidden_holdout_present": bool(repositories_by_split.get("holdout")),
    }
    return {
        "ready": all(gates.values()), "gates": gates, "cases": len(cases),
        "repositories": len({str(case.get("repository")) for case in cases}),
        "repositories_by_split": {
            key: len(value) for key, value in repositories_by_split.items()
        },
        "cases_by_split": {
            key: int(cases_by_split.get(key, 0))
            for key in ("train", "validation", "holdout")
        },
        "repository_overlap": overlaps, "source_kinds": sorted(source_kinds),
        "dataset_sha256": dataset_fingerprint(cases),
    }


class _EvaluationTaskStore:
    """Minimal task input provider used by AgenticReviewer during replay."""

    def __init__(self, task_input: dict):
        self.task_input = dict(task_input)

    def get(self, _task_id: str, _tenant_id: Optional[str] = None) -> dict:
        return {"input": dict(self.task_input)}


class SingleModelReviewer(Reviewer):
    """One-call model baseline, optionally merged with the shared 14 rules."""

    def __init__(
        self, arm: str, client: JsonChatClient, total_token_budget: int,
        total_time_budget_seconds: int = 120,
    ):
        if arm not in {"single-llm", "single-llm-scanner"}:
            raise ValueError("single-model reviewer received invalid arm: %s" % arm)
        if total_token_budget < 256:
            raise ValueError("single-model review requires at least 256 tokens")
        if total_time_budget_seconds < 1:
            raise ValueError("total_time_budget_seconds must be positive")
        self.arm = arm
        self.name = arm
        self.client = client
        self.total_token_budget = int(total_token_budget)
        self.total_time_budget_seconds = int(total_time_budget_seconds)
        self.use_scanners = bool(ARM_TOPOLOGY[arm]["deterministic_scanners"])
        self.local = LocalRuleReviewer()
        self.context = ContextRuleReviewer()
        self._last_execution: Dict[str, Any] = {}

    @staticmethod
    def _parse_findings(raw_items, parsed) -> List[Finding]:
        valid = {(item.path, int(item.line)) for item in parsed.added_lines}
        findings = []
        for raw in raw_items or []:
            if not isinstance(raw, dict):
                continue
            try:
                path, line = str(raw.get("path", "")), int(raw.get("line", 0))
            except (TypeError, ValueError):
                continue
            if (path, line) not in valid:
                continue
            try:
                severity = Severity(str(raw.get("severity", "medium")).lower())
            except ValueError:
                severity = Severity.MEDIUM
            try:
                confidence = float(raw.get("confidence", 0.7))
            except (TypeError, ValueError):
                confidence = 0.7
            findings.append(Finding(
                rule_id=str(raw.get("rule_id", "LLM-REVIEW"))[:80],
                cwe=str(raw.get("cwe", "")).strip().upper() or None,
                severity=severity,
                title=str(raw.get("title", "Review finding"))[:200],
                explanation=str(raw.get("explanation", ""))[:4000],
                path=path, line=line,
                evidence=str(raw.get("evidence", ""))[:500],
                fix=str(raw.get("fix", ""))[:4000],
                test=str(raw.get("test", ""))[:4000],
                confidence=max(0.0, min(1.0, confidence)),
                source="single-reviewer",
            ))
        return findings

    @staticmethod
    def _merge(findings: List[Finding]) -> List[Finding]:
        merged = {}
        for finding in findings:
            key = (
                finding.path, int(finding.line),
                canonical_identity(finding.rule_id, finding.cwe),
            )
            current = merged.get(key)
            if current is None or finding.confidence > current.confidence:
                merged[key] = finding
        severity_order = {
            Severity.CRITICAL: 0, Severity.HIGH: 1,
            Severity.MEDIUM: 2, Severity.LOW: 3,
        }
        return sorted(
            merged.values(),
            key=lambda item: (severity_order[item.severity], item.path, item.line),
        )

    def review(self, diff: str, parsed) -> List[Finding]:
        return self.review_case({"diff": diff, "repository": ""}, parsed)

    def review_case(self, case: dict, parsed) -> List[Finding]:
        ledger = ExecutionLedger(self.arm)
        started = time.monotonic()
        payload = {
            "repository": str(case.get("repository", "")),
            "pull_request": case.get("pull_request"),
            "unified_diff": case["diff"],
            "changed_files": list(parsed.files),
        }
        result = self.client.complete_json(
            "single-reviewer", SINGLE_REVIEW_PROMPT,
            json.dumps(payload, ensure_ascii=False), ledger,
            max_tokens=self.total_token_budget,
        )
        if time.monotonic() - started > self.total_time_budget_seconds:
            raise RuntimeError("single-model review exceeded its total time budget")
        findings = self._parse_findings(result.get("findings"), parsed)
        if self.use_scanners:
            findings.extend(self.local.review(case["diff"], parsed))
            findings.extend(self.context.review(case["diff"], parsed))
        self._last_execution = ledger.summary()
        return self._merge(findings)

    def evaluation_execution(self) -> dict:
        return dict(self._last_execution)

    def evaluation_collaboration(self) -> dict:
        return {}

    def evaluation_config(self) -> dict:
        return {
            "arm": self.arm,
            "mode": "single",
            "roles": ["single-reviewer"],
            "deterministic_rules": 14 if self.use_scanners else 0,
            "total_token_budget_per_pr": self.total_token_budget,
            "total_time_budget_seconds_per_pr": self.total_time_budget_seconds,
        }


class ProductArmReviewer:
    """Run one ablation arm through the product AgenticReviewer."""

    def __init__(
        self, arm: str, client: JsonChatClient, total_token_budget: int,
        total_time_budget_seconds: int = 120, evolved_skill_artifact: Optional[dict] = None,
    ):
        if arm not in ARM_TOPOLOGY or ARM_TOPOLOGY[arm]["mode"] != "agentic":
            raise ValueError("unknown evaluation arm: %s" % arm)
        if arm == "full-agentic-evolved-skill" and not evolved_skill_artifact:
            raise ValueError("full-agentic-evolved-skill requires an evolved Skill artifact")
        topology = ARM_TOPOLOGY[arm]
        roles = tuple(topology["roles"])
        llm_role_count = max(1, len(roles))
        if total_token_budget < 256 * llm_role_count:
            raise ValueError(
                "%s requires at least %d total tokens" % (arm, 256 * llm_role_count)
            )
        if total_time_budget_seconds < llm_role_count:
            raise ValueError("total_time_budget_seconds is too small for %s" % arm)
        per_role_tokens = max(256, total_token_budget // llm_role_count)
        per_role_seconds = max(1, total_time_budget_seconds // llm_role_count)
        enabled = set(roles)
        task_input = {
            "mode": topology["mode"],
            "enabled_agents": sorted(enabled),
        }
        self.evolved_skill = (
            AgentSkill.from_artifact(evolved_skill_artifact)
            if evolved_skill_artifact else None
        )
        if self.evolved_skill is not None:
            task_input["enabled_skills"] = [self.evolved_skill.name]
        self.arm = arm
        self.name = arm
        self.client = client
        self.total_token_budget = int(total_token_budget)
        self.total_time_budget_seconds = int(total_time_budget_seconds)
        self.per_role_token_budget = per_role_tokens
        self.per_role_time_budget_seconds = per_role_seconds
        self.expected_roles = roles
        self.store = _EvaluationTaskStore(task_input)
        # LocalRuleReviewer contributes six rules. ContextRuleReviewer contributes
        # the same eight supplemental rules to every arm, for exactly 14 total.
        self.agentic = AgenticReviewer(
            self.store, client,
            default_token_budget=per_role_tokens,
            default_time_budget=per_role_seconds,
            enabled_roles=enabled,
            scanners=[ContextRuleReviewer()],
            skill_provider=(
                (lambda _tenant: [self.evolved_skill])
                if self.evolved_skill is not None else None
            ),
        )
        self._sequence = 0
        self._last_summary: Dict[str, Any] = {}

    def review(self, diff: str, parsed) -> list:
        return self.review_case({"diff": diff, "repository": ""}, parsed)

    def review_case(self, case: dict, parsed) -> list:
        self._sequence += 1
        task_id = "evaluation:%s:%d" % (self.arm, self._sequence)
        repository_root = str(case.get("repository_root") or "")
        findings = self.agentic.review_with_context(
            task_id, case["diff"], parsed,
            repository=repository_root or str(case.get("repository") or ""),
        )
        self._last_summary = self.agentic.collaboration_summary(task_id)
        self._validate_execution()
        return findings

    def _validate_execution(self) -> None:
        execution = self._last_summary.get("execution") or {}
        calls = execution.get("model_call_log") or []
        actual = Counter(
            str(item.get("role")) for item in calls if bool(item.get("ok", True))
        )
        required = set(self.expected_roles)
        if self.arm in {"full-agentic", "full-agentic-evolved-skill"}:
            collaboration = self._last_summary.get("collaboration") or {}
            proposed = int(
                collaboration.get("candidate_findings_before_critic", 0) or 0
            )
            if proposed == 0:
                required.discard("critic")
        missing = sorted(role for role in required if actual[role] < 1)
        if missing:
            raise RuntimeError(
                "%s completed without successful LLM role(s): %s"
                % (self.arm, ", ".join(missing))
            )

    def evaluation_execution(self) -> dict:
        return dict(self._last_summary.get("execution") or {})

    def evaluation_collaboration(self) -> dict:
        return dict(self._last_summary.get("collaboration") or {})

    def evaluation_config(self) -> dict:
        return {
            "arm": self.arm,
            "mode": ARM_TOPOLOGY[self.arm]["mode"],
            "roles": list(self.expected_roles),
            "deterministic_rules": 14,
            "total_token_budget_per_pr": self.total_token_budget,
            "per_role_token_budget": self.per_role_token_budget,
            "total_time_budget_seconds_per_pr": self.total_time_budget_seconds,
            "per_role_time_budget_seconds": self.per_role_time_budget_seconds,
            "skill": self.evolved_skill.name if self.evolved_skill is not None else None,
        }


def product_reviewer_factories(
    client: JsonChatClient, total_time_budget_seconds: int = 120,
) -> Dict[str, Callable[[str, int], ProductArmReviewer]]:
    """Create the two agentic topology arms with one shared model client."""

    def build(arm: str, model: str, token_budget: int) -> ProductArmReviewer:
        if str(client.model) != str(model):
            raise ValueError(
                "evaluation model %s does not match client model %s"
                % (model, client.model)
            )
        return ProductArmReviewer(
            arm, client, token_budget, total_time_budget_seconds,
        )

    return {
        arm: (
            lambda model, budget, selected=arm: build(selected, model, budget)
        )
        for arm in REQUIRED_ARMS
    }


def experiment_reviewer_factories(
    client: JsonChatClient, total_time_budget_seconds: int = 120,
    evolved_skill_artifact: Optional[dict] = None,
) -> Dict[str, Callable[[str, int], Reviewer]]:
    """Build the complete collaboration ablation matrix.

    The evolved-Skill arm is included only when an artifact is supplied, so a
    missing experimental input is visible instead of silently substituting an
    empty or static Skill.
    """

    def build(arm: str, model: str, token_budget: int) -> Reviewer:
        if str(client.model) != str(model):
            raise ValueError(
                "evaluation model %s does not match client model %s"
                % (model, client.model)
            )
        if ARM_TOPOLOGY[arm]["mode"] == "single":
            return SingleModelReviewer(
                arm, client, token_budget, total_time_budget_seconds,
            )
        return ProductArmReviewer(
            arm, client, token_budget, total_time_budget_seconds,
            evolved_skill_artifact=(
                evolved_skill_artifact
                if arm == "full-agentic-evolved-skill" else None
            ),
        )

    selected = [
        arm for arm in EXPERIMENT_ARMS
        if arm != "full-agentic-evolved-skill" or evolved_skill_artifact is not None
    ]
    return {
        arm: (lambda model, budget, selected_arm=arm: build(
            selected_arm, model, budget,
        ))
        for arm in selected
    }


class ProductionEvaluationHarness(EndToEndEvaluationHarness):
    def _run_case(self, reviewer, case):
        class RecordingReviewer:
            def __init__(self, delegate):
                self.delegate = delegate
                self.name = delegate.name
                self.findings = []

            def review(self, diff, parsed):
                self.findings = self.delegate.review(diff, parsed)
                return self.findings

            def review_case(self, case, parsed):
                method = getattr(self.delegate, "review_case", None)
                self.findings = (
                    method(case, parsed)
                    if method else self.delegate.review(case["diff"], parsed)
                )
                return self.findings

        recording = RecordingReviewer(reviewer)
        started = time.monotonic()
        result = super()._run_case(recording, case)
        result.update({
            "invalid_comments": result["fp"],
            "exact_location_hits": 0,
            "evidence_hits": 0,
            "accepted_comments": int(case.get("accepted_comments", 0) or 0),
            "closed_comments": int(case.get("closed_comments", 0) or 0),
            "cost_usd": 0.0,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "model_roles": {},
            "critic_accepted": 0,
            "critic_rejected": 0,
            "revision_requests": 0,
            "revision_results": 0,
        })
        if result["execution_success"]:
            findings = recording.findings
            expected = [
                item for item in case["expected_findings"]
                if bool(item.get("should_comment", True))
            ]
            matches = one_to_one_match(
                expected, findings, self.line_tolerance
            )
            for match in matches:
                finding = findings[match.predicted_index]
                result["exact_location_hits"] += int(match.location_distance == 0)
                result["evidence_hits"] += int(bool(
                    finding.evidence_refs or finding.call_chain or finding.evidence.strip()
                ))
            summary_reader = getattr(reviewer, "evaluation_execution", None)
            if summary_reader:
                execution = summary_reader() or {}
                result["cost_usd"] = float(execution.get("cost_usd", 0) or 0)
                result["latency_ms"] = int(execution.get("duration_ms", result["latency_ms"]))
                result["llm_calls"] = int(execution.get("llm_calls", 0) or 0)
                result["input_tokens"] = int(execution.get("input_tokens", 0) or 0)
                result["output_tokens"] = int(execution.get("output_tokens", 0) or 0)
                result["total_tokens"] = int(execution.get("total_tokens", 0) or 0)
                result["model_roles"] = dict(Counter(
                    str(item.get("role"))
                    for item in execution.get("model_call_log") or []
                ))
            collaboration_reader = getattr(reviewer, "evaluation_collaboration", None)
            if collaboration_reader:
                collaboration = collaboration_reader() or {}
                decisions = (
                    list(collaboration.get("critic_decisions") or [])
                    if "critic" in set(collaboration.get("roles") or []) else []
                )
                result["critic_accepted"] = sum(
                    bool(item.get("accepted")) for item in decisions
                )
                result["critic_rejected"] = sum(
                    not bool(item.get("accepted")) for item in decisions
                )
                result["revision_requests"] = sum(
                    len(item.get("revision_requests") or [])
                    for item in (collaboration.get("lead") or {}).get("assessments") or []
                )
                result["revision_results"] = len(
                    collaboration.get("revision_results") or []
                )
        return result

    @staticmethod
    def _empty_totals():
        values = EndToEndEvaluationHarness._empty_totals()
        values.update({
            "invalid_comments": 0, "exact_location_hits": 0, "evidence_hits": 0,
            "accepted_comments": 0, "closed_comments": 0,
            "latency_ms": 0, "cost_microusd": 0,
            "llm_calls": 0, "input_tokens": 0,
            "output_tokens": 0, "total_tokens": 0,
            "critic_accepted": 0, "critic_rejected": 0,
            "revision_requests": 0, "revision_results": 0,
        })
        return values

    @staticmethod
    def _accumulate(totals, result):
        EndToEndEvaluationHarness._accumulate(totals, result)
        for field in (
            "invalid_comments", "exact_location_hits", "evidence_hits",
            "accepted_comments", "closed_comments", "latency_ms",
            "llm_calls", "input_tokens", "output_tokens", "total_tokens",
            "critic_accepted", "critic_rejected",
            "revision_requests", "revision_results",
        ):
            totals[field] += int(result.get(field, 0))
        totals["cost_microusd"] += int(float(result.get("cost_usd", 0)) * 1_000_000)

    @staticmethod
    def _metrics(totals):
        values = EndToEndEvaluationHarness._metrics(totals)
        cases = totals["cases"] or 1
        tp = totals["tp"] or 1
        commented = totals["accepted_comments"] + totals["closed_comments"]
        values.update({
            "invalid_comments_per_pr": round(totals["invalid_comments"] / cases, 4),
            "exact_line_accuracy": round(totals["exact_location_hits"] / tp, 4),
            "evidence_accuracy": round(totals["evidence_hits"] / tp, 4),
            "comment_acceptance_rate": round(
                totals["accepted_comments"] / commented, 4
            ) if commented else None,
            "average_cost_usd_per_pr": round(
                totals["cost_microusd"] / 1_000_000 / cases, 8
            ),
            "average_latency_ms_per_pr": round(totals["latency_ms"] / cases, 2),
            "average_llm_calls_per_pr": round(totals["llm_calls"] / cases, 4),
            "average_input_tokens_per_pr": round(totals["input_tokens"] / cases, 2),
            "average_output_tokens_per_pr": round(totals["output_tokens"] / cases, 2),
            "average_total_tokens_per_pr": round(totals["total_tokens"] / cases, 2),
            "failure_rate": round(
                1 - totals["execution_successes"] / cases, 4
            ),
            "critic_acceptance_rate": round(
                totals["critic_accepted"]
                / (totals["critic_accepted"] + totals["critic_rejected"]), 4
            ) if totals["critic_accepted"] + totals["critic_rejected"] else None,
            "critic_accepted_per_pr": round(totals["critic_accepted"] / cases, 4),
            "critic_rejected_per_pr": round(totals["critic_rejected"] / cases, 4),
            "revision_requests_per_pr": round(totals["revision_requests"] / cases, 4),
            "revision_results_per_pr": round(totals["revision_results"] / cases, 4),
        })
        return values


DEFAULT_COMPARISON_METRICS = (
    "f1", "precision", "recall", "high_risk_recall",
    "severity_accuracy", "clean_accuracy", "exact_line_accuracy",
    "evidence_accuracy", "invalid_comments_per_pr",
    "average_total_tokens_per_pr", "average_latency_ms_per_pr",
    "average_cost_usd_per_pr", "failure_rate",
)


def paired_bootstrap_comparison(
    left: dict, right: dict, iterations: int = 2000, seed: int = 20260819,
    metrics=DEFAULT_COMPARISON_METRICS,
) -> dict:
    """Paired case bootstrap for two reports evaluated in identical order."""
    left_cases = left["case_results"]
    right_cases = right["case_results"]
    if [item["id"] for item in left_cases] != [item["id"] for item in right_cases]:
        raise ValueError("paired comparison requires identical ordered case ids")
    count = len(left_cases)
    iterations = max(200, int(iterations)) if count else 0
    output = {}
    for metric_index, metric in enumerate(metrics):
        if not count:
            output[metric] = {"delta": 0.0, "ci95": [0.0, 0.0], "iterations": 0}
            continue
        rng = random.Random(int(seed) + metric_index)
        deltas = []
        for _ in range(iterations):
            left_totals = ProductionEvaluationHarness._empty_totals()
            right_totals = ProductionEvaluationHarness._empty_totals()
            for _sample in range(count):
                index = rng.randrange(count)
                ProductionEvaluationHarness._accumulate(left_totals, left_cases[index])
                ProductionEvaluationHarness._accumulate(right_totals, right_cases[index])
            left_value = ProductionEvaluationHarness._metrics(left_totals)[metric]
            right_value = ProductionEvaluationHarness._metrics(right_totals)[metric]
            deltas.append(float(right_value) - float(left_value))
        deltas.sort()
        lower = deltas[int((len(deltas) - 1) * 0.025)]
        upper = deltas[int((len(deltas) - 1) * 0.975)]
        point = float(right["metrics"][metric]) - float(left["metrics"][metric])
        output[metric] = {
            "delta": round(point, 4),
            "ci95": [round(lower, 4), round(upper, 4)],
            "iterations": iterations,
        }
    return output


class FairAblationSuite:
    """Run fair, paired collaboration ablations on an identical case order."""

    def __init__(
        self, reviewer_factories: Mapping[str, Callable[[str, int], Any]],
        model: str, token_budget: int, require_production_ready: bool = True,
        bootstrap_iterations: int = 2000, bootstrap_seed: int = 20260819,
    ):
        missing = set(REQUIRED_ARMS).difference(reviewer_factories)
        if missing:
            raise ValueError("missing ablation arms: %s" % ", ".join(sorted(missing)))
        unknown = set(reviewer_factories).difference(EXPERIMENT_ARMS)
        if unknown:
            raise ValueError("unknown ablation arms: %s" % ", ".join(sorted(unknown)))
        self.factories = reviewer_factories
        self.arm_order = [name for name in EXPERIMENT_ARMS if name in reviewer_factories]
        self.model = model
        self.token_budget = token_budget
        self.require_production_ready = bool(require_production_ready)
        self.bootstrap_iterations = max(200, int(bootstrap_iterations))
        self.bootstrap_seed = int(bootstrap_seed)

    @staticmethod
    def _role_totals(case_results: List[dict]) -> dict:
        totals = Counter()
        for case in case_results:
            totals.update(case.get("model_roles") or {})
        return dict(sorted(totals.items()))

    def _paired_delta(
        self, left: dict, right: dict, metric: str, seed_offset: int,
    ) -> dict:
        left_cases = left["case_results"]
        right_cases = right["case_results"]
        if [item["id"] for item in left_cases] != [item["id"] for item in right_cases]:
            raise ValueError("paired comparison requires identical ordered case ids")
        count = len(left_cases)
        if not count:
            return {"delta": 0.0, "ci95": [0.0, 0.0], "iterations": 0}
        rng = random.Random(self.bootstrap_seed + seed_offset)
        deltas = []
        for _ in range(self.bootstrap_iterations):
            left_totals = ProductionEvaluationHarness._empty_totals()
            right_totals = ProductionEvaluationHarness._empty_totals()
            for _sample in range(count):
                index = rng.randrange(count)
                ProductionEvaluationHarness._accumulate(left_totals, left_cases[index])
                ProductionEvaluationHarness._accumulate(right_totals, right_cases[index])
            left_value = ProductionEvaluationHarness._metrics(left_totals)[metric]
            right_value = ProductionEvaluationHarness._metrics(right_totals)[metric]
            deltas.append(float(right_value) - float(left_value))
        deltas.sort()
        lower = deltas[int((len(deltas) - 1) * 0.025)]
        upper = deltas[int((len(deltas) - 1) * 0.975)]
        point = float(right["metrics"][metric]) - float(left["metrics"][metric])
        return {
            "delta": round(point, 4),
            "ci95": [round(lower, 4), round(upper, 4)],
            "iterations": self.bootstrap_iterations,
        }

    def _comparison(self, left: dict, right: dict, seed_offset: int) -> dict:
        return paired_bootstrap_comparison(
            left, right, self.bootstrap_iterations,
            self.bootstrap_seed + seed_offset,
        )

    @staticmethod
    def _split_view(arm: dict, split: str) -> dict:
        return {
            "metrics": arm["by_split"][split],
            "case_results": [
                item for item in arm["case_results"] if item["split"] == split
            ],
        }

    def run(self, cases: List[dict]) -> Dict[str, Any]:
        readiness = validate_real_dataset(cases)
        if self.require_production_ready and not readiness["ready"]:
            raise ValueError("real PR dataset failed readiness gates: %s" % readiness["gates"])
        harness = ProductionEvaluationHarness()
        arms = {}
        for name in self.arm_order:
            reviewer = self.factories[name](self.model, self.token_budget)
            arms[name] = harness.run(reviewer, cases, name)
            config_reader = getattr(reviewer, "evaluation_config", None)
            arms[name]["fairness"] = {
                "model": self.model, "token_budget_per_pr": self.token_budget,
                "product_runtime": type(getattr(reviewer, "agentic", reviewer)).__name__,
                "configuration": config_reader() if config_reader else {},
            }
            arms[name]["execution"] = {
                "model_role_calls": self._role_totals(arms[name]["case_results"]),
                "average_llm_calls_per_pr": arms[name]["metrics"]["average_llm_calls_per_pr"],
                "average_total_tokens_per_pr": arms[name]["metrics"]["average_total_tokens_per_pr"],
                "average_latency_ms_per_pr": arms[name]["metrics"]["average_latency_ms_per_pr"],
                "average_cost_usd_per_pr": arms[name]["metrics"]["average_cost_usd_per_pr"],
                "critic_acceptance_rate": arms[name]["metrics"]["critic_acceptance_rate"],
                "critic_rejected_per_pr": arms[name]["metrics"]["critic_rejected_per_pr"],
                "revision_requests_per_pr": arms[name]["metrics"]["revision_requests_per_pr"],
                "revision_results_per_pr": arms[name]["metrics"]["revision_results_per_pr"],
            }
        no_critic_holdout = self._split_view(
            arms["multi-llm-no-critic"], "holdout",
        )
        full_holdout = self._split_view(arms["full-agentic"], "holdout")
        candidate = full_holdout["metrics"]
        critic_comparison = self._comparison(
            no_critic_holdout, full_holdout, 200,
        )
        no_critic = no_critic_holdout["metrics"]
        critic_false_positive_non_regression = (
            candidate["invalid_comments_per_pr"]
            <= no_critic["invalid_comments_per_pr"]
        )
        critic_recall_non_regression = candidate["recall"] >= no_critic["recall"] - 0.01
        critic_statistically_positive = (
            critic_comparison["f1"]["ci95"][0] > 0
            or (
                critic_comparison["precision"]["ci95"][0] > 0
                and critic_recall_non_regression
            )
        )
        comparisons = {
            "scope": "hidden-holdout",
            "critic_vs_no_critic": critic_comparison,
        }
        pair_specs = (
            ("scanner_vs_single", "single-llm", "single-llm-scanner", 400),
            (
                "multi_agent_vs_single_scanner", "single-llm-scanner",
                "multi-llm-no-critic", 600,
            ),
            (
                "evolved_skill_vs_full_agentic", "full-agentic",
                "full-agentic-evolved-skill", 800,
            ),
        )
        for label, left_name, right_name, seed_offset in pair_specs:
            if left_name in arms and right_name in arms:
                comparisons[label] = self._comparison(
                    self._split_view(arms[left_name], "holdout"),
                    self._split_view(arms[right_name], "holdout"),
                    seed_offset,
                )
        return {
            "schema_version": 5, "dataset": readiness, "arms": arms,
            "requested_arms": list(self.arm_order),
            "omitted_arms": [name for name in EXPERIMENT_ARMS if name not in arms],
            "comparisons": comparisons,
            "critic_gate": {
                "passed": bool(
                    readiness["ready"] and critic_statistically_positive
                    and critic_false_positive_non_regression
                    and critic_recall_non_regression
                ),
                "statistically_positive": critic_statistically_positive,
                "false_positive_non_regression": critic_false_positive_non_regression,
                "recall_non_regression_with_1pp_tolerance": critic_recall_non_regression,
                "production_dataset_ready": readiness["ready"],
                "decision": (
                    "keep-critic" if (
                        readiness["ready"] and critic_statistically_positive
                        and critic_false_positive_non_regression
                        and critic_recall_non_regression
                    ) else "critic-not-proven"
                ),
            },
            "claim_scope": (
                "Evidence applies to this labelled holdout and model version; "
                "it does not prove universal superiority."
            ),
        }
