""
import os
import time

import urllib.error
import urllib.request
import json

BASE = os.environ.get("SERVICE_URL", "http://localhost:8000")

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class StandError(RuntimeError):
    pass


def _call(method: str, path: str, payload=None, timeout=120):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with _opener.open(req, timeout=timeout) as r:
            body = r.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise StandError(f"{method} {path} -> {e.code}: "
                         f"{e.read().decode('utf-8')[:200]}") from e
    except urllib.error.URLError as e:
        raise StandError(
            f"cannot reach the stand at {BASE} ({e.reason}). "
            "Start it with `make up` or set SERVICE_URL.") from e



def chat(message: str, session_id: str | None = None,
         customer_id: str | None = None) -> dict:
    return _call("POST", "/chat", {"message": message, "session_id": session_id,
                                   "customer_id": customer_id})


def dialog(turns: list[str], customer_id: str | None = None) -> list[dict]:
    ""
    out, sid = [], None
    for text in turns:
        res = chat(text, sid, customer_id)
        sid = res["session_id"]
        out.append(res)
    return out



def trace(request_id: str) -> dict:
    return _call("GET", f"/api/_test/traces/{request_id}")


def spans(tree: dict):
    yield tree
    for child in tree.get("children", []):
        yield from spans(child)


def tool_calls(tree: dict) -> list[dict]:
    return [{"name": s["name"][5:],
             "arguments": (s.get("attributes") or {}).get("tool.arguments"),
             "result": (s.get("attributes") or {}).get("tool.result")}
            for s in spans(tree) if str(s.get("name", "")).startswith("tool.")]


def usage(tree: dict) -> dict:
    attrs = tree.get("attributes", {})
    return {"input_tokens": attrs.get("gen_ai.usage.total_input_tokens", 0),
            "output_tokens": attrs.get("gen_ai.usage.total_output_tokens", 0)}


def llm_calls(tree: dict) -> list[dict]:
    return [s.get("attributes", {}) for s in spans(tree)
            if s.get("name") == "llm.call"]



def health() -> dict:
    return _call("GET", "/health")


def profile() -> dict:
    return _call("GET", "/api/_test/defects")


def set_profile(name: str | None) -> dict:
    return _call("PUT", "/api/_test/profile", {"profile": name})


def set_defects(ids: str | None) -> dict:
    return _call("PUT", "/api/_test/defects", {"defects": ids})


def prompt_version() -> str:
    return _call("GET", "/api/_test/prompt")["version"]


def state(table: str) -> list[dict]:
    return _call("GET", f"/api/_test/state/{table}")["rows"]


def reset() -> dict:
    return _call("POST", "/api/_test/reset")


def set_clock(iso: str | None) -> dict:
    return _call("POST", "/api/_test/clock", {"now": iso})


def set_summarize_after(steps: int) -> dict:
    ""
    return _call("PUT", "/api/_test/summarize_after", {"steps": int(steps)})


def has_span(tree: dict, name: str) -> bool:
    return any(s.get("name") == name for s in spans(tree))


def wait_until_ready(seconds: int = 60) -> None:
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            health()
            return
        except StandError as e:
            last = e
            time.sleep(2)
    raise StandError(f"stand did not become ready in {seconds}s: {last}")
