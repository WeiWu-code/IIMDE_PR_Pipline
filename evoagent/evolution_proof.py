"""Auditable offline proof for feedback-driven prompt evolution.

This module deliberately uses a deterministic prompt-policy reviewer.  It proves
that EvoAgent's feedback -> prompt version -> replay -> holdout -> activation
loop changes agent behavior under controlled conditions. It does not claim that
an unconfigured external LLM improved, and reports produced here are marked as
offline fixtures.
"""

import hashlib
import json
import os
import re
from typing import Any, Dict, Iterable, List, Set

from .diff_parser import parse_unified_diff
from .evaluation_harness import RULE_TO_CWE, dataset_fingerprint, load_jsonl, one_to_one_match
from .evolution import DEFAULT_PROMPT, EvolutionEngine, RegressionEvaluator
from .models import Finding
from .reviewer import LocalRuleReviewer, Reviewer
from .review_rules import ContextRuleReviewer
from .store import TaskStore, utc_now


FOCUS_RULE = re.compile(r"\[focus-rule:([A-Z][A-Z0-9_-]{1,79})\]")


DEFAULT_PROMPT_DATASET = os.path.abspath(os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "evaluation_data", "prompt_evolution_130.jsonl",
))


def load_prompt_evolution_cases(dataset_path: str = DEFAULT_PROMPT_DATASET) -> List[dict]:
    """Load the checked-in 130-case prompt replay corpus."""
    return load_jsonl(dataset_path)


def generate_prompt_evolution_cases() -> List[dict]:
    """Backward-compatible name for loading the pre-generated corpus."""
    return load_prompt_evolution_cases()


def write_jsonl(cases: Iterable[dict], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")


class PromptPolicyReviewer(Reviewer):
    """One reviewer whose specialist focus is selected only by its prompt version."""

    name = "prompt-policy-reviewer"

    def __init__(self, prompt: str):
        self.prompt = prompt
        self.focus_rules: Set[str] = set(FOCUS_RULE.findall(prompt))
        self.local = LocalRuleReviewer()
        self.context = ContextRuleReviewer()

    def review(self, diff: str, parsed) -> List[Finding]:
        findings = list(self.local.review(diff, parsed))
        findings.extend(
            finding for finding in self.context.review(diff, parsed)
            if finding.rule_id in self.focus_rules
        )
        deduplicated = {}
        for finding in findings:
            key = (finding.rule_id, finding.path, int(finding.line))
            deduplicated[key] = finding
        return list(deduplicated.values())


def _evolution_case(case: dict) -> dict:
    expected = [
        {
            "path": item["path"],
            "line": int(item["start_line"]),
            "rule_id": item.get("rule_id", item["cwe"]),
            "min_severity": item["severity"],
        }
        for item in case["expected_findings"]
    ]
    return {
        "name": "proof-%s" % case["id"],
        "split": case["split"],
        "diff": case["diff"],
        "expected": expected,
        "source": (case.get("source") or {}).get("kind", "unknown"),
    }


def _prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _collect_feedback(store: TaskStore, cases: Iterable[dict], prompt: str) -> List[dict]:
    reviewer = PromptPolicyReviewer(prompt)
    feedback = []
    for case in cases:
        if case["split"] != "validation":
            continue
        parsed = parse_unified_diff(case["diff"])
        findings = reviewer.review(case["diff"], parsed)
        matches = one_to_one_match(case["expected_findings"], findings)
        matched = {item.expected_index for item in matches}
        for index, expected in enumerate(case["expected_findings"]):
            if index in matched:
                continue
            task_id = "feedback-%s-%s" % (case["id"], index)
            store.create(
                task_id, case["repository"], case.get("pull_request"),
                {"source": "prompt-evolution-proof", "case_id": case["id"]},
            )
            finding = {
                "rule_id": expected.get("rule_id", expected["cwe"]),
                "cwe": expected["cwe"],
                "path": expected["path"],
                "line": expected["start_line"],
                "severity": expected["severity"],
            }
            store.record_failure_case(
                task_id, "missed_issue",
                {"finding": finding, "note": "Confirmed validation false negative"},
            )
            feedback.append({"task_id": task_id, "finding": finding})
    return feedback


def _metric_delta(candidate: dict, baseline: dict) -> dict:
    names = (
        "score", "precision", "recall", "f1", "severity_accuracy",
        "high_severity_recall", "clean_accuracy", "success_rate",
    )
    return {
        name: round(float(candidate[name]) - float(baseline[name]), 4)
        for name in names
    }


def run_prompt_evolution_proof(dataset_path: str, database_path: str) -> Dict[str, Any]:
    """Run a fresh, reproducible prompt-version evolution experiment."""
    cases = load_jsonl(dataset_path)
    source_kinds = sorted({
        str((case.get("source") or {}).get("kind", "unknown")) for case in cases
    })
    store = TaskStore(database_path)
    converted = [_evolution_case(case) for case in cases]
    for case in converted:
        store.save_evaluation_case(
            case["name"], case["split"], case["diff"], case["expected"],
            case["source"], True,
        )

    baseline_validation = RegressionEvaluator(PromptPolicyReviewer).run(
        DEFAULT_PROMPT,
        store.list_evaluation_cases("validation", True, len(cases)),
    )
    store.save_skill_version(
        "llm-review", DEFAULT_PROMPT, baseline_validation["score"], activate=True
    )
    feedback = _collect_feedback(store, cases, DEFAULT_PROMPT)
    engine = EvolutionEngine(
        store,
        reviewer_factory=PromptPolicyReviewer,
        min_cases=sum(case["split"] == "validation" for case in cases),
        max_cases=len(cases),
        min_improvement=0.02,
        min_holdout_cases=sum(case["split"] == "holdout" for case in cases),
        # Precision may trade slightly for recall, but the allowed tolerance is
        # explicit and still protected by the score/F1/holdout gates.
        max_metric_regression=0.02,
        seed_defaults=False,
    )
    result = engine.auto_propose("llm-review")
    active = store.get_active_skill_version("llm-review")
    versions = list(reversed(store.list_skill_versions("llm-review")))
    runs = store.list_evolution_runs()
    candidate_prompt = active["prompt"] if active else DEFAULT_PROMPT

    all_cases = store.list_evaluation_cases(None, True, len(cases))
    evaluator = RegressionEvaluator(PromptPolicyReviewer)
    baseline_all = evaluator.run(DEFAULT_PROMPT, all_cases)
    candidate_all = evaluator.run(candidate_prompt, all_cases)
    provenance_passed = source_kinds == ["public-github-pr"]
    quantitative_passed = result.get("decision") == "activated"
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "claim_scope": {
            "level": "controlled-offline-behavioral-evolution",
            "proves": (
                "Feedback automatically produced a new prompt version; the same reviewer "
                "changed behavior and passed validation improvement plus holdout non-regression."
            ),
            "does_not_prove": (
                "External LLM weight improvement or production performance on real GitHub PRs."
            ),
        },
        "dataset": {
            "path": os.path.abspath(dataset_path),
            "cases": len(cases),
            "validation_cases": sum(case["split"] == "validation" for case in cases),
            "holdout_cases": sum(case["split"] == "holdout" for case in cases),
            "repositories": len({case["repository"] for case in cases}),
            "source_kinds": source_kinds,
            "sha256": dataset_fingerprint(cases),
            "validation_sha256": result["gates"] and runs[0]["metrics"]["reproducibility"][
                "validation_dataset_sha256"
            ],
            "holdout_sha256": result["gates"] and runs[0]["metrics"]["reproducibility"][
                "holdout_dataset_sha256"
            ],
        },
        "feedback": {
            "missed_findings": len(feedback),
            "learned_rule_ids": result.get("learned_rule_ids", []),
            "resolved_after_activation": sum(
                bool(case.get("resolved")) for case in store.list_failure_cases(False, 500)
            ),
        },
        "versions": [
            {
                "version": item["version"],
                "active": bool(item["active"]),
                "parent_version": item.get("parent_version"),
                "score": item["score"],
                "prompt_sha256": _prompt_sha256(item["prompt"]),
            }
            for item in versions
        ],
        "evolution_run": {
            "run_id": result.get("run_id"),
            "decision": result.get("decision"),
            "reason": result.get("reason"),
            "failure_cases_used": result.get("failure_cases_used"),
            "gates": result.get("gates"),
        },
        "validation": {
            "baseline": result["baseline"],
            "candidate": result["candidate"],
            "delta": _metric_delta(result["candidate"], result["baseline"]),
        },
        "holdout": {
            "baseline": result["baseline_holdout"],
            "candidate": result["candidate_holdout"],
            "delta": _metric_delta(result["candidate_holdout"], result["baseline_holdout"]),
        },
        "all_cases": {
            "baseline": baseline_all,
            "candidate": candidate_all,
            "delta": _metric_delta(candidate_all, baseline_all),
        },
        "release_gate": {
            "quantitative_passed": quantitative_passed,
            "production_data_provenance_passed": provenance_passed,
            "production_activation_allowed": quantitative_passed and provenance_passed,
        },
    }


def render_markdown(report: Dict[str, Any]) -> str:
    def pct(value: float) -> str:
        return "%.2f%%" % (100.0 * float(value))

    def row(label: str, key: str, block: dict) -> str:
        return "| %s | %s | %s | %+.2f pp |" % (
            label, pct(block["baseline"][key]), pct(block["candidate"][key]),
            100.0 * block["delta"][key],
        )

    lines = [
        "# EvoAgent 提示词版本进化回放证明",
        "",
        "## 结论",
        "",
        "- 进化决策：`%s`" % report["evolution_run"]["decision"],
        "- 数值门禁：`%s`" % (
            "PASS" if report["release_gate"]["quantitative_passed"] else "FAIL"
        ),
        "- 生产来源门禁：`%s`" % (
            "PASS" if report["release_gate"]["production_data_provenance_passed"] else "FAIL"
        ),
        "- 生产激活：`%s`" % (
            "ALLOWED" if report["release_gate"]["production_activation_allowed"] else "BLOCKED"
        ),
        "",
        "> 本报告证明受控离线环境中的行为版本进化，不证明外部 LLM 权重提升，"
        "也不代表真实 GitHub PR 的生产效果。",
        "",
        "## 数据与反馈",
        "",
        "- 样本：%s（Validation %s / Holdout %s）" % (
            report["dataset"]["cases"], report["dataset"]["validation_cases"],
            report["dataset"]["holdout_cases"],
        ),
        "- 仓库：%s" % report["dataset"]["repositories"],
        "- 来源：`%s`" % ", ".join(report["dataset"]["source_kinds"]),
        "- Validation 漏报反馈：%s" % report["feedback"]["missed_findings"],
        "- 自动学习规则：`%s`" % "`, `".join(report["feedback"]["learned_rule_ids"]),
        "",
        "## Validation 回放",
        "",
        "| 指标 | Prompt v1 | Prompt v2 | 变化 |",
        "|---|---:|---:|---:|",
        row("Precision", "precision", report["validation"]),
        row("Recall", "recall", report["validation"]),
        row("F1", "f1", report["validation"]),
        row("高风险召回率", "high_severity_recall", report["validation"]),
        row("干净样本准确率", "clean_accuracy", report["validation"]),
        row("综合得分", "score", report["validation"]),
        "",
        "## Holdout 回放",
        "",
        "| 指标 | Prompt v1 | Prompt v2 | 变化 |",
        "|---|---:|---:|---:|",
        row("Precision", "precision", report["holdout"]),
        row("Recall", "recall", report["holdout"]),
        row("F1", "f1", report["holdout"]),
        row("高风险召回率", "high_severity_recall", report["holdout"]),
        row("干净样本准确率", "clean_accuracy", report["holdout"]),
        row("综合得分", "score", report["holdout"]),
        "",
        "## 审计证据",
        "",
        "- Evolution run：`%s`" % report["evolution_run"]["run_id"],
        "- 原因：%s" % report["evolution_run"]["reason"],
    ]
    for version in report["versions"]:
        lines.append(
            "- Prompt v%s：active=`%s`，parent=`%s`，SHA-256=`%s`" % (
                version["version"], str(version["active"]).lower(),
                version["parent_version"], version["prompt_sha256"],
            )
        )
    lines.extend([
        "- 完整数据集指纹：`%s`" % report["dataset"]["sha256"],
        "- Validation 数据指纹：`%s`" % report["dataset"]["validation_sha256"],
        "- Holdout 数据指纹：`%s`" % report["dataset"]["holdout_sha256"],
        "",
    ])
    return "\n".join(lines)


def write_report(report: Dict[str, Any], output_dir: str) -> Dict[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, "prompt-evolution-proof.json")
    markdown_path = os.path.join(output_dir, "prompt-evolution-proof.md")
    with open(json_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    with open(markdown_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_markdown(report))
    return {"json": json_path, "markdown": markdown_path}
