"""Reproducible accuracy, collaboration and Agent Skill evolution experiments."""
import hashlib
import json
import os
import random
import re
import statistics
from typing import Any, Callable, Dict, Iterable, List, Optional

from .diff_parser import parse_unified_diff
from .evaluation_harness import FixtureRepairer, load_jsonl, one_to_one_match
from .evaluation_v2 import (
    ProductionEvaluationHarness,
    paired_bootstrap_comparison,
    validate_real_dataset,
)
from .reviewer import CompositeReviewer, LocalRuleReviewer, Reviewer
from .models import Finding, Severity
from .review_rules import ContextRuleReviewer
from .skill_evolution import SkillEvolutionEngine, validate_artifact


DEFAULT_CONTROLLED_DATASET = os.path.abspath(os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "evaluation_data", "pr_diff_100.jsonl",
))


def load_controlled_pr_cases(path: Optional[str] = None) -> List[dict]:
    """Load the checked-in 100-case corpus; no cases are generated at runtime."""
    return load_jsonl(path or DEFAULT_CONTROLLED_DATASET)


ACCURACY_METRICS = (
    "precision", "recall", "f1", "high_risk_recall", "severity_accuracy",
    "clean_accuracy", "exact_line_accuracy", "evidence_accuracy",
    "invalid_comments_per_pr", "safe_fix_rate", "e2e_security_fix_rate",
    "execution_success_rate", "failure_rate", "average_llm_calls_per_pr",
    "average_total_tokens_per_pr", "average_latency_ms_per_pr",
    "average_cost_usd_per_pr",
)

SKILL_PROTECTED_METRICS = (
    "f1", "precision", "recall", "high_risk_recall", "severity_accuracy",
    "clean_accuracy", "safe_fix_rate", "e2e_security_fix_rate",
    "execution_success_rate",
)


def prepare_controlled_experiment_cases(cases: Iterable[dict]) -> List[dict]:
    """Adapt the checked-in 100-case corpus for three-way experiments.

    Repositories 1-6 become train, 7-8 validation and 9-10 holdout. Human-only
    production gates remain closed because this is an offline fixture corpus.
    """
    cases = [dict(item) for item in cases]
    repositories = sorted({str(item["repository"]) for item in cases})
    if len(cases) != 100 or len(repositories) != 10:
        raise ValueError("controlled experiment adapter requires 100 cases from 10 repositories")
    by_repository = {
        repository: (
            "train" if index < 6 else "validation" if index < 8 else "holdout"
        )
        for index, repository in enumerate(repositories)
    }
    adapted = []
    for original in cases:
        source_kind = str((original.get("source") or {}).get("kind", ""))
        if source_kind != "offline-fixture":
            raise ValueError("controlled experiment adapter accepts only offline-fixture data")
        item = dict(original)
        item["split"] = by_repository[str(item["repository"])]
        item["expected_findings"] = [
            {**dict(finding), "should_comment": bool(finding.get("should_comment", True))}
            for finding in original["expected_findings"]
        ]
        adapted.append(item)
    return adapted


class ControlledSkillReviewer(Reviewer):
    """Deterministic Skill-policy replay for the offline fixture corpus only."""

    name = "controlled-offline-skill-policy"

    def __init__(self, artifact: dict):
        self.artifact = artifact
        content = str((artifact.get("files") or {}).get("SKILL.md", ""))
        self.learned_rules = set(re.findall(
            r"evoagent:learned:([A-Z][A-Z0-9_-]{1,79}):start", content,
        ))
        self.local = LocalRuleReviewer()
        self.catalog = {}
        for case in load_controlled_pr_cases():
            for finding in case.get("expected_findings") or []:
                validation = case.get("repair_validation") or {}
                self.catalog.setdefault(finding["rule_id"], {
                    "rule_id": finding["rule_id"],
                    "severity": finding["severity"],
                    "risk_pattern": validation.get("risk_pattern", ""),
                })

    def review(self, diff: str, parsed) -> List[Finding]:
        findings = list(self.local.review(diff, parsed))
        seen = {(item.rule_id, item.path, item.line) for item in findings}
        for line in parsed.added_lines:
            for rule_id in sorted(self.learned_rules):
                scenario = self.catalog.get(rule_id)
                if not scenario or not re.search(scenario["risk_pattern"], line.content):
                    continue
                identity = (rule_id, line.path, line.line)
                if identity in seen:
                    continue
                seen.add(identity)
                findings.append(Finding(
                    rule_id=rule_id,
                    severity=Severity(str(scenario["severity"])),
                    title="Replay-validated learned Skill rule",
                    explanation=(
                        "The active controlled Skill contains a learned rule matching "
                        "this newly added behavior."
                    ),
                    path=line.path, line=line.line,
                    evidence=line.content.strip()[:240],
                    fix="Apply the safe alternative required by the learned rule.",
                    test="Add a focused regression test for the confirmed failure mode.",
                    confidence=0.86, source="controlled-offline-skill-policy",
                ))
        return findings


class ControlledExperimentClient:
    """Deterministic orchestration client; it is explicitly not an LLM."""

    provider = "controlled-offline"
    model = "no-llm-controlled-v1"

    def complete_json(self, role, _system, user, ledger=None, max_tokens=None):
        if ledger:
            ledger.record_model(
                role, self.provider, self.model,
                {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}, 0,
            )
        if role == "single-reviewer":
            payload = json.loads(user)
            diff = str(payload.get("unified_diff", ""))
            parsed = parse_unified_diff(diff)
            return {
                "findings": [item.to_dict() for item in LocalRuleReviewer().review(diff, parsed)]
            }
        managed = json.loads(user)
        task = json.loads(managed["task"])
        if role == "lead":
            phase = task["phase"]
            if phase == "delegate":
                requested = list(task.get("requested_agent_skills") or [])
                return {"action": "final", "delegations": [
                    {
                        "assignment_id": "%s-1" % worker,
                        "worker": worker,
                        "objective": "Run the controlled %s review." % worker,
                        "files": list(task.get("changed_files") or []),
                        "skills": requested,
                    }
                    for worker in task.get("enabled_workers") or []
                ]}
            if phase == "assess-workers":
                return {
                    "action": "final", "revision_requests": [],
                    "critic_objective": "Reject benchmark-specific benign scanner matches.",
                }
            if phase == "finalize":
                decisions = {
                    int(item["finding_index"]): bool(item.get("accepted"))
                    for item in task.get("critic_decisions") or []
                }
                indices = [
                    index for index, _item in enumerate(task["candidate_findings"])
                    if decisions.get(index, True)
                ]
                return {
                    "action": "final", "accepted_finding_indices": indices,
                    "confidence_adjustments": [],
                }
        if role in {"security", "correctness-reliability"}:
            return {"action": "final", "findings": []}
        if role == "critic":
            decisions = []
            for index, candidate in enumerate(task.get("candidates") or []):
                evidence = str(candidate.get("evidence", ""))
                benign_fixture = (
                    "test-placeholder" in evidence or "fixture-id" in evidence
                )
                decisions.append({
                    "finding_index": index, "accepted": not benign_fixture,
                    "objections": ["controlled benign fixture"] if benign_fixture else [],
                    "confidence_adjustment": 0.0,
                })
            return {"action": "final", "decisions": decisions}
        raise ValueError("unsupported controlled role: %s" % role)


def _artifact_sha256(artifact: dict) -> str:
    rendered = json.dumps(
        artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _metric_view(report: dict) -> dict:
    metrics = report["metrics"]
    return {name: metrics.get(name) for name in ACCURACY_METRICS}


class AccuracyExperimentSuite:
    """Experiment one: controlled Harness validation plus real-PR performance."""

    def __init__(self, minimum_real_cases: int = 300):
        self.minimum_real_cases = int(minimum_real_cases)

    @staticmethod
    def controlled_reviewer() -> Reviewer:
        return CompositeReviewer([LocalRuleReviewer(), ContextRuleReviewer()])

    def run_controlled(self, cases: Optional[List[dict]] = None) -> dict:
        cases = list(cases or load_controlled_pr_cases())
        harness = ProductionEvaluationHarness(repairer=FixtureRepairer())
        report = harness.run(self.controlled_reviewer(), cases, "controlled-rules")
        contract = {
            "exactly_100_cases": len(cases) == 100,
            "forty_risk_cases": sum(bool(item["expected_findings"]) for item in cases) == 40,
            "sixty_clean_cases": sum(not item["expected_findings"] for item in cases) == 60,
            "repository_disjoint_validation_holdout": not (
                {item["repository"] for item in cases if item["split"] == "validation"}
                & {item["repository"] for item in cases if item["split"] == "holdout"}
            ),
            "offline_fixture_provenance": {
                str((item.get("source") or {}).get("kind")) for item in cases
            } == {"offline-fixture"},
        }
        return {
            "dataset_contract": contract,
            "dataset_contract_passed": all(contract.values()),
            "repair_evaluation": "fixture-only",
            "metrics": _metric_view(report),
            "report": report,
        }

    def run_real(
        self, cases: List[dict], reviewer: Reviewer,
        require_production_ready: bool = True, repairer=None,
    ) -> dict:
        readiness = validate_real_dataset(cases, self.minimum_real_cases)
        if require_production_ready and not readiness["ready"]:
            raise ValueError(
                "real PR dataset failed readiness gates: %s" % readiness["gates"]
            )
        report = ProductionEvaluationHarness(repairer=repairer).run(
            reviewer, cases, "real-pr-code-review",
        )
        return {
            "dataset_readiness": readiness,
            "production_claim_allowed": bool(readiness["ready"]),
            "repair_evaluation": "enabled" if repairer is not None else "not-configured",
            "metrics": _metric_view(report),
            "report": report,
        }

    def run(
        self, real_cases: Optional[List[dict]] = None,
        real_reviewer: Optional[Reviewer] = None,
        require_production_ready: bool = True,
    ) -> dict:
        controlled = self.run_controlled()
        real = None
        if real_cases is not None:
            if real_reviewer is None:
                raise ValueError("real_reviewer is required when real_cases are supplied")
            real = self.run_real(
                real_cases, real_reviewer,
                require_production_ready=require_production_ready,
            )
        return {
            "schema_version": 1,
            "experiment": "code-review-accuracy",
            "metric_contract": list(ACCURACY_METRICS),
            "controlled": controlled,
            "real": real,
            "claim_scope": (
                "Controlled results validate the harness. Production performance may be "
                "claimed only when the real dataset readiness gates pass."
            ),
        }


class SkillEvolutionExperimentSuite:
    """Experiment three: Static, evolved and random-feedback Skill controls."""

    def __init__(
        self, reviewer_factory: Callable[[dict], Reviewer],
        minimum_real_cases: int = 300, min_validation_f1_improvement: float = .01,
        max_metric_regression: float = 0.0, max_rounds: int = 3,
        repeats: int = 1, bootstrap_iterations: int = 2000,
        random_seed: int = 20260819, require_production_ready: bool = True,
        repairer=None,
    ):
        self.reviewer_factory = reviewer_factory
        self.minimum_real_cases = int(minimum_real_cases)
        self.min_validation_f1_improvement = float(min_validation_f1_improvement)
        self.max_metric_regression = float(max_metric_regression)
        self.max_rounds = max(1, int(max_rounds))
        self.repeats = max(1, int(repeats))
        self.bootstrap_iterations = max(200, int(bootstrap_iterations))
        self.random_seed = int(random_seed)
        self.require_production_ready = bool(require_production_ready)
        self.repairer = repairer

    @staticmethod
    def _split(cases: Iterable[dict], name: str) -> List[dict]:
        return [item for item in cases if item["split"] == name]

    def _evaluate(self, artifact: dict, cases: List[dict], name: str) -> dict:
        reviewer = self.reviewer_factory(artifact)
        report = ProductionEvaluationHarness(repairer=self.repairer).run(
            reviewer, cases, name,
        )
        report["artifact_sha256"] = _artifact_sha256(artifact)
        return report

    @staticmethod
    def _line_evidence(case: dict, truth: dict) -> str:
        target_path = str(truth["path"]).replace("\\", "/")
        target_line = int(truth["start_line"])
        for changed in parse_unified_diff(case["diff"]).added_lines:
            if changed.path.replace("\\", "/") == target_path and changed.line == target_line:
                return changed.content.strip()[:240]
        return "confirmed defect in %s at line %d" % (target_path, target_line)

    def _collect_feedback(self, artifact: dict, train_cases: List[dict]) -> dict:
        reviewer = self.reviewer_factory(artifact)
        feedback, errors = [], []
        execution_totals = {
            "llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
            "total_tokens": 0, "cost_usd": 0.0, "duration_ms": 0,
        }
        for case in train_cases:
            expected = [
                item for item in case["expected_findings"]
                if bool(item.get("should_comment", True))
            ]
            try:
                parsed = parse_unified_diff(case["diff"])
                review_case = getattr(reviewer, "review_case", None)
                findings = (
                    review_case(case, parsed)
                    if review_case else reviewer.review(case["diff"], parsed)
                )
                execution_reader = getattr(reviewer, "evaluation_execution", None)
                execution = execution_reader() if execution_reader else {}
                for name in (
                    "llm_calls", "input_tokens", "output_tokens",
                    "total_tokens", "duration_ms",
                ):
                    execution_totals[name] += int((execution or {}).get(name, 0) or 0)
                execution_totals["cost_usd"] += float(
                    (execution or {}).get("cost_usd", 0) or 0
                )
                matched = {
                    item.expected_index
                    for item in one_to_one_match(expected, findings)
                }
            except Exception as exc:
                errors.append({"id": case["id"], "error": str(exc)[:1000]})
                continue
            for index, truth in enumerate(expected):
                if index in matched:
                    continue
                feedback.append({
                    "id": "%s:%d" % (case["id"], index),
                    "category": "missed_issue",
                    "payload": {"finding": {
                        "rule_id": str(truth.get("rule_id") or truth["cwe"]),
                        "cwe": str(truth["cwe"]),
                        "severity": str(truth["severity"]),
                        "path": str(truth["path"]),
                        "line": int(truth["start_line"]),
                        "evidence": self._line_evidence(case, truth),
                    }},
                })
        return {
            "feedback": feedback, "errors": errors,
            "cases": len(train_cases), "missed_findings": len(feedback),
            "execution": {
                **execution_totals,
                "cost_usd": round(execution_totals["cost_usd"], 8),
            },
        }

    @staticmethod
    def _random_feedback(count: int, round_number: int, rng: random.Random) -> list:
        values = []
        benign = (
            "validated_value = str(value)",
            "return json.loads(payload)",
            "cursor.execute(query, parameters)",
            "digest = hashlib.sha256(payload).hexdigest()",
        )
        for index in range(count):
            values.append({
                "id": "random:%d:%d" % (round_number, index),
                "category": "missed_issue",
                "payload": {"finding": {
                    "rule_id": "RANDOM-CONTROL-%02d-%04d" % (round_number, index),
                    "severity": "medium", "path": "control.py", "line": 1,
                    "evidence": benign[rng.randrange(len(benign))],
                }},
            })
        return values

    def _validation_gate(self, baseline: dict, candidate: dict) -> dict:
        before, after = baseline["metrics"], candidate["metrics"]
        improvement = after["f1"] >= (
            before["f1"] + self.min_validation_f1_improvement
        )
        regressions = {
            name: round(float(after[name]) - float(before[name]), 4)
            for name in SKILL_PROTECTED_METRICS
            if float(after[name]) + self.max_metric_regression < float(before[name])
        }
        return {
            "passed": bool(improvement and not regressions),
            "f1_improvement": round(after["f1"] - before["f1"], 4),
            "minimum_f1_improvement": self.min_validation_f1_improvement,
            "protected_metric_regressions": regressions,
        }

    def _holdout_gate(self, static: dict, candidate: dict) -> dict:
        before, after = static["metrics"], candidate["metrics"]
        regressions = {
            name: round(float(after[name]) - float(before[name]), 4)
            for name in SKILL_PROTECTED_METRICS
            if float(after[name]) + self.max_metric_regression < float(before[name])
        }
        return {
            "passed": not regressions,
            "protected_metric_regressions": regressions,
            "catastrophic_regression": bool(regressions),
        }

    def _run_once(
        self, cases: List[dict], static_artifact: dict, repeat_index: int,
    ) -> dict:
        train = self._split(cases, "train")
        validation = self._split(cases, "validation")
        holdout = self._split(cases, "holdout")
        skill_name = static_artifact["name"]
        evolved_artifact = static_artifact
        random_artifact = static_artifact
        evolved_validation = self._evaluate(
            evolved_artifact, validation, "evolved-validation-v0",
        )
        random_validation = evolved_validation
        rounds = []
        rng = random.Random(self.random_seed + repeat_index)
        evolved_selected_any = False
        random_selected_any = False

        for round_number in range(1, self.max_rounds + 1):
            collected = self._collect_feedback(evolved_artifact, train)
            mutation = SkillEvolutionEngine.build_candidate_artifact(
                skill_name, evolved_artifact, collected["feedback"],
            )
            learned_count = len(mutation["learned_rule_ids"])
            if not mutation["used_feedback_ids"]:
                rounds.append({
                    "round": round_number, "decision": "deferred",
                    "reason": "no supported missed-issue feedback remained",
                    "training_feedback": collected,
                })
                break

            candidate_validation = self._evaluate(
                mutation["artifact"], validation,
                "evolved-validation-v%d" % round_number,
            )
            evolved_gate = self._validation_gate(
                evolved_validation, candidate_validation,
            )
            if evolved_gate["passed"]:
                evolved_artifact = mutation["artifact"]
                evolved_validation = candidate_validation
                evolved_selected_any = True

            random_feedback = self._random_feedback(
                learned_count, round_number, rng,
            )
            random_mutation = SkillEvolutionEngine.build_candidate_artifact(
                skill_name, random_artifact, random_feedback,
            )
            random_candidate_validation = self._evaluate(
                random_mutation["artifact"], validation,
                "random-validation-v%d" % round_number,
            )
            random_gate = self._validation_gate(
                random_validation, random_candidate_validation,
            )
            if random_gate["passed"]:
                random_artifact = random_mutation["artifact"]
                random_validation = random_candidate_validation
                random_selected_any = True

            rounds.append({
                "round": round_number,
                "training_feedback": collected,
                "evolved": {
                    "decision": "selected" if evolved_gate["passed"] else "rejected",
                    "learned_rule_ids": mutation["learned_rule_ids"],
                    "artifact_sha256": _artifact_sha256(mutation["artifact"]),
                    "validation_gate": evolved_gate,
                    "validation_metrics": _metric_view(candidate_validation),
                },
                "random_control": {
                    "decision": "selected" if random_gate["passed"] else "rejected",
                    "feedback_items": len(random_feedback),
                    "artifact_sha256": _artifact_sha256(random_mutation["artifact"]),
                    "validation_gate": random_gate,
                    "validation_metrics": _metric_view(random_candidate_validation),
                },
            })
            if not evolved_gate["passed"]:
                break

        # Holdout is evaluated only after all validation-based selection is complete.
        static_holdout = self._evaluate(static_artifact, holdout, "static-skill")
        evolved_holdout = self._evaluate(evolved_artifact, holdout, "evolved-skill")
        random_holdout = self._evaluate(random_artifact, holdout, "random-feedback-skill")
        evolved_holdout_gate = self._holdout_gate(static_holdout, evolved_holdout)
        random_holdout_gate = self._holdout_gate(static_holdout, random_holdout)
        evolved_holdout_gate["validation_candidate_selected"] = evolved_selected_any
        evolved_holdout_gate["passed"] = bool(
            evolved_selected_any and evolved_holdout_gate["passed"]
        )
        random_holdout_gate["validation_candidate_selected"] = random_selected_any
        random_holdout_gate["passed"] = bool(
            random_selected_any and random_holdout_gate["passed"]
        )
        return {
            "repeat": repeat_index + 1,
            "rounds": rounds,
            "arms": {
                "static-skill": static_holdout,
                "evolved-skill": evolved_holdout,
                "random-feedback-skill": random_holdout,
            },
            "comparisons": {
                "evolved_vs_static": paired_bootstrap_comparison(
                    static_holdout, evolved_holdout, self.bootstrap_iterations,
                    self.random_seed + repeat_index * 1000,
                ),
                "random_vs_static": paired_bootstrap_comparison(
                    static_holdout, random_holdout, self.bootstrap_iterations,
                    self.random_seed + repeat_index * 1000 + 100,
                ),
                "evolved_vs_random": paired_bootstrap_comparison(
                    random_holdout, evolved_holdout, self.bootstrap_iterations,
                    self.random_seed + repeat_index * 1000 + 200,
                ),
            },
            "release_gate": {
                "evolved_skill": {
                    **evolved_holdout_gate,
                    "decision": (
                        "eligible-for-activation"
                        if evolved_holdout_gate["passed"] else "blocked"
                    ),
                },
                "random_feedback_skill": {
                    **random_holdout_gate,
                    "decision": (
                        "control-passed" if random_holdout_gate["passed"] else "control-blocked"
                    ),
                },
            },
            "final_artifacts": {
                "static": static_artifact,
                "evolved": evolved_artifact,
                "random_feedback": random_artifact,
            },
        }

    @staticmethod
    def _aggregate(runs: List[dict]) -> dict:
        output = {}
        for arm in ("static-skill", "evolved-skill", "random-feedback-skill"):
            output[arm] = {}
            for metric in ACCURACY_METRICS:
                values = [
                    float(run["arms"][arm]["metrics"][metric])
                    for run in runs
                    if run["arms"][arm]["metrics"].get(metric) is not None
                ]
                if not values:
                    output[arm][metric] = None
                    continue
                output[arm][metric] = {
                    "mean": round(statistics.mean(values), 4),
                    "min": round(min(values), 4),
                    "max": round(max(values), 4),
                    "population_stddev": round(statistics.pstdev(values), 4),
                    "runs": len(values),
                }
        return output

    def run(self, cases: List[dict], static_artifact: dict) -> dict:
        readiness = validate_real_dataset(cases, self.minimum_real_cases)
        if self.require_production_ready and not readiness["ready"]:
            raise ValueError(
                "real PR dataset failed readiness gates: %s" % readiness["gates"]
            )
        static_artifact = validate_artifact(
            static_artifact, str(static_artifact.get("name", "")).strip().lower(),
        )
        runs = [
            self._run_once(cases, static_artifact, repeat_index)
            for repeat_index in range(self.repeats)
        ]
        production_claim_allowed = bool(
            readiness["ready"]
            and all(
                run["release_gate"]["evolved_skill"]["passed"]
                for run in runs
            )
        )
        return {
            "schema_version": 1,
            "experiment": "agent-skill-evolution",
            "dataset": readiness,
            "protocol": {
                "feedback_split": "train",
                "selection_split": "validation",
                "final_evaluation_split": "holdout",
                "holdout_access": "after-all-validation-selection",
                "arms": ["static-skill", "evolved-skill", "random-feedback-skill"],
                "max_rounds": self.max_rounds,
                "repeats": self.repeats,
                "random_seed": self.random_seed,
                "minimum_validation_f1_improvement": self.min_validation_f1_improvement,
                "maximum_protected_metric_regression": self.max_metric_regression,
                "repair_evaluation": (
                    "enabled" if self.repairer is not None else "not-configured"
                ),
            },
            "runs": runs,
            "aggregate": self._aggregate(runs),
            "production_claim_allowed": production_claim_allowed,
            "claim_scope": (
                "The evolved Skill is supported only when validation selection succeeds, "
                "holdout protected metrics do not regress, and real-data readiness gates pass."
            ),
        }
