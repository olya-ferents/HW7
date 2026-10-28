import os
import unittest
from unittest.mock import patch

import console
import loader
import runner


def result(tokens, latency=1000):
    return {
        "usage": {"input_tokens": tokens - 100, "output_tokens": 100},
        "latency_ms": latency,
        "answer": "Done",
        "trace": {"children": []},
    }


class BudgetTests(unittest.TestCase):
    def test_tokens_use_median_of_dialog_totals(self):
        results = [result(n) for n in [61098, 59817, 57315, 60306, 59329]]
        verdict = runner._budget({"budget": {"tokens_total": 68000}}, results)
        self.assertTrue(verdict.passed)
        self.assertIn("median tokens 59817", verdict.detail)

    def test_defect_median_exceeds_budget(self):
        results = [result(n) for n in [72824, 80647, 85262, 70506, 81170]]
        verdict = runner._budget({"budget": {"tokens_total": 68000}}, results)
        self.assertFalse(verdict.passed)
        self.assertIn("median tokens 80647 > 68000", verdict.detail)

    def test_outlier_does_not_replace_median(self):
        verdict = runner._budget(
            {"budget": {"tokens_total": 1000}},
            [result(n) for n in [1000, 1000, 1000, 1000, 100000]],
        )
        self.assertTrue(verdict.passed)

    def test_even_number_of_runs_and_threshold_equality(self):
        for limit, expected in [(1000, True), (999, False)]:
            with self.subTest(limit=limit):
                verdict = runner._budget(
                    {"budget": {"tokens_total": limit}},
                    [result(900), result(1100)],
                )
                self.assertEqual(verdict.passed, expected)

    def test_latency_still_checks_worst_dialog(self):
        verdict = runner._budget(
            {"budget": {"latency_ms": 35000}},
            [result(1000, n) for n in [1000, 1000, 1000, 1000, 35001]],
        )
        self.assertFalse(verdict.passed)
        self.assertIn("latency 35001ms > 35000ms", verdict.detail)

    def test_no_completed_runs_is_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "at least one completed run"):
            runner._budget({"budget": {"tokens_total": 68000}}, [])

    def test_d14_case_budget_rejects_repeated_transactions(self):
        cases = loader.load(runner.SETS_DIR / "l05_dialogs.jsonl")
        case = next(c for c in cases if c["id"] == "D14-BUDGET")
        self.assertEqual(case["context"]["customer_id"], "CUS-0003")
        self.assertEqual(case["additional_metadata"]["gate"], "daily")
        self.assertEqual(case["additional_metadata"]["runs"], 1)
        for count, expected in [(1, True), (8, False)]:
            with self.subTest(count=count):
                run = result(1000)
                run["trace"]["children"] = [
                    {"name": "tool.get_transactions", "attributes": {}}
                    for _ in range(count)
                ]
                verdict = runner.evaluate(case, [run])
                self.assertEqual(verdict.passed, expected)

    def test_report_and_console_include_per_run_metrics(self):
        case = {
            "id": "C07-BUDGET",
            "input": "Question",
            "additional_metadata": {
                "assertion": "budget", "budget": {"tokens_total": 68000},
                "runs": 5, "layer": "memory", "oracle": "human",
                "severity": "medium", "gate": "release",
            },
        }
        results = [result(n) for n in [61098, 59817, 57315, 60306, 59329]]
        with patch.dict(os.environ, {"REPEAT": "", "TRACES": ""}):
            with patch.object(runner, "_one_run", side_effect=results):
                row = runner.run_case(case)
        self.assertEqual(row["budget_stats"]["tokens_per_run"],
                         [61098, 59817, 57315, 60306, 59329])
        self.assertEqual(row["budget_stats"]["tokens_median"], 59817)
        self.assertEqual(row["budget_stats"]["latency_ms_per_run"], [1000] * 5)
        self.assertEqual(row["budget_stats"]["latency_ms_max"], 1000)
        self.assertEqual(row["tokens"], sum(r["usage"]["input_tokens"] + 100
                                            for r in results))
        text = "\n".join(console.case_lines(case, row, columns=120))
        self.assertRegex(text, r"per run:\s+tokens")
        self.assertIn("59817", text)


if __name__ == "__main__":
    unittest.main()
