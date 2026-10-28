""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

RUBRIC_VERSION = "rubric.v1"

SYSTEM = """You are a quality judge for a bank support agent. You are given a
customer question, the agent's answer, and a rubric. Apply the rubric exactly
as written — do not add criteria of your own, and do not reward or punish
length, politeness or formatting.

Reply with ONE line of JSON and nothing else:
{"verdict": "PASS" | "FAIL", "trigger": "<the rubric clause you applied>",
 "evidence_turn": <int>, "quote": "<verbatim words from the answer>"}

`quote` must be copied from the answer character for character. If nothing in
the answer supports the verdict, use an empty quote."""

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _anthropic(prompt: str, model: str, temperature: float | None) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    body = {"model": model, "max_tokens": 400, "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}]}
    if temperature is not None:
        body["temperature"] = temperature
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"Content-Type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    delay = 2.0
    for attempt in range(4):
        try:
            with _opener.open(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
                return "".join(b.get("text", "") for b in data.get("content", []))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8")[:200]
            if "temperature" in detail and "temperature" in payload.decode("utf-8"):
                body.pop("temperature", None)
                payload = json.dumps(body).encode("utf-8")
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages", data=payload,
                    headers={"Content-Type": "application/json",
                             "x-api-key": key,
                             "anthropic-version": "2023-06-01"})
                continue
            if e.code in (429, 500, 502, 503, 529) and attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"judge {e.code}: {detail}") from e
    raise RuntimeError("judge unreachable")


def _ollama(prompt: str, model: str, temperature: float) -> str:
    ""
    base = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    payload = json.dumps({
        "model": model, "stream": False,
        "options": {"temperature": temperature},
        "system": SYSTEM, "prompt": prompt,
    }).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/api/generate", data=payload,
        headers={"Content-Type": "application/json"})
    with _opener.open(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8")).get("response", "")


def _parse(raw: str) -> dict:
    ""
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        raise ValueError(f"judge returned no JSON: {raw[:160]!r}")
    data = json.loads(match.group(0))
    verdict = str(data.get("verdict", "")).upper()
    if verdict not in ("PASS", "FAIL"):
        raise ValueError(f"judge returned a non-binary verdict: {verdict!r}")
    return {"verdict": verdict, "trigger": data.get("trigger", ""),
            "evidence_turn": data.get("evidence_turn", 0),
            "quote": data.get("quote", "")}


def judge(question: str, answer: str, rubric: str,
          model: str | None = None, temperature: float | None = None) -> dict:
    model = model or os.environ.get("JUDGE_MODEL") or "claude-haiku-5-5"
    prompt = (f"CUSTOMER QUESTION:\n{question}\n\n"
              f"AGENT ANSWER:\n{answer}\n\n"
              f"RUBRIC:\n{rubric}\n\nJSON:")
    raw = (_ollama(prompt, model, temperature)
           if model.startswith("ollama:") or os.environ.get("JUDGE_BACKEND") == "ollama"
           else _anthropic(prompt, model, temperature))
    out = _parse(raw)
    out["judge_model"] = model
    out["rubric_version"] = RUBRIC_VERSION
    return out


def verdict(question: str, answer: str, rubric: str, model: str):
    ""
    result = judge(question, answer, rubric, model)
    detail = (f"{result['verdict']} — {result['trigger']} "
              f"| quote: {result['quote'][:80]!r}")
    return result["verdict"] == "PASS", detail


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(judge(
        "What is the dispute window for a duplicate charge?",
        "The window is 60 days from the transaction date.",
        "PASS when the answer states 60 days. FAIL otherwise."),
        ensure_ascii=False, indent=2))
