""
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import assertions as A
import console
import stand
from loader import load, set_hash, summarise

SETS_DIR = Path(os.environ.get("EVAL_SETS_DIR", HERE.parent)).resolve()
REPORTS_DIR = Path(os.environ.get("EVAL_REPORTS_DIR", HERE.parent / "reports")).resolve()
RUBRIC_VERSION = os.environ.get("RUBRIC_VERSION", "rubric.v1")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL") or "not-used"
SUMMARIZE_SPAN = "agent.summarize"
RUN_STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")



def _engines():
    ""
    app_dir = Path(os.environ.get(
        "PAYPILOT_APP_DIR", HERE.parent.parent / "paypilot-stand" / "app"))
    root = app_dir.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from app.engines import disputes, fx, limits, policy
    return {"fx": fx, "limits": limits, "disputes": disputes, "policy": policy}



class JudgeUnavailable(RuntimeError):
    pass


def _judge(question, answer, rubric):
    ""
    if JUDGE_MODEL == "not-used":
        raise JudgeUnavailable("judge not configured (set JUDGE_MODEL)")
    from judge import verdict
    return verdict(question, answer, rubric, JUDGE_MODEL)


def evaluate(case: dict, results: list[dict]) -> A.Verdict:
    ""
    meta = case["additional_metadata"]
    if meta["assertion"] == "dialog":
        return _dialog(case, results)
    answer = results[-1]["answer"] if results else ""
    tree = results[-1].get("trace") or {}
    return _apply(meta["assertion"], meta, case, answer, tree, results)


def _apply(name: str, p: dict, case: dict, answer: str, tree: dict,
           results: list[dict]) -> A.Verdict:
    ""
    expected = p.get("expected", case.get("expected_output"))

    if name in ("equals", "exact"):
        return A.level1_exact(answer, expected)
    if name in ("entity_exact", "entity_numeric"):
        return A.extracted_entity(
            answer, p["extract_pattern"],
            p["expected_number"] if name == "entity_numeric" else p["expected"],
            numeric=name == "entity_numeric", tolerance=p.get("tolerance", 0.01),
            required=p.get("required", True),
            normalize_separators=p.get("normalize_separators", False))
    if name == "exact_tool_calls":
        return A.tool_calls_exact(tree, p.get("expected_tool_calls", []))
    if name in ("numeric", "number", "amount"):
        return A.level2_numeric(answer, p.get("expected_number", expected),
                                p.get("tolerance", 0.01))
    if name == "budget":
        return _budget(p, results)
    if name in ("contains", "pattern"):
        return A.level3_contains(answer, expected or p.get("reference", ""))
    if name == "regex":
        return A.level3_regex(answer, p["pattern"])
    if name in ("not_contains", "absent"):
        return A.level4_not_contains(answer, p["forbidden"])
    if name == "not_regex":
        return A.level4_not_regex(answer, p["forbidden_pattern"])
    if name == "tool_result_numeric":
        return A.tool_result_numeric(tree, p["tool"], p["field"],
                                     p["expected_number"],
                                     p.get("tolerance", 0.01))
    if name == "tool_result_entity_numeric":
        return A.tool_result_entity_numeric(
            tree, p["tool"], p["collection_field"], p["match_field"],
            p["match_value"], p["value_field"], p["expected_number"],
            p.get("tolerance", 0.01))
    if name == "tool_grounded_numeric":
        return A.tool_grounded_numeric(tree, answer, p["tool"],
                                       p["field"], p["expected_number"],
                                       p.get("tolerance", 0.01))
    if name == "tool_result_flag":
        return A.tool_result_flag(tree, p["tool"], p["field"],
                                  p["expected_flag"])
    if name == "tool_called_with":
        return A.tool_called_with(tree, p["tool"], p["args"])
    if name == "tool_sequence":
        return A.tool_sequence(tree, p.get("must_include"),
                               p.get("must_exclude"),
                               tuple(p["before"]) if p.get("before") else None)
    if name == "state_row":
        rows = stand.state(p["table"])
        return A.state_row(rows, p.get("where", {}),
                           p.get("expect_fields", {}),
                           p.get("expected_count"))
    if name == "tool_call_count":
        return A.tool_call_count(tree, p["tool"], p["expected_count"])
    if name == "has_span":
        return A.has_span(tree, p["span"])
    if name == "no_span":
        return A.level4_no_span(tree, p["forbidden_span"])
    if name == "similar":
        return A.level5_similar(answer, p.get("reference", expected or ""),
                                p.get("threshold", 0.5))
    if name == "k_of_n":
        hits = [A.level3_contains(r["answer"], expected).passed for r in results]
        return A.level6_k_of_n(hits, p.get("k", 4))
    if name == "judge":
        return A.level7_judge(answer, p.get("rubric", ""),
                              case.get("input", ""), _judge)
    return A.Verdict(False, 0, name, f"no handler for assertion {name!r}")


def _dialog(case: dict, results: list[dict]) -> A.Verdict:
    ""
    verdicts = [_dialog_run(case, run) for run in results]
    bad = [(i, v) for i, v in enumerate(verdicts, 1) if not v.passed]
    if not bad:
        return verdicts[-1]
    i, v = bad[0]
    if len(results) > 1:
        v.detail = f"{len(bad)}/{len(results)} runs failed; run {i}: {v.detail}"
    return v


def _dialog_run(case: dict, run: dict) -> A.Verdict:
    meta = case["additional_metadata"]
    turns = run["turns"]
    outcomes = []
    for chk in meta["checks"]:
        wanted = chk.get("turns") or [chk["turn"]]
        tried = []
        for t in wanted:
            if t > len(turns):
                v = A.Verdict(False, 1, chk["assertion"],
                              f"the dialog has only {len(turns)} turns")
            else:
                at = turns[t - 1]
                v = _apply(chk["assertion"], chk, case, at["answer"],
                           at.get("trace") or {}, [{"answer": at["answer"]}])
            tried.append((t, v))
        # `turns` is an "any of" list: one turn that passes is enough
        t, v = next(((t, v) for t, v in tried if v.passed), tried[0])
        outcomes.append({
            "turn": t if len(wanted) == 1 or v.passed else wanted,
            "assertion": chk["assertion"],
            "label": chk.get("label", ""),
            "defect": chk.get("defect", ""),
            "precondition": bool(chk.get("precondition")),
            "passed": v.passed,
            "level": v.level,
            "detail": v.detail,
            "hint": chk.get("hint", ""),
        })

    broken = [o for o in outcomes if o["precondition"] and not o["passed"]]
    if broken:
        o = broken[0]
        where = _turn_text(o["turn"])
        detail = f"precondition failed at {where}: {o['detail']}"
        if o["hint"]:
            detail += f". {o['hint']}"
        return A.Verdict(False, 1, "precondition", detail, outcomes)

    graded = [o for o in outcomes if not o["precondition"]]
    failed = [o for o in graded if not o["passed"]]
    level = max((o["level"] for o in graded), default=1)
    if not failed:
        return A.Verdict(True, level, "dialog",
                         f"{len(graded)} checks over {len(turns)} turns", outcomes)
    detail = "; ".join(
        f"{_turn_text(o['turn'])}"
        + (f" [{o['defect']}]" if o["defect"] else "")
        + f" {o['detail']}" for o in failed)
    return A.Verdict(False, level, "dialog", detail, outcomes)


def _turn_text(turn) -> str:
    if isinstance(turn, list):
        return "turns " + "/".join(str(t) for t in turn)
    return f"turn {turn}"


def _budget_stats(results: list[dict]) -> dict:
    if not results:
        raise ValueError("budget requires at least one completed run")
    tokens = [r["usage"]["input_tokens"] + r["usage"]["output_tokens"]
              for r in results]
    latencies = [r["latency_ms"] for r in results]
    return {
        "tokens_per_run": tokens,
        "tokens_median": statistics.median(tokens),
        "latency_ms_per_run": latencies,
        "latency_ms_max": max(latencies),
    }


def _budget(meta: dict, results: list[dict]) -> A.Verdict:
    budget = meta.get("budget", {})
    stats = _budget_stats(results)
    tokens = stats["tokens_median"]
    identical = 0
    for r in results:
        calls = [
            (call["name"], json.dumps(
                call.get("arguments"), sort_keys=True, separators=(",", ":"),
                ensure_ascii=False))
            for call in stand.tool_calls(r.get("trace") or {})
        ]
        identical = max(identical, max(Counter(calls).values(), default=0))
    problems = []
    if "tokens_total" in budget and tokens > budget["tokens_total"]:
        problems.append(f"median tokens {tokens:g} > {budget['tokens_total']}")
    if ("identical_tool_calls_max" in budget
            and identical > budget["identical_tool_calls_max"]):
        problems.append(f"identical tool calls {identical} > "
                        f"{budget['identical_tool_calls_max']}")
    if "latency_ms" in budget:
        worst = stats["latency_ms_max"]
        if worst > budget["latency_ms"]:
            problems.append(f"latency {worst}ms > {budget['latency_ms']}ms")
    ok = not problems
    level = 2 if {"tokens_total", "latency_ms"} & budget.keys() else 1
    return A.Verdict(ok, level, "budget",
                     "; ".join(problems) if problems else
                     f"median tokens {tokens:g}, identical calls {identical}")


def _evaluate_with_budget(case: dict, results: list[dict]) -> tuple[A.Verdict, A.Verdict | None]:
    meta = case["additional_metadata"]
    verdict = evaluate(case, results)
    if "budget" not in meta:
        return verdict, None
    if meta["assertion"] == "budget":
        return verdict, verdict

    budget_verdict = _budget(meta, results)
    if not budget_verdict.passed:
        verdict.passed = False
        verdict.detail = f"{verdict.detail}; budget: {budget_verdict.detail}"
    return verdict, budget_verdict



def _summarize_after(meta: dict) -> int | None:
    ""
    raw = os.environ.get("SUMMARIZE_AFTER", "").strip()
    if raw:
        return int(raw)
    return meta.get("summarize_after")


def _one_run(case: dict) -> dict:
    meta = case["additional_metadata"]
    ctx = case.get("context") or {}
    stand.reset()
    if ctx.get("clock"):
        stand.set_clock(ctx["clock"])
    steps = _summarize_after(meta)
    if steps is not None:
        stand.set_summarize_after(steps)
    customer = ctx.get("customer_id")
    t0 = time.time()
    if case.get("turns"):
        asked = [t["content"] for t in case["turns"] if t["role"] == "user"]
        outs = stand.dialog(asked, customer)
        turns = [{"question": q,
                  "answer": o.get("answer", ""),
                  "usage": o.get("usage") or {},
                  "trace": stand.trace(o["request_id"])}
                 for q, o in zip(asked, outs)]
    else:
        out = stand.chat(case["input"], customer_id=customer)
        turns = [{"question": case["input"],
                  "answer": out.get("answer", ""),
                  "usage": out.get("usage") or {},
                  "trace": stand.trace(out["request_id"])}]
    tokens = {"input_tokens": sum(t["usage"].get("input_tokens", 0) for t in turns),
              "output_tokens": sum(t["usage"].get("output_tokens", 0) for t in turns)}
    return {
        "answer": turns[-1]["answer"],
        "usage": tokens,
        "latency_ms": int((time.time() - t0) * 1000),
        "trace": turns[-1]["trace"],
        "turns": turns,
    }


def _fragments(calls: list[dict]) -> list[dict]:
    ""
    out = []
    for c in calls:
        if c["name"] == "search_knowledge_base" and isinstance(c.get("result"), dict):
            out += c["result"].get("fragments") or []
    return out


def _transcript(run: dict) -> list[dict]:
    ""
    out = []
    for i, t in enumerate(run["turns"], 1):
        tree = t["trace"] or {}
        calls = stand.tool_calls(tree)
        out.append({"turn": i,
                    "user": t["question"],
                    "answer": t["answer"],
                    "summarized": stand.has_span(tree, SUMMARIZE_SPAN),
                    "tools": [c["name"] for c in calls],
                    "tool_calls": calls,
                    "fragments": [{"id": f.get("id"), "text": f.get("text", "")}
                                  for f in _fragments(calls)],
                    "usage": t["usage"]})
    return out


def _turn_no(turn) -> int:
    return turn if isinstance(turn, int) else min(turn)


def _runs_detail(case: dict, results: list[dict]) -> list[dict]:
    ""
    detail = []
    for i, run in enumerate(results, 1):
        v = _dialog_run(case, run)
        failed = sorted({_turn_no(o["turn"]) for o in v.checks
                         if not o["passed"] and not o["precondition"]})
        detail.append({"run": i, "passed": v.passed,
                       "precondition_failed": v.assertion == "precondition",
                       "failed_turns": failed,
                       "first_fail_turn": failed[0] if failed else None,
                       "transcript": _transcript(run)})
    return detail


def _run_stats(detail: list[dict]) -> dict:
    firsts = [d["first_fail_turn"] for d in detail if d["first_fail_turn"]]
    return {"runs": len(detail),
            "passed": sum(1 for d in detail if d["passed"]),
            "first_fail_turns": [d["first_fail_turn"] for d in detail],
            "min": min(firsts) if firsts else None,
            "max": max(firsts) if firsts else None,
            "median": statistics.median(firsts) if firsts else None}


def _dump_traces(case_id: str, run_no: int, run: dict) -> str:
    ""
    folder = REPORTS_DIR / "traces" / RUN_STAMP
    folder.mkdir(parents=True, exist_ok=True)
    for n, t in enumerate(run["turns"], 1):
        path = folder / f"{case_id}-run{run_no}-turn{n}.json"
        path.write_text(json.dumps(
            {"case": case_id, "run": run_no, "turn": n, "user": t["question"],
             "answer": t["answer"], "trace": t["trace"]},
            indent=2, ensure_ascii=False), encoding="utf-8")
    return str(folder.relative_to(HERE.parent))


def run_case(case: dict) -> dict:
    meta = case["additional_metadata"]
    runs = int(os.environ.get("REPEAT") or meta.get("runs", 1))
    results = []
    skipped = False
    traces_dir = None
    budget_verdict = None
    try:
        for k in range(1, runs + 1):
            results.append(_one_run(case))
            if os.environ.get("TRACES") == "1":
                traces_dir = _dump_traces(case["id"], k, results[-1])
        verdict, budget_verdict = _evaluate_with_budget(case, results)
    except JudgeUnavailable as e:
        verdict = A.Verdict(False, 7, "judge", str(e))
        skipped = True
    except stand.StandError as e:
        verdict = A.Verdict(False, 1, "stand_error", str(e))
    row = {
        "skipped": skipped,
        "id": case["id"],
        "layer": meta["layer"],
        "oracle": meta["oracle"],
        "severity": meta["severity"],
        "gate": meta.get("gate", "daily"),
        "runs": runs,
        "verdict": verdict.as_dict(),
        "answers": [r["answer"][:400] for r in results],
        "latency_ms": results[-1]["latency_ms"] if results else 0,
        "tokens": sum((r["usage"] or {}).get("input_tokens", 0)
                      + (r["usage"] or {}).get("output_tokens", 0)
                      for r in results),
        "tool_calls": (stand.tool_calls(results[-1].get("trace") or {})
                       if results else []),
    }
    if budget_verdict is not None:
        row["budget_verdict"] = budget_verdict.as_dict()
    if case.get("turns") and results:
        row["transcript"] = _transcript(results[-1])
        if meta["assertion"] == "dialog":
            row["runs_detail"] = _runs_detail(case, results)
            row["run_stats"] = _run_stats(row["runs_detail"])
    if traces_dir:
        row["traces_dir"] = traces_dir
    if "budget" in meta and results:
        row["budget_stats"] = _budget_stats(results)
    return row


def main() -> int:
    set_name = os.environ.get("SET", "l05_dialogs")
    gate = os.environ.get("GATE", "")
    only = {x.strip() for x in os.environ.get("CASE", "").split(",") if x.strip()}
    path = SETS_DIR / f"{set_name}.jsonl"

    stand.wait_until_ready()
    profile_now = stand.profile()["profile"]
    prompt_ver = stand.prompt_version()
    cases = load(path)
    if only:
        cases = [c for c in cases if c["id"] in only]
        if not cases:
            print(f"no case matched CASE={sorted(only)}")
            return 1
    if gate:
        cases = [c for c in cases
                 if c["additional_metadata"].get("gate", "daily") == gate]
        if not cases:
            print(f"no cases with gate={gate} in {set_name} — nothing was run. "
                  "Use --gate all (or the right gate)")
            return 1

    print(f"set {set_name} ({len(cases)} cases)  profile {profile_now}  "
          f"prompt {prompt_ver}" + (f"  gate {gate}" if gate else ""))
    print("-" * 72)

    brief = os.environ.get("BRIEF", "") == "1"
    colour = console.colour_enabled()
    t0 = time.time()
    rows = []
    for case in cases:
        row = run_case(case)
        rows.append(row)
        for line in console.case_lines(case, row, brief=brief, colour=colour):
            print(line)
    elapsed = round(time.time() - t0, 1)

    graded = [r for r in rows if not r.get("skipped")]
    skipped = [r for r in rows if r.get("skipped")]
    passed = sum(1 for r in graded if r["verdict"]["passed"])
    by_layer = {}
    for r in graded:
        s = by_layer.setdefault(r["layer"], {"passed": 0, "total": 0})
        s["total"] += 1
        s["passed"] += bool(r["verdict"]["passed"])

    print("-" * 72)
    print(f"{passed}/{len(graded)} passed"
          + (f"   {len(skipped)} skipped (no judge)" if skipped else "")
          + f"   elapsed {elapsed}s")
    for layer, s in sorted(by_layer.items()):
        print(f"  {layer:<12} {s['passed']}/{s['total']}")
    failed = None if brief else console.failed_line(rows, colour)
    if failed:
        print(failed)

    record = {
        "prompt_version": prompt_ver,
        "set_hash": set_hash(path),
        "profile": profile_now,
        "judge_model": JUDGE_MODEL,
        "rubric_version": RUBRIC_VERSION,
        "elapsed": elapsed,
        "set": set_name,
        "gate": gate or "all",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "total": len(graded),
        "skipped": len(skipped),
        "by_layer": by_layer,
        "coverage": summarise(cases),
        "cases": rows,
    }
    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = RUN_STAMP
    out = REPORTS_DIR / f"{set_name}-{profile_now}-{stamp}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"report -> {out}")
    return 0 if passed == len(graded) else 1


if __name__ == "__main__":
    sys.exit(main())
