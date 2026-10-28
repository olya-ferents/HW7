""
import hashlib
import json
import re
from pathlib import Path

from assertions import NAME_TO_LEVEL

REQUIRED = ("id",)
REQUIRED_META = ("layer", "oracle", "assertion", "severity", "source")
LAYERS = {"generation", "retrieval", "memory", "action", "judge", "orchestration"}
ORACLES = {"engine", "corpus", "human"}
PLAN_ROLES = {"основний", "найгірший", "контроль", "ізоляція осі"}
AXES = ("length", "entities", "topic_switches")
SEVERITIES = {"low", "medium", "high", "critical"}
GATES = {"daily", "release"}


class SetError(ValueError):
    pass


def load(path: str | Path) -> list[dict]:
    ""
    path = Path(path)
    if not path.exists():
        raise SetError(f"set not found: {path}")
    cases, seen = [], set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as e:
            raise SetError(f"{path.name}:{lineno}: bad JSON — {e}") from e
        _validate(case, f"{path.name}:{lineno}")
        if case["id"] in seen:
            raise SetError(f"{path.name}:{lineno}: duplicate id {case['id']}")
        seen.add(case["id"])
        cases.append(case)
    if not cases:
        raise SetError(f"{path.name}: no cases")
    return cases


def _validate(case: dict, where: str) -> None:
    for key in REQUIRED:
        if not case.get(key):
            raise SetError(f"{where}: missing {key}")
    if not case.get("input") and not case.get("turns"):
        raise SetError(f"{where}: needs either input or turns")
    meta = case.get("additional_metadata") or {}
    for key in REQUIRED_META:
        if not meta.get(key):
            raise SetError(f"{where}: additional_metadata.{key} is required")
    if meta["layer"] not in LAYERS:
        raise SetError(f"{where}: unknown layer {meta['layer']!r}")
    # «engine:disputes» — engine plus the name of the engine module
    if str(meta["oracle"]).split(":", 1)[0] not in ORACLES:
        raise SetError(f"{where}: unknown oracle {meta['oracle']!r}")
    if meta["severity"] not in SEVERITIES:
        raise SetError(f"{where}: unknown severity {meta['severity']!r}")
    if meta["assertion"] == "invariant":
        _compile_invariants(case, meta, where)
    if meta["assertion"] not in NAME_TO_LEVEL:
        raise SetError(f"{where}: unknown assertion {meta['assertion']!r}")
    if meta.get("gate") and meta["gate"] not in GATES:
        raise SetError(f"{where}: unknown gate {meta['gate']!r}")
    if meta["layer"] == "retrieval" and not meta.get("reference"):
        raise SetError(f"{where}: retrieval cases need a reference "
                       "(context recall cannot be computed without it)")
    if case.get("turns"):
        for t in case["turns"]:
            if t.get("role") not in ("user", "assistant"):
                raise SetError(f"{where}: bad turn role {t.get('role')!r}")
    if meta["assertion"] == "dialog":
        _validate_dialog(case, meta, where)
    _validate_plan_fields(case, meta, where)
    if "summarize_after" in meta and not (
            isinstance(meta["summarize_after"], int) and meta["summarize_after"] > 0):
        raise SetError(f"{where}: summarize_after must be a positive integer")


def _validate_dialog(case: dict, meta: dict, where: str) -> None:
    ""
    if not case.get("turns"):
        raise SetError(f"{where}: assertion 'dialog' needs turns")
    n_user = sum(1 for t in case["turns"] if t["role"] == "user")
    checks = meta.get("checks")
    if not checks:
        raise SetError(f"{where}: assertion 'dialog' needs additional_metadata.checks")
    if all(c.get("precondition") for c in checks):
        raise SetError(f"{where}: a dialog needs at least one check that is "
                       "not a precondition")
    for i, c in enumerate(checks, 1):
        at = f"{where}: checks[{i}]"
        if c.get("assertion") not in NAME_TO_LEVEL or c["assertion"] == "dialog":
            raise SetError(f"{at}: unknown assertion {c.get('assertion')!r}")
        turns = c.get("turns") or ([c["turn"]] if c.get("turn") else [])
        if not turns:
            raise SetError(f"{at}: needs turn (1-based) or turns")
        for t in turns:
            if not isinstance(t, int) or not 1 <= t <= n_user:
                raise SetError(f"{at}: turn {t!r} is outside 1..{n_user} "
                               "(user turns are counted)")


def set_hash(path: str | Path) -> str:
    ""
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def summarise(cases: list[dict]) -> dict:
    ""
    meta = [c.get("additional_metadata", {}) for c in cases]
    levels = set()
    for m in meta:
        if m["assertion"] == "dialog":
            levels |= {NAME_TO_LEVEL[c["assertion"]] for c in m["checks"]
                       if not c.get("precondition")}
        else:
            levels.add(NAME_TO_LEVEL[m["assertion"]])
    return {
        "cases": len(cases),
        "multi_turn": sum(1 for c in cases if c.get("turns")),
        "layers": _count(m["layer"] for m in meta),
        "oracles": _count(str(m["oracle"]).split(":")[0] for m in meta),
        "sources": _count(m["source"] for m in meta),
        "severities": _count(m["severity"] for m in meta),
        "ladder_levels": sorted(levels),
        "gates": _count(m.get("gate", "-") for m in meta),
        "human_oracle_pct": round(
            100 * sum(1 for m in meta if m["oracle"] == "human") / len(meta), 1),
        "severity_filled_pct": round(
            100 * sum(1 for m in meta if m.get("severity")) / len(meta), 1),
    }


def _count(values) -> dict:
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def _n_user(case: dict) -> int:
    return sum(1 for t in case.get("turns", []) if t["role"] == "user")


def _compile_invariants(case: dict, meta: dict, where: str) -> None:
    """assertion "invariant" is a dialog whose checks are generated from
    additional_metadata.invariants. Every invariant names the entity, the step
    it starts to hold from, the ladder level, and HOW to read it: `probe_turns`
    (the steps where the reply must show the entity) plus a `pattern`
    (level 3) or `expected_number` (level 2). An `extract_pattern` selects
    one entity for exact (level 1) or numeric (level 2) comparison.
    `forbid` adds a not_regex for legacy pattern/number invariants."""
    invs = meta.get("invariants")
    if not invs:
        raise SetError(f"{where}: assertion 'invariant' needs invariants")
    if not case.get("turns"):
        raise SetError(f"{where}: assertion 'invariant' needs turns")
    n = _n_user(case)
    checks = []
    expect = meta.get("expect_summarize", bool(meta.get("summarize_after")))
    st = meta.get("summarize_turn", 3)
    # one step, or a list "any of" (a run with another threshold summarizes later)
    where_t = {"turns": st} if isinstance(st, list) else {"turn": st}
    if expect:
        checks.append({"assertion": "has_span", "span": "agent.summarize",
                       "precondition": True, **where_t,
                       "label": "agent.summarize is in the trace",
                       "hint": "Without agent.summarize the case tests nothing: "
                               "lower the threshold (--summarize-after 2)"})
    elif meta.get("summarize_after") and not expect:
        for turn in range(1, n + 1):
            checks.append({
                "assertion": "no_span",
                "forbidden_span": "agent.summarize",
                "precondition": True,
                "turn": turn,
                "label": f"control: no agent.summarize at turn {turn}",
                "hint": "A control without summarization: the threshold "
                        "must exceed the dialog length",
            })
    for i, inv in enumerate(invs, 1):
        at = f"{where}: invariants[{i}]"
        for key in ("entity", "from_turn", "level"):
            if key not in inv:
                raise SetError(f"{at}: missing {key}")
        if not isinstance(inv["level"], int) or not 1 <= inv["level"] <= 7:
            raise SetError(f"{at}: level must be 1..7 (the check ladder)")
        if not isinstance(inv["from_turn"], int) or not 1 <= inv["from_turn"] <= n:
            raise SetError(f"{at}: from_turn is outside 1..{n}")
        if meta.get("added_in") == "hw4":
            ot = inv.get("oracle_turn")
            if not isinstance(ot, int) or not 1 <= ot <= inv["from_turn"]:
                raise SetError(f"{at}: oracle_turn = the step where the value was "
                               "first said (1..from_turn) is required")
            if not inv.get("oracle_by"):
                raise SetError(f"{at}: oracle_by (who checks that the value is "
                               "right: engine:… / kb:… / human) is required")
        probes = inv.get("probe_turns")
        if not probes:
            raise SetError(f"{at}: needs probe_turns — the steps where the "
                           "reply must still show the entity")
        for t in probes:
            if not isinstance(t, int) or not inv["from_turn"] < t <= n:
                raise SetError(f"{at}: probe turn {t!r} must be after from_turn "
                               f"{inv['from_turn']} and within 1..{n}")
        if "extract_pattern" in inv:
            if inv.get("forbid"):
                raise SetError(f"{at}: use explicit checks for forbid with extract_pattern")
            try:
                extractor = re.compile(inv["extract_pattern"])
            except re.error as exc:
                raise SetError(f"{at}: invalid extract_pattern: {exc}") from exc
            if extractor.groups != 1:
                raise SetError(f"{at}: extract_pattern needs exactly one capture group")
            numeric = "expected_number" in inv
            if not numeric and not isinstance(inv.get("expected"), str):
                raise SetError(f"{at}: exact entity needs a string expected")
            want = 2 if numeric else 1
            if inv["level"] != want:
                raise SetError(f"{at}: extracted entity requires level {want}")
            required_turns = inv.get("required_turns", probes)
            if any(t not in probes for t in required_turns):
                raise SetError(f"{at}: required_turns must be within probe_turns")
            if inv.get("probe_any"):
                raise SetError(f"{at}: extracted entities require every probe, not probe_any")
            oracle = inv.get("oracle_assertion")
            if oracle and oracle != "tool_result_entity_numeric":
                raise SetError(f"{at}: unsupported oracle_assertion {oracle!r}")
            if oracle:
                fields = ("oracle_tool", "oracle_collection_field",
                          "oracle_match_field", "oracle_match_value",
                          "oracle_value_field")
                missing = [key for key in fields if not inv.get(key)]
                if missing:
                    raise SetError(f"{at}: tool-result oracle needs "
                                   + ", ".join(missing))
                if not numeric:
                    raise SetError(f"{at}: tool-result entity oracle requires "
                                   "a numeric entity")
            entity_check = {
                "assertion": "entity_numeric" if numeric else "entity_exact",
                "extract_pattern": inv["extract_pattern"],
                **({"expected_number": inv["expected_number"],
                    "tolerance": inv.get("tolerance", 0.01)}
                   if numeric else {"expected": inv["expected"]}),
                "normalize_separators": inv.get("normalize_separators", False),
                "defect": inv.get("defect", ""),
            }
            if oracle:
                checks.append({
                    "assertion": oracle,
                    "tool": inv["oracle_tool"],
                    "collection_field": inv["oracle_collection_field"],
                    "match_field": inv["oracle_match_field"],
                    "match_value": inv["oracle_match_value"],
                    "value_field": inv["oracle_value_field"],
                    "expected_number": inv["expected_number"],
                    "tolerance": inv.get("tolerance", 0.01),
                    "turn": inv.get("oracle_turn", inv["from_turn"]),
                    "required": True,
                    "precondition": True,
                    "label": f"{inv['entity']} tool-result baseline",
                })
            else:
                checks.append({
                    **entity_check, "turn": inv["from_turn"], "required": True,
                    "precondition": True, "label": f"{inv['entity']} baseline",
                })
            for t in probes:
                checks.append({
                    **entity_check,
                    "required": t in required_turns,
                    "turn": t,
                    "label": f"{inv['entity']} still holds",
                })
            continue
        if "expected_number" in inv:
            base = {"assertion": "numeric", "expected_number": inv["expected_number"],
                    "tolerance": inv.get("tolerance", 0.01)}
        elif "pattern" in inv:
            base = {"assertion": "regex", "pattern": inv["pattern"]}
        else:
            raise SetError(f"{at}: needs pattern or expected_number")
        want = 2 if "expected_number" in inv else 3
        if inv["level"] != want:
            raise SetError(f"{at}: level {inv['level']} does not match the check "
                           f"({'numeric' if want == 2 else 'regex'} is level {want})")
        if inv.get("probe_any"):
            # the entity need not be named on every probe step (the question may
            # not ask for it): one step of the list showing it is enough
            checks.append({**base, "turns": list(probes), "defect": inv.get("defect", ""),
                           "label": f"{inv['entity']} still holds (any of {probes})"})
        else:
            for t in probes:
                checks.append({**base, "turn": t, "defect": inv.get("defect", ""),
                               "label": f"{inv['entity']} still holds"})
        if inv.get("forbid"):
            for t in inv.get("forbid_turns", probes):
                if not isinstance(t, int) or not inv["from_turn"] < t <= n:
                    raise SetError(f"{at}: forbid turn {t!r} is outside the dialog")
                checks.append({"assertion": "not_regex",
                               "forbidden_pattern": inv["forbid"], "turn": t,
                               "defect": inv.get("defect", ""),
                               "label": f"{inv['entity']}: no substitute"})
    # control checks written by hand (not invariants) stay in `checks`
    meta["checks"] = checks + list(meta.get("checks") or [])
    meta["assertion"] = "dialog"


def _validate_plan_fields(case: dict, meta: dict, where: str) -> None:
    """Fields that tie a dialog case to the test plan (HW4)."""
    if "min_turns" in meta:
        n = _n_user(case)
        if not isinstance(meta["min_turns"], int) or meta["min_turns"] > n:
            raise SetError(f"{where}: min_turns {meta['min_turns']!r} is more "
                           f"than the {n} user turns of the case")
    if "plan_role" in meta and meta["plan_role"] not in PLAN_ROLES:
        raise SetError(f"{where}: plan_role must be one of {sorted(PLAN_ROLES)}")
    axes = meta.get("axes")
    if axes is not None:
        if set(axes) != set(AXES) or not all(isinstance(v, int) for v in axes.values()):
            raise SetError(f"{where}: axes needs integer {', '.join(AXES)}")
        if axes["length"] != _n_user(case):
            raise SetError(f"{where}: axes.length={axes['length']} but the case "
                           f"has {_n_user(case)} user turns")
    if meta.get("added_in") == "hw4":
        for key in ("axes", "plan_role", "min_turns"):
            if key not in meta:
                raise SetError(f"{where}: hw4 cases need {key}")
        if meta.get("runs") != 5:
            raise SetError(f"{where}: hw4 cases need runs: 5")
