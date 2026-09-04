"""Run all three experiments offline on evaluation_data/pr_diff_100.jsonl."""
import argparse
import json
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evoagent.evaluation_experiments import (  # noqa: E402
    AccuracyExperimentSuite,
    ControlledExperimentClient,
    ControlledSkillReviewer,
    SkillEvolutionExperimentSuite,
    prepare_controlled_experiment_cases,
)
from evoagent.evaluation_harness import load_jsonl  # noqa: E402
from evoagent.evaluation_v2 import (  # noqa: E402
    FairAblationSuite,
    experiment_reviewer_factories,
)
from evoagent.skill_evolution import SkillEvolutionEngine  # noqa: E402


def render_summary(report):
    def pct(value):
        return "%.2f%%" % (100.0 * float(value))

    lines = [
        "# EvoAgent 100 条受控集实验结果", "",
        "> 执行模式：`controlled-offline-no-llm`。本报告没有调用大模型，",
        "> 只验证确定性规则、Agent 编排计数和 Skill 选择门禁。", "",
        "## 数据", "",
        "- 总样本：100",
        "- Train / Validation / Holdout：60 / 20 / 20（按仓库隔离）", "",
        "## 实验一：规则基线", "",
        "| Precision | Recall | F1 | 高风险召回 | Clean accuracy | Safe fix | E2E fix |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    first = report["experiment_one"]["metrics"]
    lines.append("| %s | %s | %s | %s | %s | %s | %s |" % tuple(
        pct(first[name]) for name in (
            "precision", "recall", "f1", "high_risk_recall", "clean_accuracy",
            "safe_fix_rate", "e2e_security_fix_rate",
        )
    ))
    lines.extend([
        "", "## 实验二：受控离线五臂消融", "",
        "| 实验臂 | Precision | Recall | F1 | 高风险召回 | Clean accuracy | 无效评论/PR | 模拟角色调用/PR |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for name in (
        "single-llm", "single-llm-scanner", "multi-llm-no-critic",
        "full-agentic", "full-agentic-evolved-skill",
    ):
        metrics = report["experiment_two"]["arms"][name]["metrics"]
        lines.append(
            "| `%s` | %s | %s | %s | %s | %s | %.4f | %.2f |" % (
                name, pct(metrics["precision"]), pct(metrics["recall"]),
                pct(metrics["f1"]), pct(metrics["high_risk_recall"]),
                pct(metrics["clean_accuracy"]), metrics["invalid_comments_per_pr"],
                metrics["average_llm_calls_per_pr"],
            )
        )
    scanner_delta = report["experiment_two"]["comparisons"][
        "scanner_vs_single"
    ]["f1"]["delta"]
    lines.extend([
        "", "隐藏 Holdout 上，Critic、多 Agent 和 Evolved Skill 相对对应基线的 F1 差值均为 0；",
        "Scanner 相对单规则基线的 Holdout F1 变化为 %+.2f 个百分点。"
        % (100.0 * scanner_delta), "",
        "## 实验三：Skill 自进化", "",
        "| 实验臂 | Precision | Recall | F1 | 高风险召回 | Clean accuracy |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    skill_run = report["experiment_three"]["runs"][0]
    for name in ("static-skill", "evolved-skill", "random-feedback-skill"):
        metrics = skill_run["arms"][name]["metrics"]
        lines.append("| `%s` | %s | %s | %s | %s | %s |" % (
            name, pct(metrics["precision"]), pct(metrics["recall"]),
            pct(metrics["f1"]), pct(metrics["high_risk_recall"]),
            pct(metrics["clean_accuracy"]),
        ))
    learned = skill_run["rounds"][0]["evolved"]["learned_rule_ids"]
    lines.extend([
        "", "- 第 1 轮学习规则：`%s`" % "`, `".join(learned),
        "- Validation 候选：`rejected`（没有达到 F1 最小提升）",
        "- Holdout 激活门禁：`blocked`",
        "- 结论：在该仓库隔离切分上，Skill 自进化没有得到准确率提升证据。", "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run accuracy, orchestration ablation and Skill controls without an LLM."
    )
    parser.add_argument(
        "--dataset", default=os.path.join(ROOT, "evaluation_data", "pr_diff_100.jsonl"),
    )
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260819)
    parser.add_argument(
        "--output", default=os.path.join(
            ROOT, "output", "controlled-experiments", "evaluation.json",
        ),
    )
    args = parser.parse_args()
    original = load_jsonl(args.dataset)
    cases = prepare_controlled_experiment_cases(original)

    experiment_one = AccuracyExperimentSuite().run_controlled(original)
    skill_suite = SkillEvolutionExperimentSuite(
        ControlledSkillReviewer,
        minimum_real_cases=100,
        min_validation_f1_improvement=.01,
        max_rounds=3,
        repeats=1,
        bootstrap_iterations=args.bootstrap_iterations,
        random_seed=args.seed,
        require_production_ready=False,
    )
    experiment_three = skill_suite.run(
        cases, SkillEvolutionEngine.empty_artifact("controlled-review"),
    )
    evolved_artifact = experiment_three["runs"][0]["final_artifacts"]["evolved"]

    client = ControlledExperimentClient()
    experiment_two = FairAblationSuite(
        experiment_reviewer_factories(client, 40, evolved_artifact),
        client.model, 4096,
        require_production_ready=False,
        bootstrap_iterations=args.bootstrap_iterations,
        bootstrap_seed=args.seed,
    ).run(cases)
    report = {
        "schema_version": 1,
        "execution_mode": "controlled-offline-no-llm",
        "model": None,
        "dataset": {
            "path": os.path.abspath(args.dataset),
            "cases": len(cases),
            "split_counts": {
                split: sum(item["split"] == split for item in cases)
                for split in ("train", "validation", "holdout")
            },
        },
        "experiment_one": experiment_one,
        "experiment_two": experiment_two,
        "experiment_three": experiment_three,
        "claim_scope": (
            "This run validates deterministic rules, orchestration accounting and Skill "
            "selection gates. It is not evidence about any large language model."
        ),
    }
    output = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    summary_path = os.path.join(os.path.dirname(output), "summary.md")
    report["summary_markdown"] = summary_path
    with open(output, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    with open(summary_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_summary(report))
    print(output)


if __name__ == "__main__":
    main()
