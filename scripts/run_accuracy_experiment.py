"""Run experiment one: controlled Harness validation and optional real-PR accuracy."""
import argparse
import json
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evoagent.evaluation_experiments import AccuracyExperimentSuite  # noqa: E402
from evoagent.evaluation_harness import load_jsonl  # noqa: E402
from evoagent.evaluation_v2 import ProductArmReviewer  # noqa: E402
from evoagent.llm import JsonChatClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate code-review accuracy on the 100-case controlled set and real PRs."
    )
    parser.add_argument("--real-dataset", default="")
    parser.add_argument("--minimum-real-cases", type=int, default=300)
    parser.add_argument("--base-url", default=os.getenv("EVOAGENT_LLM_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.getenv("EVOAGENT_LLM_API_KEY", ""))
    parser.add_argument("--model", default=os.getenv("EVOAGENT_LLM_MODEL", ""))
    parser.add_argument("--provider", default=os.getenv("EVOAGENT_LLM_PROVIDER", "custom"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--token-budget", type=int, default=12000)
    parser.add_argument("--time-budget", type=int, default=240)
    parser.add_argument("--allow-non-production-data", action="store_true")
    parser.add_argument(
        "--output", default=os.path.join(
            ROOT, "output", "accuracy-experiment", "evaluation.json",
        ),
    )
    args = parser.parse_args()

    real_cases = real_reviewer = None
    if args.real_dataset:
        if not args.base_url or not args.api_key or not args.model:
            parser.error(
                "--base-url, --api-key and --model are required with --real-dataset"
            )
        client = JsonChatClient(
            args.base_url, args.api_key, args.model,
            provider=args.provider, timeout=args.timeout,
        )
        real_reviewer = ProductArmReviewer(
            "full-agentic", client, args.token_budget, args.time_budget,
        )
        real_cases = load_jsonl(args.real_dataset)

    report = AccuracyExperimentSuite(args.minimum_real_cases).run(
        real_cases=real_cases, real_reviewer=real_reviewer,
        require_production_ready=not args.allow_non_production_data,
    )
    output = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(output)


if __name__ == "__main__":
    main()
