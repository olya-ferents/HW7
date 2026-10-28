""
import re
from dataclasses import dataclass, field

LEVELS = {
    1: "exact",
    2: "numeric",
    3: "contains",
    4: "not_contains",
    5: "similar",
    6: "k_of_n",
    7: "judge",
}

NAME_TO_LEVEL = {
    "equals": 1, "exact": 1, "exact_tool_calls": 1,
    "entity_exact": 1, "entity_numeric": 2,
    "numeric": 2, "number": 2, "amount": 2, "budget": 2,
    "contains": 3, "regex": 3, "pattern": 3,
    "not_contains": 4, "absent": 4, "no_span": 4, "not_regex": 4,
    "tool_result_numeric": 2, "tool_result_entity_numeric": 2,
    "tool_result_flag": 1, "tool_call_count": 1,
    "tool_grounded_numeric": 2, "tool_called_with": 1,
    "state_row": 1, "tool_sequence": 3,
    "has_span": 1,
    "similar": 5,
    "k_of_n": 6,
    "judge": 7,
    # діалог: верхній рівень лише збирає перевірки по ходах, а рівень кожної
    # береться з самої перевірки (див. loader.summarise)
    "dialog": 3,
    # інваріанти (ДЗ №4): loader.py перетворює їх на dialog до запуску
    "invariant": 3,
}


@dataclass
class Verdict:
    passed: bool
    level: int
    assertion: str
    detail: str
    checks: list = field(default_factory=list)

    def as_dict(self):
        out = {"passed": self.passed, "level": self.level,
               "assertion": self.assertion, "detail": self.detail}
        if self.checks:
            out["checks"] = self.checks
        return out


_NUM_RE = re.compile(r"-?\d[\d\s,]*(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    out = []
    for raw in _NUM_RE.findall(text or ""):
        cleaned = raw.replace(",", "").replace(" ", "")
        try:
            out.append(float(cleaned))
        except ValueError:
            continue
    return out



def level1_exact(actual, expected) -> Verdict:
    ok = str(actual).strip() == str(expected).strip()
    return Verdict(ok, 1, "equals",
                   "" if ok else f"expected {expected!r}, got {str(actual)[:120]!r}")


def extracted_entity(answer: str, pattern: str, expected: str | float,
                     numeric: bool = False, tolerance: float = 0.01,
                     required: bool = True,
                     normalize_separators: bool = False) -> Verdict:
    match = re.search(pattern, answer or "", re.I)
    name, level = ("entity_numeric", 2) if numeric else ("entity_exact", 1)
    if match is None:
        return Verdict(not required, level, name,
                       "entity missing" if required else "entity not stated; skipped")
    value = match.group(1)
    if numeric:
        actual = float(value.replace(",", ""))
        ok = abs(actual - float(expected)) <= tolerance
    else:
        actual = value
        if normalize_separators:
            actual = re.sub(r"[\s-]+", "_", actual.lower())
        ok = actual == str(expected)
    return Verdict(ok, level, name,
                   "" if ok else f"expected {expected!r}, got {actual!r}")


def level2_numeric(answer: str, expected: float, tolerance: float = 0.01) -> Verdict:
    found = _numbers(answer)
    hit = [n for n in found if abs(n - float(expected)) <= tolerance]
    ok = bool(hit)
    return Verdict(ok, 2, "numeric",
                   "" if ok else f"expected {expected} +/-{tolerance}; "
                                 f"numbers in answer: {found[:8]}")


def level3_contains(answer: str, needle: str) -> Verdict:
    ok = needle.lower() in (answer or "").lower()
    return Verdict(ok, 3, "contains",
                   "" if ok else f"{needle!r} not in the answer")


def level3_regex(answer: str, pattern: str) -> Verdict:
    ok = bool(re.search(pattern, answer or "", re.I))
    return Verdict(ok, 3, "regex",
                   "" if ok else f"pattern {pattern!r} did not match")


def level4_not_contains(answer: str, needle: str) -> Verdict:
    ok = needle.lower() not in (answer or "").lower()
    return Verdict(ok, 4, "not_contains",
                   "" if ok else f"forbidden {needle!r} appeared in the answer")


def level4_not_regex(answer: str, pattern: str) -> Verdict:
    ""
    m = re.search(pattern, answer or "", re.I)
    return Verdict(not m, 4, "not_regex",
                   "" if not m else f"forbidden pattern matched {m.group(0)!r}")


def level4_no_span(trace: dict, span_name: str) -> Verdict:
    ""
    present = _has_span(trace, span_name)
    return Verdict(not present, 4, "no_span",
                   "" if not present else f"span {span_name} should not be there")


def has_span(trace: dict, span_name: str) -> Verdict:
    ""
    present = _has_span(trace, span_name)
    return Verdict(present, 1, "has_span",
                   f"span {span_name} present" if present
                   else f"span {span_name} not found in the trace")


def _has_span(node: dict, name: str) -> bool:
    if node.get("name") == name:
        return True
    return any(_has_span(c, name) for c in node.get("children", []))



def level5_similar(answer: str, reference: str, threshold: float = 0.5) -> Verdict:
    ""
    a = set(re.findall(r"[a-z0-9]+", (answer or "").lower()))
    b = set(re.findall(r"[a-z0-9]+", (reference or "").lower()))
    if not b:
        return Verdict(False, 5, "similar", "empty reference")
    score = len(a & b) / len(b)
    ok = score >= threshold
    return Verdict(ok, 5, "similar",
                   f"overlap {score:.2f} vs threshold {threshold}"
                   if not ok else f"overlap {score:.2f}")



def level6_k_of_n(results: list[bool], k: int) -> Verdict:
    hits = sum(bool(x) for x in results)
    ok = hits >= k
    return Verdict(ok, 6, "k_of_n",
                   f"{hits}/{len(results)} runs agreed, needed {k}")



def level7_judge(answer: str, rubric: str, question: str, judge_fn) -> Verdict:
    ""
    ok, why = judge_fn(question, answer, rubric)
    return Verdict(bool(ok), 7, "judge", why)



def tool_calls_exact(trace: dict, expected: list[str]) -> Verdict:
    actual = _tool_sequence(trace)
    ok = actual == expected
    return Verdict(ok, 1, "exact_tool_calls",
                   "" if ok else f"expected {expected}, called {actual}")


def tool_sequence(trace: dict, must_include: list[str] | None = None,
                  must_exclude: list[str] | None = None,
                  before: tuple[str, str] | None = None) -> Verdict:
    ""
    called = _tool_sequence(trace)
    problems = []
    for tool in must_include or []:
        if tool not in called:
            problems.append(f"{tool} was not called")
    for tool in must_exclude or []:
        if tool in called:
            problems.append(f"{tool} should not have been called")
    if before:
        first, second = before
        if first in called and second in called:
            if called.index(first) > called.index(second):
                problems.append(f"{first} must come before {second}")
        elif second in called and first not in called:
            problems.append(f"{second} was called without {first}")
    ok = not problems
    return Verdict(ok, 3, "tool_sequence",
                   f"called {called}" if ok else "; ".join(problems))


def tool_called_with(trace: dict, tool: str, args: dict,
                     level: int = 1) -> Verdict:
    ""
    for span in _spans(trace):
        if span.get("name") != f"tool.{tool}":
            continue
        actual = (span.get("attributes") or {}).get("tool.arguments") or {}
        misses = []
        for key, want in args.items():
            got = actual.get(key)
            if isinstance(want, (int, float)) and isinstance(got, (int, float)):
                if abs(float(got) - float(want)) > 0.01:
                    misses.append(f"{key}={got} != {want}")
            elif str(got) != str(want):
                misses.append(f"{key}={got!r} != {want!r}")
        if not misses:
            return Verdict(True, level, "tool_called_with", f"{tool} args match")
        return Verdict(False, level, "tool_called_with",
                       f"{tool}: " + "; ".join(misses))
    return Verdict(False, level, "tool_called_with", f"{tool} was never called")


def tool_result_numeric(trace: dict, tool: str, field: str,
                        expected: float, tolerance: float = 0.01) -> Verdict:
    ""
    for span in _spans(trace):
        if span.get("name") != f"tool.{tool}":
            continue
        result = (span.get("attributes") or {}).get("tool.result") or {}
        if field not in result:
            return Verdict(False, 2, "tool_result_numeric",
                           f"{tool} result has no field {field!r}")
        got = result[field]
        ok = isinstance(got, (int, float)) and abs(float(got) - float(expected)) <= tolerance
        return Verdict(ok, 2, "tool_result_numeric",
                       f"{tool}.{field} = {got}" if ok else
                       f"{tool}.{field} = {got}, expected {expected}")
    return Verdict(False, 2, "tool_result_numeric", f"{tool} was never called")


def tool_result_entity_numeric(trace: dict, tool: str, collection_field: str,
                               match_field: str, match_value: str,
                               value_field: str, expected: float,
                               tolerance: float = 0.01) -> Verdict:
    name = "tool_result_entity_numeric"
    for span in _spans(trace):
        if span.get("name") != f"tool.{tool}":
            continue
        result = (span.get("attributes") or {}).get("tool.result") or {}
        entries = result.get(collection_field)
        if not isinstance(entries, list):
            return Verdict(False, 2, name,
                           f"{tool} result field {collection_field!r} is not a list")
        matches = [entry for entry in entries
                   if isinstance(entry, dict)
                   and str(entry.get(match_field)) == str(match_value)]
        if len(matches) != 1:
            return Verdict(False, 2, name,
                           f"{tool}.{collection_field} has {len(matches)} "
                           f"entries matching {match_field}={match_value!r}")
        actual = matches[0].get(value_field)
        ok = (isinstance(actual, (int, float))
              and not isinstance(actual, bool)
              and abs(float(actual) - float(expected)) <= tolerance)
        return Verdict(ok, 2, name,
                       f"{tool}.{collection_field}[{match_field}={match_value}]."
                       f"{value_field} = {actual}" if ok else
                       f"{tool}.{collection_field}[{match_field}={match_value}]."
                       f"{value_field} = {actual!r}, expected {expected}")
    return Verdict(False, 2, name, f"{tool} was never called")


def tool_grounded_numeric(trace: dict, answer: str, tool: str, field: str,
                          expected: float, tolerance: float = 0.01) -> Verdict:
    name = "tool_grounded_numeric"
    results = [(s.get("attributes") or {}).get("tool.result") or {}
               for s in _spans(trace) if s.get("name") == f"tool.{tool}"]
    if not results:
        return Verdict(False, 2, name, f"{tool} was never called")
    returned = [r.get(field) if isinstance(r, dict) else None for r in results]
    grounded = [v for v in returned
                if isinstance(v, (int, float)) and not isinstance(v, bool)
                and abs(float(v) - float(expected)) <= tolerance]
    if not grounded:
        return Verdict(False, 2, name,
                       f"{tool}.{field} = {returned}, expected {expected} "
                       f"+/-{tolerance}")
    found = _numbers(answer)
    if not any(abs(n - float(expected)) <= tolerance for n in found):
        return Verdict(False, 2, name,
                       f"{tool}.{field} = {grounded[0]}, but the answer does "
                       f"not carry it; numbers in answer: {found[:8]}")
    return Verdict(True, 2, name, f"{tool}.{field} = {grounded[0]}")


def tool_result_flag(trace: dict, tool: str, field: str, expected) -> Verdict:
    for span in _spans(trace):
        if span.get("name") != f"tool.{tool}":
            continue
        result = (span.get("attributes") or {}).get("tool.result") or {}
        got = result.get(field)
        ok = got == expected
        return Verdict(ok, 1, "tool_result_flag",
                       f"{tool}.{field} = {got}" if ok else
                       f"{tool}.{field} = {got!r}, expected {expected!r}")
    return Verdict(False, 1, "tool_result_flag", f"{tool} was never called")


def state_row(rows: list[dict], where: dict, expect_fields: dict,
              expected_count: int | None = None) -> Verdict:
    ""
    matched = [r for r in rows
               if all(str(r.get(k)) == str(v) for k, v in where.items())]
    if expected_count is not None and len(matched) != expected_count:
        return Verdict(False, 1, "state_row",
                       f"{len(matched)} rows matched {where or '{}'}, "
                       f"expected {expected_count}")
    for field, want in (expect_fields or {}).items():
        for row in matched:
            if str(row.get(field)) != str(want):
                return Verdict(False, 1, "state_row",
                               f"{field} = {row.get(field)!r}, expected {want!r}")
    detail = f"{len(matched)} row(s) match"
    if expect_fields:
        detail += " with " + ", ".join(f"{k}={v}" for k, v in expect_fields.items())
    return Verdict(True, 1, "state_row", detail)


def tool_call_count(trace: dict, tool: str, expected: int) -> Verdict:
    n = sum(1 for s in _spans(trace) if s.get("name") == f"tool.{tool}")
    ok = n == expected
    return Verdict(ok, 1, "tool_call_count",
                   f"{tool} called {n}x, expected {expected}x")


def _spans(node: dict):
    yield node
    for child in node.get("children", []):
        yield from _spans(child)


def _tool_sequence(trace: dict) -> list[str]:
    return [s["name"][5:] for s in _spans(trace)
            if str(s.get("name", "")).startswith("tool.")]
