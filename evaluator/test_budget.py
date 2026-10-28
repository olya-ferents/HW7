import os
import unittest
from pathlib import Path
from unittest.mock import patch

import assertions as A
import console
import loader
import runner


CASE = Path(__file__).resolve().parent.parent / "l05_dialogs.jsonl"


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

    def test_identical_tool_call_budget_compares_name_and_arguments_at_level_one(self):
        run = result(1000)
        run["trace"]["children"] = [
            {"name": "tool.get_transactions",
             "attributes": {"tool.arguments": {"account_id": "ACC-1"}}},
            {"name": "tool.get_transactions",
             "attributes": {"tool.arguments": {"account_id": "ACC-2"}}},
        ]
        verdict = runner._budget(
            {"budget": {"identical_tool_calls_max": 1}}, [run])
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.level, 1)

        run["trace"]["children"][1]["attributes"]["tool.arguments"] = {
            "account_id": "ACC-1"}
        verdict = runner._budget(
            {"budget": {"identical_tool_calls_max": 1}}, [run])
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.level, 1)
        self.assertIn("identical tool calls 2 > 1", verdict.detail)

    def test_no_completed_runs_is_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "at least one completed run"):
            runner._budget({"budget": {"tokens_total": 68000}}, [])

    def test_d14_is_level_one_and_rejects_repeated_transactions(self):
        cases = loader.load(runner.SETS_DIR / "l05_dialogs.jsonl")
        case = next(c for c in cases if c["id"] == "D14-BUDGET")
        self.assertEqual(case["context"]["customer_id"], "CUS-0003")
        self.assertEqual(case["additional_metadata"]["gate"], "daily")
        self.assertEqual(case["additional_metadata"]["runs"], 1)
        self.assertEqual(case["additional_metadata"]["assertion"],
                         "tool_call_count")
        self.assertEqual(A.NAME_TO_LEVEL[
            case["additional_metadata"]["assertion"]], 1)
        for count, expected in [(1, True), (8, False)]:
            with self.subTest(count=count):
                run = result(1000)
                run["trace"]["children"] = [
                    {"name": "tool.get_transactions", "attributes": {}}
                    for _ in range(count)
                ]
                verdict, budget_verdict = runner._evaluate_with_budget(
                    case, [run])
                self.assertEqual(verdict.level, 1)
                self.assertEqual(verdict.passed, expected)
                self.assertEqual(budget_verdict.passed, expected)
                self.assertEqual(budget_verdict.level, 1)

    def test_d15_is_level_two_release_with_five_runs(self):
        cases = loader.load(CASE)
        case = next(c for c in cases if c["id"] == "C07-BUDGET")
        meta = case["additional_metadata"]
        self.assertEqual(meta["assertion"], "budget")
        self.assertEqual(A.NAME_TO_LEVEL[meta["assertion"]], 2)
        self.assertEqual(meta["gate"], "release")
        self.assertEqual(meta["runs"], 5)
        self.assertEqual(meta["budget"]["tokens_total"], 67000)

    def test_conv_token_caps_are_baseline_calibrated_release_cases(self):
        expected_caps = {
            "CONV-002": 61241,
            "CONV-003": 35492,
            "CONV-004": 92834,
            "CONV-005": 55361,
            "CONV-006": 65774,
            "CONV-007": 63081,
        }
        cases = {case["id"]: case for case in loader.load(CASE)}
        for case_id, cap in expected_caps.items():
            with self.subTest(case_id=case_id):
                meta = cases[case_id]["additional_metadata"]
                self.assertEqual(meta["budget"]["tokens_total"], cap)
                self.assertEqual(meta["gate"], "release")
                self.assertEqual(meta["runs"], 5)

    def test_invariant_case_checks_budget_from_same_runs(self):
        case = loader.load(CASE)[0]
        self.assertEqual(case["additional_metadata"]["assertion"], "dialog")
        self.assertIn("tokens_total", case["additional_metadata"]["budget"])
        results = [result(tokens) for tokens in [70000, 71000, 72000, 73000, 74000]]
        for run in results:
            run["turns"] = [{"question": "Q", "answer": "A", "trace": {}}]
        invariant = A.Verdict(True, 3, "dialog", "all invariant checks passed")
        with patch.object(runner, "evaluate", return_value=invariant) as evaluate:
            verdict, budget_verdict = runner._evaluate_with_budget(case, results)

        evaluate.assert_called_once_with(case, results)
        self.assertFalse(verdict.passed)
        self.assertIn("budget: median tokens 72000 > 61241", verdict.detail)
        self.assertFalse(budget_verdict.passed)
        self.assertEqual(budget_verdict.assertion, "budget")

    def test_invariant_failure_is_preserved_when_budget_passes(self):
        case = loader.load(CASE)[0]
        results = [result(tokens) for tokens in [50000, 51000, 52000, 53000, 54000]]
        invariant = A.Verdict(False, 3, "dialog", "reason code mismatch")
        with patch.object(runner, "evaluate", return_value=invariant):
            verdict, budget_verdict = runner._evaluate_with_budget(case, results)

        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.detail, "reason code mismatch")
        self.assertTrue(budget_verdict.passed)
        self.assertIn("median tokens 52000", budget_verdict.detail)

    def test_run_case_reports_budget_verdict_for_invariant_case(self):
        case = loader.load(CASE)[0]
        case.pop("turns")
        results = [result(tokens) for tokens in [70000, 71000, 72000, 73000, 74000]]
        invariant = A.Verdict(True, 3, "dialog", "all invariant checks passed")
        with patch.dict(os.environ, {"REPEAT": "", "TRACES": ""}):
            with patch.object(runner, "_one_run", side_effect=results):
                with patch.object(runner, "evaluate", return_value=invariant):
                    row = runner.run_case(case)

        self.assertFalse(row["verdict"]["passed"])
        self.assertFalse(row["budget_verdict"]["passed"])
        self.assertEqual(row["budget_stats"]["tokens_median"], 72000)
        text = "\n".join(console.case_lines(case, row, columns=120))
        self.assertIn("budget    FAIL:", text)

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
