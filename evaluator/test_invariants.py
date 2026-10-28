import copy
import json
import unittest
from pathlib import Path

import assertions
import loader
import runner


CASE = Path(__file__).resolve().parent.parent / "l05_dialogs.jsonl"


def transaction_trace(amount=240.0):
    return {
        "name": "agent.request",
        "children": [{
            "name": "tool.get_transactions",
            "attributes": {"tool.result": {"transactions": [
                {"id": "TX-0401", "amount": amount},
            ]}},
            "children": [],
        }],
    }


class InvariantTests(unittest.TestCase):
    def test_exact_code_normalization_and_substitution(self):
        pattern = r"\b(duplicate[_ -]charge|service[_ -]not[_ -]rendered)\b"
        for text, expected in [
            ("under **duplicate_charge**", True),
            ("under duplicate-charge", True),
            ("under Duplicate Charge", True),
            ("under service_not_rendered", False),
        ]:
            with self.subTest(text=text):
                result = assertions.extracted_entity(
                    text, pattern, "duplicate_charge", normalize_separators=True)
                self.assertEqual(result.passed, expected)
                self.assertEqual(result.level, 1)

    def test_numeric_entity_tolerance_and_missing(self):
        pattern = r"EUR ([0-9]+(?:\.[0-9]+)?)"
        for text, required, expected in [
            ("EUR 240.009", True, True),
            ("EUR 240.02", True, False),
            ("EUR 180.00", True, False),
            ("Please confirm the amount", True, False),
            ("The deadline is September 18", False, True),
        ]:
            with self.subTest(text=text):
                result = assertions.extracted_entity(
                    text, pattern, 240, numeric=True, required=required)
                self.assertEqual(result.passed, expected)

    def test_numeric_entity_accepts_currency_before_or_after_value(self):
        pattern = (
            r"(?:(?:\bEUR\b|€)\s*\**|"
            r"(?=[0-9]+(?:\.[0-9]{1,2})?\s*(?:\bEUR\b|€)))"
            r"([0-9]+(?:\.[0-9]{1,2})?)\b"
        )
        for text in [
            "EUR 240.00",
            "€240.00",
            "**€ 240.00**",
            "240.00 EUR",
            "**240.00 EUR**",
        ]:
            with self.subTest(text=text):
                self.assertTrue(assertions.extracted_entity(
                    text, pattern, 240, numeric=True).passed)

    def test_tool_result_entity_numeric_matches_transaction_id(self):
        trace = {
            "name": "agent.request",
            "children": [{
                "name": "tool.get_transactions",
                "attributes": {"tool.result": {"transactions": [
                    {"id": "TX-0402", "amount": 240.0},
                    {"id": "TX-0401", "amount": 240.0},
                ]}},
                "children": [],
            }],
        }
        result = assertions.tool_result_entity_numeric(
            trace, "get_transactions", "transactions", "id", "TX-0401",
            "amount", 240, 0.01)
        self.assertTrue(result.passed)
        self.assertFalse(assertions.tool_result_entity_numeric(
            trace, "get_transactions", "transactions", "id", "TX-0401",
            "amount", 180, 0.01).passed)
        self.assertFalse(assertions.tool_result_entity_numeric(
            trace, "get_transactions", "transactions", "id", "TX-9999",
            "amount", 240, 0.01).passed)

    def test_no_summary_control_checks_every_turn(self):
        case = loader.load(CASE)[0]
        case["turns"] = case["turns"][:3]
        meta = case["additional_metadata"]
        meta["summarize_after"] = 4
        meta["expect_summarize"] = False
        meta["summarize_turn"] = 3
        meta["invariants"] = [meta["invariants"][0]]
        meta["invariants"][0]["probe_turns"] = [2, 3]
        meta["invariants"][0]["required_turns"] = [3]
        case["turns"][0]["content"] = "Look up TX-0401 and verify its amount."
        loader._compile_invariants(case, meta, "test")
        no_summary = [c for c in meta["checks"] if c["assertion"] == "no_span"]
        self.assertEqual([c["turn"] for c in no_summary], [1, 2, 3])

    def test_exact_is_case_sensitive_without_normalization(self):
        self.assertFalse(assertions.extracted_entity(
            "DUPLICATE_CHARGE", r"(\w+)", "duplicate_charge").passed)

    def test_loader_rejects_bad_extractor(self):
        raw = json.loads(CASE.read_text().splitlines()[0])
        for pattern in ["(", "EUR [0-9]+", "(EUR) ([0-9]+)"]:
            case = copy.deepcopy(raw)
            case["additional_metadata"]["invariants"][0]["extract_pattern"] = pattern
            with self.subTest(pattern=pattern), self.assertRaises(loader.SetError):
                loader._validate(case, "test")

    def test_case_loading_and_evaluation(self):
        case = loader.load(CASE)[0]
        summary = {"name": "agent.summarize", "children": []}
        for amount, code, expected in [
            (240, "duplicate-charge", True),
            (180, "duplicate_charge", False),
            (240, "service_not_rendered", False),
        ]:
            turns = [{"answer": "", "trace": {}} for _ in range(7)]
            turns[0]["trace"] = transaction_trace()
            turns[1]["answer"] = "under duplicate_charge"
            turns[2]["trace"] = summary
            turns[4]["answer"] = f"TX-0401: EUR {amount}.00 under {code}."
            with self.subTest(amount=amount, code=code):
                self.assertEqual(
                    runner.evaluate(case, [{"turns": turns}]).passed, expected)

    def test_optional_absence_cannot_mask_required_probe(self):
        case = loader.load(CASE)[0]
        turns = [{"answer": "", "trace": {}} for _ in range(7)]
        turns[0]["trace"] = transaction_trace()
        turns[1]["answer"] = "under duplicate_charge"
        turns[2]["trace"] = {"name": "agent.summarize", "children": []}
        turns[6]["answer"] = "EUR 240.00 under duplicate_charge"
        result = runner.evaluate(case, [{"turns": turns}])
        self.assertFalse(result.passed)
        self.assertNotEqual(result.assertion, "precondition")

    def test_wrong_baseline_invalidates_run(self):
        case = loader.load(CASE)[0]
        turns = [{"answer": "", "trace": {}} for _ in range(7)]
        turns[0]["trace"] = transaction_trace(180.0)
        turns[1]["answer"] = "under duplicate_charge"
        turns[2]["trace"] = {"name": "agent.summarize", "children": []}
        turns[4]["answer"] = "EUR 240.00 under duplicate_charge"
        result = runner.evaluate(case, [{"turns": turns}])
        self.assertFalse(result.passed)
        self.assertEqual(result.assertion, "precondition")

    def test_cases_use_tool_result_baseline_and_no_summary_control(self):
        cases = {case["id"]: case for case in loader.load(CASE)}
        self.assertEqual(len(cases), 8)
        self.assertEqual(cases["C07-BUDGET"]["additional_metadata"]["assertion"],
                         "budget")
        for case in cases.values():
            if case["additional_metadata"]["assertion"] in (
                    "budget", "tool_call_count"):
                continue
            amount_check = next(
                check for check in case["additional_metadata"]["checks"]
                if check.get("label") == "dispute_amount tool-result baseline")
            self.assertEqual(amount_check["assertion"], "tool_result_entity_numeric")
            self.assertEqual(amount_check["match_value"], "TX-0401")
            self.assertEqual(amount_check["expected_number"], 240)
        no_summary = [
            check for check in cases["CONV-007"]["additional_metadata"]["checks"]
            if check["assertion"] == "no_span"
        ]
        self.assertEqual([check["turn"] for check in no_summary], list(range(1, 8)))


if __name__ == "__main__":
    unittest.main()
