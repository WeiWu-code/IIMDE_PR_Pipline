import json
import unittest

from evoagent.diff_parser import parse_unified_diff
from evoagent.evaluation_experiments import (
    AccuracyExperimentSuite,
    load_controlled_pr_cases,
    SkillEvolutionExperimentSuite,
    prepare_controlled_experiment_cases,
)
from evoagent.evaluation_v2 import (
    FairAblationSuite,
    experiment_reviewer_factories,
)
from evoagent.models import Finding, Severity
from evoagent.reviewer import Reviewer
from evoagent.skill_evolution import SkillEvolutionEngine


DIFF = (
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -0,0 +1 @@\n"
    "+value = open(base / user_path)\n"
)


class ExperimentClient:
    provider = "fake"
    model = "fake-model"

    def complete_json(self, role, _system, user, ledger=None, max_tokens=None):
        if ledger:
            ledger.record_model(
                role, self.provider, self.model,
                {"prompt_tokens": 10, "completion_tokens": 5}, 1,
            )
        if role == "single-reviewer":
            return {"findings": []}
        if role == "lead":
            managed = json.loads(user)
            task = json.loads(managed["task"])
            if task["phase"] == "delegate":
                return {"action": "final", "delegations": [
                    {
                        "assignment_id": "security-1", "worker": "security",
                        "objective": "Review security",
                    },
                    {
                        "assignment_id": "reliability-1",
                        "worker": "correctness-reliability",
                        "objective": "Review reliability",
                    },
                ]}
            if task["phase"] == "assess-workers":
                return {
                    "action": "final", "revision_requests": [],
                    "critic_objective": "Verify candidates",
                }
            if task["phase"] == "finalize":
                return {
                    "action": "final",
                    "accepted_finding_indices": list(
                        range(len(task["candidate_findings"]))
                    ),
                    "confidence_adjustments": [],
                }
        if role in {"security", "correctness-reliability"}:
            return {"action": "final", "findings": []}
        if role == "critic":
            managed = json.loads(user)
            task = json.loads(managed["task"])
            return {"action": "final", "decisions": [
                {
                    "finding_index": index, "accepted": True,
                    "objections": [], "confidence_adjustment": 0.0,
                }
                for index, _item in enumerate(task["candidates"])
            ]}
        raise AssertionError(role)


class ArtifactAwareReviewer(Reviewer):
    name = "artifact-aware"

    def __init__(self, artifact):
        self.enabled = "evoagent:learned:LEARN-MISSING:start" in (
            artifact["files"]["SKILL.md"]
        )

    def review(self, diff, parsed):
        if not self.enabled:
            return []
        line = parsed.added_lines[0]
        return [Finding(
            "LEARN-MISSING", Severity.HIGH, "Learned issue",
            "The learned project rule identifies this defect.",
            line.path, line.line, line.content.strip(),
            "Use a safe API.", "Add a regression test.", 0.9,
        )]


def labelled_case(identifier, split, rule_id="SEC-PATH-TRAVERSAL"):
    return {
        "schema_version": 1,
        "id": identifier,
        "repository": "%s/%s" % (split, identifier),
        "pull_request": 1,
        "split": split,
        "source": {"kind": "public-github-pr"},
        "diff": DIFF,
        "expected_findings": [{
            "path": "app.py", "start_line": 1, "end_line": 1,
            "rule_id": rule_id, "cwe": rule_id, "severity": "high",
            "should_comment": True,
        }],
    }


class EvaluationExperimentTests(unittest.TestCase):
    def test_controlled_adapter_creates_repository_disjoint_60_20_20_splits(self):
        cases = prepare_controlled_experiment_cases(load_controlled_pr_cases())
        self.assertEqual(60, sum(item["split"] == "train" for item in cases))
        self.assertEqual(20, sum(item["split"] == "validation" for item in cases))
        self.assertEqual(20, sum(item["split"] == "holdout" for item in cases))
        repositories = {
            split: {item["repository"] for item in cases if item["split"] == split}
            for split in ("train", "validation", "holdout")
        }
        self.assertFalse(repositories["train"] & repositories["validation"])
        self.assertFalse(repositories["train"] & repositories["holdout"])
        self.assertFalse(repositories["validation"] & repositories["holdout"])
        self.assertTrue(all(
            "should_comment" in finding
            for item in cases for finding in item["expected_findings"]
        ))

    def test_controlled_accuracy_reports_full_metric_contract(self):
        report = AccuracyExperimentSuite().run()
        self.assertTrue(report["controlled"]["dataset_contract_passed"])
        metrics = report["controlled"]["metrics"]
        for name in (
            "precision", "recall", "f1", "high_risk_recall",
            "severity_accuracy", "clean_accuracy", "exact_line_accuracy",
            "evidence_accuracy", "invalid_comments_per_pr", "safe_fix_rate",
            "e2e_security_fix_rate",
        ):
            self.assertIn(name, metrics)

    def test_five_arm_ablation_reports_collaboration_and_cost(self):
        artifact = SkillEvolutionEngine.empty_artifact("evolved-review")
        factories = experiment_reviewer_factories(
            ExperimentClient(), 40, artifact,
        )
        cases = [
            labelled_case("train", "train"),
            labelled_case("validation", "validation"),
            labelled_case("holdout", "holdout"),
        ]
        report = FairAblationSuite(
            factories, "fake-model", 4096,
            require_production_ready=False, bootstrap_iterations=200,
        ).run(cases)
        self.assertEqual(5, len(report["arms"]))
        self.assertIn("multi_agent_vs_single_scanner", report["comparisons"])
        self.assertIn("evolved_skill_vs_full_agentic", report["comparisons"])
        execution = report["arms"]["full-agentic"]["execution"]
        self.assertIn("critic_acceptance_rate", execution)
        self.assertIn("revision_requests_per_pr", execution)
        self.assertIn("average_cost_usd_per_pr", execution)

    def test_skill_evolution_uses_train_validation_and_locked_holdout(self):
        cases = [
            labelled_case("train", "train", "LEARN-MISSING"),
            labelled_case("validation", "validation", "LEARN-MISSING"),
            labelled_case("holdout", "holdout", "LEARN-MISSING"),
        ]
        suite = SkillEvolutionExperimentSuite(
            ArtifactAwareReviewer, minimum_real_cases=3,
            max_rounds=2, repeats=1, bootstrap_iterations=200,
        )
        report = suite.run(
            cases, SkillEvolutionEngine.empty_artifact("evolved-review"),
        )
        run = report["runs"][0]
        self.assertEqual("train", report["protocol"]["feedback_split"])
        self.assertEqual("validation", report["protocol"]["selection_split"])
        self.assertEqual("holdout", report["protocol"]["final_evaluation_split"])
        self.assertEqual(0.0, run["arms"]["static-skill"]["metrics"]["f1"])
        self.assertEqual(1.0, run["arms"]["evolved-skill"]["metrics"]["f1"])
        self.assertEqual(0.0, run["arms"]["random-feedback-skill"]["metrics"]["f1"])
        self.assertEqual(
            "eligible-for-activation",
            run["release_gate"]["evolved_skill"]["decision"],
        )
        self.assertTrue(report["production_claim_allowed"])


if __name__ == "__main__":
    unittest.main()
