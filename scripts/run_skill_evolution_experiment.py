"""Run experiment three with static, evolved and random-feedback Skill arms."""
import argparse
import json
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evoagent.evaluation_experiments import SkillEvolutionExperimentSuite  # noqa: E402
from evoagent.evaluation_experiments import prepare_controlled_experiment_cases  # noqa: E402
from evoagent.evaluation_harness import load_jsonl  # noqa: E402
from evoagent.llm import JsonChatClient  # noqa: E402
from evoagent.skill_evolution import (  # noqa: E402
    AgentSkillReplayReviewer,
    SkillEvolutionEngine,
    validate_artifact,
)


def load_static_artifact(path, skill_name):
    if not path:
        return SkillEvolutionEngine.empty_artifact(skill_name)
    with open(path, "r", encoding="utf-8") as handle:
        if path.lower().endswith(".json"):
            value = json.load(handle)
        else:
            value = {"name": skill_name, "files": {"SKILL.md": handle.read()}}
    return validate_artifact(value, skill_name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Static Skill, feedback-evolved Skill and random-feedback Skill "
            "using train feedback, validation selection and a locked holdout."
        )
    )
    parser.add_argument("dataset", help="Human-labelled public/historical PR JSONL")
    parser.add_argument("--static-skill", default="")
    parser.add_argument("--skill-name", default="evolved-review")
    parser.add_argument("--base-url", default=os.getenv("EVOAGENT_LLM_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.getenv("EVOAGENT_LLM_API_KEY", ""))
    parser.add_argument("--model", default=os.getenv("EVOAGENT_LLM_MODEL", ""))
    parser.add_argument("--provider", default=os.getenv("EVOAGENT_LLM_PROVIDER", "custom"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--token-budget", type=int, default=12000)
    parser.add_argument("--time-budget", type=int, default=240)
    parser.add_argument("--minimum-real-cases", type=int, default=300)
    parser.add_argument("--minimum-validation-f1-improvement", type=float, default=.01)
    parser.add_argument("--maximum-metric-regression", type=float, default=0.0)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    parser.add_argument("--random-seed", type=int, default=20260819)
    parser.add_argument("--allow-non-production-data", action="store_true")
    parser.add_argument(
        "--adapt-controlled-100", action="store_true",
        help="Create 60/20/20 repository-disjoint splits from pr_diff_100.jsonl.",
    )
    parser.add_argument(
        "--output", default=os.path.join(
            ROOT, "output", "skill-evolution-experiment", "evaluation.json",
        ),
    )
    args = parser.parse_args()
    if not args.base_url or not args.api_key or not args.model:
        parser.error("--base-url, --api-key and --model are required")
    if args.token_budget < 1024:
        parser.error("--token-budget must be at least 1024")

    client = JsonChatClient(
        args.base_url, args.api_key, args.model,
        provider=args.provider, timeout=args.timeout,
    )
    per_role_tokens = max(256, args.token_budget // 4)
    per_role_seconds = max(1, args.time_budget // 4)

    def reviewer_factory(artifact):
        return AgentSkillReplayReviewer(
            artifact, client, per_role_tokens, per_role_seconds,
        )

    suite = SkillEvolutionExperimentSuite(
        reviewer_factory,
        minimum_real_cases=args.minimum_real_cases,
        min_validation_f1_improvement=args.minimum_validation_f1_improvement,
        max_metric_regression=args.maximum_metric_regression,
        max_rounds=args.max_rounds,
        repeats=args.repeats,
        bootstrap_iterations=args.bootstrap_iterations,
        random_seed=args.random_seed,
        require_production_ready=not (
            args.allow_non_production_data or args.adapt_controlled_100
        ),
    )
    cases = load_jsonl(args.dataset)
    if args.adapt_controlled_100:
        cases = prepare_controlled_experiment_cases(cases)
    report = suite.run(
        cases,
        load_static_artifact(args.static_skill, args.skill_name),
    )
    output = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    artifact_paths = []
    for index, run in enumerate(report["runs"], 1):
        artifact_path = os.path.join(
            os.path.dirname(output), "evolved-skill-repeat-%d.json" % index,
        )
        with open(artifact_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                run["final_artifacts"]["evolved"], handle,
                ensure_ascii=False, indent=2, sort_keys=True,
            )
            handle.write("\n")
        artifact_paths.append(artifact_path)
    canonical_artifact = os.path.join(os.path.dirname(output), "evolved-skill.json")
    with open(canonical_artifact, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            report["runs"][0]["final_artifacts"]["evolved"], handle,
            ensure_ascii=False, indent=2, sort_keys=True,
        )
        handle.write("\n")
    report["artifact_outputs"] = {
        "preselected_repeat_1": canonical_artifact,
        "all_repeats": artifact_paths,
        "selection_note": (
            "Repeat 1 is preselected for downstream ablation; holdout results are not "
            "used to choose among repeat artifacts."
        ),
    }
    with open(output, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(output)


if __name__ == "__main__":
    main()
