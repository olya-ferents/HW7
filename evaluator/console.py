import os
import shutil
import sys
import textwrap

LABEL_WIDTH = 10
INDENT = " " * 6
ANSWER_LINES = 6
RESULT_LINES = 3
STYLES = {"green": "32", "red": "31", "yellow": "33", "cyan": "36",
          "dim": "2", "bold": "1"}
MARK_STYLE = {"PASS": "green", "FAIL": "red", "SKIP": "yellow"}


def colour_enabled(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", None)) and stream.isatty()


def paint(text: str, style: str, colour: bool) -> str:
    if not colour:
        return text
    return f"\033[{STYLES[style]}m{text}\033[0m"


def width() -> int:
    columns = shutil.get_terminal_size((100, 24)).columns
    return max(72, min(columns, 120))


def mark(row: dict) -> str:
    if row.get("skipped"):
        return "SKIP"
    return "PASS" if row["verdict"]["passed"] else "FAIL"


def brief_line(row: dict) -> str:
    return (f"{mark(row)}  {row['id']:<10} L{row['verdict']['level']} "
            f"{row['layer']:<11} {row['verdict']['detail'][:70]}")


def expectation(case: dict) -> str:
    meta = case.get("additional_metadata") or {}
    name = meta.get("assertion", "")
    expected = case.get("expected_output")
    tolerance = meta.get("tolerance", 0.01)
    target = f"{meta.get('tool')}.{meta.get('field')}"
    if name == "tool_grounded_numeric":
        return (f"{target} = {meta.get('expected_number')} +/-{tolerance}, "
                f"and the same figure in the answer")
    if name == "tool_result_numeric":
        return f"{target} = {meta.get('expected_number')} +/-{tolerance}"
    if name == "tool_result_flag":
        return f"{target} = {meta.get('expected_flag')!r}"
    if name in ("numeric", "number", "amount"):
        return (f"{meta.get('expected_number', expected)} +/-{tolerance} "
                f"in the answer")
    if name in ("contains", "pattern", "k_of_n"):
        return f"answer contains {expected or meta.get('reference', '')!r}"
    if name == "regex":
        return f"answer matches {meta.get('pattern')!r}"
    if name in ("not_contains", "absent"):
        return f"answer does not contain {meta.get('forbidden')!r}"
    if name == "not_regex":
        return f"answer does not match {meta.get('forbidden_pattern')!r}"
    if name == "tool_called_with":
        args = ", ".join(f"{k}={v}" for k, v in (meta.get("args") or {}).items())
        return f"{meta.get('tool')} called with {args}"
    if name == "has_span":
        return f"span {meta.get('span')} present"
    if name == "exact_tool_calls":
        return f"tools called exactly {meta.get('expected_tool_calls', [])}"
    if name == "tool_call_count":
        return f"{meta.get('tool')} called {meta.get('expected_count')}x"
    if name == "no_span":
        return f"no span {meta.get('forbidden_span')}"
    if name == "judge":
        return f"judge rubric: {meta.get('rubric', '')}"
    return str(expected) if expected is not None else name


def questions(case: dict) -> list[str]:
    if case.get("turns"):
        return [t["content"] for t in case["turns"] if t["role"] == "user"]
    return [case.get("input", "")]


def _field(label: str, text: str, columns: int, limit: int | None = None,
           style: str | None = None, colour: bool = False) -> list[str]:
    room = max(20, columns - len(INDENT) - LABEL_WIDTH)
    wrapped = []
    for paragraph in str(text).splitlines() or [""]:
        wrapped.extend(textwrap.wrap(paragraph, room) or [""])
    if limit is not None and len(wrapped) > limit:
        wrapped = wrapped[:limit]
        wrapped[-1] = wrapped[-1][:room - 2] + " …"
    out = []
    for i, line in enumerate(wrapped):
        head = paint(f"{label:<{LABEL_WIDTH}}", "dim", colour) if i == 0 \
            else " " * LABEL_WIDTH
        body = paint(line, style, colour) if style else line
        out.append(f"{INDENT}{head}{body}")
    return out


def _pairs(payload, first: str | None = None) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    keys = list(payload)
    if first in payload:
        keys.remove(first)
        keys.insert(0, first)
    return ", ".join(f"{k}={payload[k]}" for k in keys)


def _tool_lines(case: dict, row: dict, columns: int, colour: bool) -> list[str]:
    calls = row.get("tool_calls") or []
    if not calls:
        return _field("tool", "none called", columns, style="dim", colour=colour)
    asserted = (case.get("additional_metadata") or {}).get("field")
    out = []
    for call in calls:
        out += _field("tool", f"{call['name']}({_pairs(call.get('arguments') or {})})",
                      columns, limit=2, style="cyan", colour=colour)
        out += _field("", "-> " + _pairs(call.get("result"), asserted),
                      columns, limit=RESULT_LINES)
    return out


def _answer(row: dict) -> str:
    answers = row.get("answers") or [""]
    lines = [line.strip() for line in answers[-1].replace("**", "").splitlines()]
    return "\n".join(line for line in lines if line)


def _turn_label(turn) -> str:
    if isinstance(turn, list):
        return "T" + "/".join(str(t) for t in turn)
    return f"T{turn}"


def _check_lines(row: dict, columns: int, colour: bool) -> list[str]:
    ""
    checks = (row.get("verdict") or {}).get("checks") or []
    out = []
    for i, c in enumerate(checks):
        ok = c["passed"]
        state = paint("ok  " if ok else "FAIL", "green" if ok else "red", colour)
        tag = f" [{c['defect']}]" if c.get("defect") else ""
        kind = "precondition: " if c.get("precondition") else ""
        text = f"{_turn_label(c['turn']):<8}{state} {kind}{c.get('label') or c['assertion']}{tag}"
        if not ok and c.get("detail"):
            text += f" -> {c['detail']}"
        out += _field("checks" if i == 0 else "", text, columns, limit=3)
    return out


def _stats_lines(row: dict, columns: int) -> list[str]:
    ""
    st = row.get("run_stats")
    if not st or st["runs"] < 2:
        return []
    firsts = ", ".join("-" if x is None else str(x) for x in st["first_fail_turns"])
    text = (f"{st['passed']}/{st['runs']} runs passed; first failing turn per run: "
            f"{firsts}")
    if st["median"] is not None:
        text += f"; median {st['median']}, range {st['min']}-{st['max']}"
    return _field("runs", text, columns, limit=3)


def _budget_lines(row: dict, columns: int) -> list[str]:
    stats = row.get("budget_stats")
    if not stats:
        return []
    verdict = row.get("budget_verdict") or {}
    status = "PASS" if verdict.get("passed") else "FAIL"
    detail = verdict.get("detail", "")
    return (
        _field("per run:", f"tokens {stats['tokens_per_run']}", columns)
        + _field("", f"latency_ms {stats['latency_ms_per_run']}", columns)
        + _field("budget", f"{status}: {detail}; median tokens "
                 f"{stats['tokens_median']:g}; max latency "
                 f"{stats['latency_ms_max']}ms", columns)
    )


def _dialog_lines(case: dict, row: dict, columns: int, colour: bool) -> list[str]:
    ""
    out = []
    for t in row.get("transcript") or []:
        out += _field(f"turn {t['turn']}", "U: " + t["user"], columns, limit=2)
        out += _field("", "A: " + " ".join(t["answer"].replace("**", "").split()),
                      columns, limit=2, style="dim", colour=colour)
        if t.get("summarized"):
            out += _field("", "~ agent.summarize ran on this turn", columns,
                          style="cyan", colour=colour)
    return out


def case_lines(case: dict, row: dict, brief: bool = False,
               colour: bool = False, columns: int | None = None) -> list[str]:
    if brief:
        return [brief_line(row)]
    columns = columns or width()
    status = mark(row)
    verdict = row["verdict"]
    timing = f"{row.get('latency_ms', 0) / 1000:.1f}s, {row.get('tokens', 0)} tokens"
    if row.get("runs", 1) > 1:
        timing += f" over {row['runs']} runs, last run shown"
    head = (f"{paint(status, MARK_STYLE[status], colour)}  "
            f"{paint(format(row['id'], '<10'), 'bold', colour)} "
            f"L{verdict['level']} "
            f"{row['layer']:<11} {verdict['assertion']}  "
            f"{paint(timing, 'dim', colour)}")
    out = [head]
    out += _budget_lines(row, columns)
    if row.get("transcript"):
        out += _dialog_lines(case, row, columns, colour)
        out += _check_lines(row, columns, colour)
        out += _stats_lines(row, columns)
        if not verdict.get("checks") or verdict["assertion"] == "precondition":
            out += _field("verdict", verdict["detail"] or "ok", columns, limit=4,
                          style=MARK_STYLE[status], colour=colour)
        out.append("")
        return out
    asked = questions(case)
    for i, text in enumerate(asked, 1):
        label = "question" if len(asked) == 1 else f"turn {i}"
        out += _field(label, text, columns, limit=3)
    out += _field("expected", expectation(case), columns, limit=3)
    out += _tool_lines(case, row, columns, colour)
    out += _field("verdict", verdict["detail"] or "ok", columns, limit=4,
                  style=MARK_STYLE[status], colour=colour)
    out += _field("answer", _answer(row), columns, limit=ANSWER_LINES)
    out.append("")
    return out


def failed_line(rows: list[dict], colour: bool = False) -> str | None:
    failed = [r["id"] for r in rows
              if not r.get("skipped") and not r["verdict"]["passed"]]
    if not failed:
        return None
    return paint("failed: " + ", ".join(failed), "red", colour)
