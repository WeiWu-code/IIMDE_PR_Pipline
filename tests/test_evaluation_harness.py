import os
import tempfile
import unittest

from evoagent.evaluation_experiments import load_controlled_pr_cases
from evoagent.evolution import RegressionEvaluator
from evoagent.evaluation_harness import (
    dataset_fingerprint,
    load_jsonl,
    one_to_one_match,
)
from evoagent.models import Finding, Severity


class EndToEndEvaluationTests(unittest.TestCase):
    def test_generated_dataset_has_repository_level_split_and_expected_counts(self):
        cases = load_controlled_pr_cases()
        self.assertEqual(100, len(cases))
        self.assertEqual(40, sum(bool(item["expected_findings"]) for item in cases))
        self.assertEqual(60, sum(not item["expected_findings"] for item in cases))
        validation_repos = {
            item["repository"] for item in cases if item["split"] == "validation"
        }
        holdout_repos = {
            item["repository"] for item in cases if item["split"] == "holdout"
        }
        self.assertEqual(8, len(validation_repos))
        self.assertEqual(2, len(holdout_repos))
        self.assertFalse(validation_repos & holdout_repos)
        self.assertEqual(
            {"offline-fixture"},
            {item["source"]["kind"] for item in cases},
        )

    def test_one_to_one_matching_counts_duplicate_prediction_once(self):
        expected = [{
            "path": "src/a.py", "start_line": 10, "end_line": 12,
            "cwe": "CWE-95", "severity": "critical",
        }]
        predicted = [
            Finding(
                "SEC-EVAL", Severity.CRITICAL, "a", "long enough explanation",
                "src/a.py", line, "eval(x)", "replace eval safely",
                "add malicious input test", 0.9,
            )
            for line in (10, 11)
        ]
        matches = one_to_one_match(expected, predicted)
        self.assertEqual(1, len(matches))

    def test_one_to_one_matching_uses_explicit_cwe_not_random_rule_id(self):
        expected = [{
            "path": "src/a.py", "start_line": 10, "end_line": 10,
            "cwe": "CWE-95", "severity": "critical",
        }]
        predicted = [Finding(
            "MODEL-GENERATED-LABEL", Severity.CRITICAL, "a",
            "long enough explanation", "src/a.py", 10, "eval(x)",
            "replace eval safely", "add malicious input test", 0.9,
            cwe="CWE-95",
        )]
        self.assertEqual(1, len(one_to_one_match(expected, predicted)))

    def test_one_to_one_matching_rejects_unknown_rule_without_cwe(self):
        expected = [{
            "path": "src/a.py", "start_line": 10, "end_line": 10,
            "cwe": "CWE-95", "severity": "critical",
        }]
        predicted = [Finding(
            "MODEL-GENERATED-LABEL", Severity.CRITICAL, "a",
            "long enough explanation", "src/a.py", 10, "eval(x)",
            "replace eval safely", "add malicious input test", 0.9,
        )]
        self.assertEqual([], one_to_one_match(expected, predicted))

    def test_prompt_replay_accepts_model_label_when_cwe_is_canonical(self):
        case = {
            "name": "canonical-cwe",
            "diff": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(data)\n",
            "expected": [{
                "path": "a.py", "line": 1, "rule_id": "SEC-EVAL",
                "min_severity": "high",
            }],
        }

        class Reviewer:
            name = "test"

            def review(self, _diff, parsed):
                line = parsed.added_lines[0]
                return [Finding(
                    "MODEL-LABEL", Severity.CRITICAL, "x", "x",
                    line.path, line.line, line.content, "fix", "test", 0.9,
                    cwe="CWE-95",
                )]

        report = RegressionEvaluator(lambda _prompt: Reviewer()).run("p", [case])
        self.assertEqual(1, report["case_results"][0]["tp"])

    def test_dataset_round_trip_has_stable_fingerprint(self):
        cases = load_controlled_pr_cases()
        handle, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(handle)
        try:
            import json
            with open(path, "w", encoding="utf-8") as output:
                for case in cases:
                    output.write(json.dumps(case, ensure_ascii=False) + "\n")
            loaded = load_jsonl(path)
            self.assertEqual(dataset_fingerprint(cases), dataset_fingerprint(loaded))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
