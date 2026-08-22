#!/usr/bin/env python3
"""Prove the wiring works, without leaving anything behind in the project.

Trace delivery is deliberately fire-and-forget - it must never block or break the agent it
is watching - so a misconfigured SDK does not raise. It logs one warning and the traces go
nowhere, which looks exactly like an agent nobody has run yet. Three settings fail that way:

  - AGENTX_API_BASE_URL unset. The SDK then defaults to the hosted platform, so a self-host
    user's traces leave for api.agentx.so and never appear in the dashboard they are watching.
  - a key from a different project. The traces arrive, and are invisible to every other key.
  - a process that exits before the queue drains. Delivery runs on a background daemon thread
    with no atexit hook, so a CLI can finish with its traces still queued.

Two halves, because they answer different questions.

**The connection** (no arguments) authenticates against the engine and reads the project back.
That is what proves the key, the base URL and the engine - the three settings above - and it is
worth doing before any code is written, because every one of them fails silently afterwards. It
**writes nothing**: an engine has no route to delete a trace, so anything sent to prove a point
stays in that project next to the agent's real traffic forever.

**The check** (`--check <agent-name>`) is the other half, and the write path with it: run the
agent for real, then read back what it produced and hold it against what a usable trace looks
like - one trace per run, question and answer in `input`/`output`, token counts present, tool
calls recorded, turns of a conversation sharing a session. That is what proves the
*instrumentation*, and it is the step people skip, because a diff that looks right and a
dashboard that stays empty feel unrelated.

`--self-test` sends three synthetic traces instead - a plain span, a span with a tool call, and
a second turn sharing the first's session - and fetches each back by id. It is for the case where
the agent cannot easily be run (no model key to hand, a server that needs a request) and for
debugging a pipeline where nothing arrives at all. **Those three traces are permanent**, so it is
opt-in rather than the default.

Run it with the SAME interpreter the agent runs under - the one that has agentx-python
installed - e.g. `.venv/bin/python verify_trace.py`.

Usage:
  verify_trace.py [--env .env.agentx]      # authenticate and read; writes nothing
  verify_trace.py --check support-desk     # grade the traces the real agent just produced
  verify_trace.py --self-test              # send three synthetic traces; they cannot be deleted
  verify_trace.py --capabilities           # what the INSTALLED sdk supports, before writing code
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import List


def load_env_file(path: Path) -> dict:
    """Minimal .env reader - no dependency, and deferring to a real environment variable.

    Deference matters: in production the platform sets AGENTX_API_KEY itself, and a local
    file that overrode it would quietly redirect production traces to a developer's project.
    """
    loaded = {}
    if not path.is_file():
        return loaded
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


def fetch_trace(base_url: str, api_key: str, trace_id: str) -> dict | None:
    req = urllib.request.Request(f"{base_url.rstrip('/')}/ingest/traces/{trace_id}")
    req.add_header("x-api-key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, json.JSONDecodeError):
        return None


def list_traces(base_url: str, api_key: str, limit: int = 25) -> List[dict]:
    """Recent traces for whichever project this key belongs to."""
    req = urllib.request.Request(f"{base_url.rstrip('/')}/ingest/traces?limit={limit}")
    req.add_header("x-api-key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, json.JSONDecodeError):
        return []
    if isinstance(body, list):
        return body
    for field in ("traces", "data", "items", "results"):
        if isinstance(body.get(field), list):
            return body[field]
    return []


def looks_like_prose(value) -> bool:
    """Is this the question/answer a judge can read, or the envelope it arrived in?

    `input` holding `{'messages': [...]}` or a serialised Request is the single most common
    way a trace turns out worthless later: it renders, it looks instrumented, and no
    evaluation can score it because the question is not in it.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    head = value.lstrip()[:1]
    return head not in ("{", "[", "<")


PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def grade(traces: List[dict]) -> List[tuple]:
    """Hold real traces against what a usable one looks like. Returns (status, label, detail).

    These are the Phase 6 questions, asked by a script instead of by eye, because every one of
    them has a specific cause worth naming when it fails.

    Three states, not two, because two of these questions cannot be answered from the traces
    alone. An agent with no tools *should* record no tool calls, and four independent one-shot
    runs *should* each have their own session - both identical, in the data, to the wiring
    being broken. Calling those FAIL would train people to ignore a red line; calling them
    PASS would hide a real defect. WARN says what was seen and leaves the judgement where it
    belongs, and only FAIL moves the exit code.
    """
    checks: List[tuple] = []
    if not traces:
        return [(FAIL, "traces registered", "none found - see the three silent failures above")]

    checks.append((PASS, "traces registered", f"{len(traces)} found"))

    readable = [t for t in traces if looks_like_prose(t.get("input")) and looks_like_prose(t.get("output"))]
    checks.append((
        PASS if len(readable) == len(traces) else FAIL,
        "input/output are the question and the answer",
        f"{len(readable)}/{len(traces)} readable" if readable
        else "a serialised object, not prose - set span.input/span.output yourself",
    ))

    tokened = [t for t in traces if (t.get("inputTokens") or 0) or (t.get("outputTokens") or 0)]
    checks.append((
        PASS if tokened else FAIL,
        "token counts present",
        f"{len(tokened)}/{len(traces)} priced" if tokened
        else "the model client is not auto-instrumented (Phase 3)",
    ))

    tooled = [t for t in traces if (t.get("toolCalls") or [])]
    names = sorted({c.get("name") for t in tooled for c in (t.get("toolCalls") or []) if c.get("name")})
    checks.append((
        PASS if tooled else WARN,
        "tool calls recorded",
        ", ".join(names[:5]) + (" ..." if len(names) > 5 else "") if tooled
        else "none recorded - right if the agent calls no tools, otherwise Phase 3 or Phase 4",
    ))

    sessions = {t.get("sessionId") for t in traces if t.get("sessionId")}
    if len(traces) == 1:
        status, detail = WARN, "only one trace - nothing to group yet"
    elif not sessions:
        status, detail = FAIL, "no session id on any trace - pass session_id to the span (Phase 2)"
    elif len(sessions) == len(traces):
        status, detail = WARN, (f"{len(traces)} traces, {len(sessions)} sessions - every run its own "
                                "session. Right for one-shot runs, wrong for a conversation")
    else:
        status, detail = PASS, f"{len(traces)} turns in {len(sessions)} session(s)"
    checks.append((status, "session grouping", detail))
    return checks


def report_capabilities() -> int:
    """What the installed agentx-python actually supports.

    Worth one command before generating any code against it. The published package and the
    docs do not always agree: 0.6.30 on PyPI documents `span.add_tool_call(..., success=...)`
    but its `add_tool_call` takes only name/input/output/latency_ms, so generated code using
    those keywords raises TypeError inside the user's agent at the first failed tool call.
    `tracer.trace_tool_call(...)` records the same success/error fields on every version that
    has it, which is why the brief prefers it.
    """
    import inspect

    try:
        from agentx.tracing.tracer import Tracer, _TraceSpan
        from agentx.version import VERSION
    except ImportError:
        print(f"error: agentx-python is not installed in {sys.executable}\n"
              f"  pip install agentx-python", file=sys.stderr)
        return 4

    def accepts(fn, keyword: str) -> bool:
        try:
            return keyword in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return False

    caps = {
        "version": VERSION,
        "interpreter": sys.executable,
        "tracer.trace_tool_call": hasattr(Tracer, "trace_tool_call"),
        "tracer.record_tool_call": hasattr(Tracer, "record_tool_call"),
        "tracer.use_span": hasattr(Tracer, "use_span"),
        "trace(sync=)": accepts(Tracer.trace, "sync"),
        "trace(monitor=)": accepts(Tracer.trace, "monitor"),
        "trace(agent_id=)": accepts(Tracer.trace, "agent_id"),
        "add_tool_call(success=)": accepts(_TraceSpan.add_tool_call, "success"),
        "add_tool_call(error=)": accepts(_TraceSpan.add_tool_call, "error"),
    }
    for key, value in caps.items():
        print(f"  {key:26} {value}", file=sys.stderr)
    if not caps["add_tool_call(success=)"]:
        print("\n  This version's add_tool_call() takes no success=/error=. Use "
              "tracer.trace_tool_call(...) to record a failed tool call.", file=sys.stderr)

    print(json.dumps(caps, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default=".env.agentx", help="env file to load first (default: .env.agentx)")
    ap.add_argument("--name", default="instrument-check", help="name prefix for --self-test traces")
    ap.add_argument("--check", metavar="AGENT",
                    help="grade the traces this agent already produced instead of sending any")
    ap.add_argument("--limit", type=int, default=25,
                    help="how many recent traces --check looks through (default 25)")
    ap.add_argument("--self-test", action="store_true",
                    help="send three synthetic traces to prove ingestion. They are permanent - "
                         "the engine has no delete route - so prefer --check on a real run")
    ap.add_argument("--capabilities", action="store_true",
                    help="report the installed SDK's tracing surface and exit; touches no network")
    args = ap.parse_args()

    if args.capabilities:
        return report_capabilities()

    env_path = Path(args.env)
    loaded = load_env_file(env_path)
    print(f"env: {env_path} ({'loaded ' + str(len(loaded)) + ' keys' if loaded else 'not found'})",
          file=sys.stderr)

    base_url = os.getenv("AGENTX_API_BASE_URL")
    if not base_url:
        print("AGENTX_API_BASE_URL is unset. The SDK will send to the hosted platform "
              "(https://api.agentx.so) by default - which is wrong for a self-host engine, "
              "and fails silently.", file=sys.stderr)
    if not os.getenv("AGENTX_API_KEY"):
        print("error: AGENTX_API_KEY is unset and no env file supplied one.", file=sys.stderr)
        return 3

    try:
        from agentx import AgentX
    except ImportError:
        print("error: agentx-python is not installed in this interpreter "
              f"({sys.executable}).\n  pip install agentx-python", file=sys.stderr)
        return 4

    client = AgentX.from_env()
    try:
        ok = client.ping()
    except Exception as exc:  # the SDK raises typed errors whose messages already name the cause
        print(f"error: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 5
    print(f"authenticated against {ok.get('base_url')}", file=sys.stderr)

    read_url = base_url or "https://api.agentx.so/api/v1"
    api_key = os.environ["AGENTX_API_KEY"]
    root = (base_url or "").replace("/api/v1", "")

    # ---- the check: grade what the agent itself produced -------------------------------
    if args.check:
        traces = [t for t in list_traces(read_url, api_key, limit=args.limit)
                  if t.get("name") == args.check]
        print(f"\n{args.check!r} - what the agent's own runs recorded:", file=sys.stderr)
        checks = grade(traces)
        for status, label, detail in checks:
            print(f"  {status}  {label:44} {detail}", file=sys.stderr)
        if root:
            print(f"\n  they are in Live Traces at {root}", file=sys.stderr)
        failed = [c for c in checks if c[0] == FAIL]
        warned = [c for c in checks if c[0] == WARN]
        if warned and not failed:
            print("\n  The WARNs are not failures - they are the two questions the traces cannot "
                  "answer on their own. Read them against what this agent is meant to do.",
                  file=sys.stderr)
        print(json.dumps({
            "agent": args.check,
            "traces": len(traces),
            "checks": [{"status": s, "check": label, "detail": detail} for s, label, detail in checks],
        }, indent=2))
        return 0 if not failed else 8

    # ---- the connection, and nothing more, unless --self-test was asked for -------------
    # Anything sent here is permanent: the engine serves no DELETE for a trace, so a synthetic
    # one sits in Live Traces beside the agent's real traffic for good. The write path gets
    # proven a few minutes later by --check, on traces that belong in the project anyway.
    if not args.self_test:
        recent = list_traces(read_url, api_key, limit=args.limit)
        names = sorted({t.get("name") for t in recent if t.get("name")})
        print(f"\n  reachable, and this key can read its project: {len(recent)} recent trace(s)"
              + (f", from {', '.join(names[:4])}" if names else " - none recorded yet"),
              file=sys.stderr)
        if root:
            print(f"  Live Traces: {root}", file=sys.stderr)
        print("\n  Key, base URL and engine are proven. Nothing was written. That says nothing "
              "about the agent's own code:\n  run the agent, then verify_trace.py --check "
              "<agent-name>.", file=sys.stderr)
        print(json.dumps({"base_url": base_url, "reachable": True,
                          "recent_traces": len(recent), "wrote": False}, indent=2))
        return 0

    # ---- --self-test: three traces that exercise what a run needs -----------------------
    # sync=True is what makes span.trace_id available: the default queues the trace on a
    # background thread and returns nothing to look up.
    # monitor=False skips every ingest-time check, so a self-test never spends a judge call.
    session = f"{args.name}-session"
    sent: List[tuple] = []

    with client.tracer.trace(f"{args.name}-1-span", framework="raw_python",
                             sync=True, monitor=False) as span:
        span.input = "Does a plain span register?"
        span.output = "Yes - this trace is the evidence."
    sent.append(("plain span", span.trace_id, lambda t: True, ""))

    with client.tracer.trace(f"{args.name}-2-tools", framework="raw_python",
                             sync=True, monitor=False, session_id=session) as span:
        span.input = "Does a tool call nest inside it?"
        with client.tracer.trace_tool_call("smoke_tool", input="ping") as tool:
            tool.output = "pong"
        span.output = "Yes - smoke_tool is recorded on this trace."
    sent.append(("tool call recorded", span.trace_id,
                 lambda t: bool(t.get("toolCalls")), "no toolCalls on the stored trace"))

    with client.tracer.trace(f"{args.name}-3-session", framework="raw_python",
                             sync=True, monitor=False, session_id=session) as span:
        span.input = "Does a second turn join the first one's session?"
        span.output = "Yes - both turns carry the same session id."
    sent.append(("session grouping", span.trace_id,
                 lambda t: t.get("sessionId") == session, "sessionId did not survive the round trip"))

    print(f"\nself-test - three traces sent to {read_url}. They stay in this project; the "
          f"engine has no route to delete them:", file=sys.stderr)
    failures = 0
    results = []
    for label, trace_id, predicate, why in sent:
        if not trace_id:
            print(f"  FAIL  {label:24} the engine returned no id; nothing was stored", file=sys.stderr)
            failures += 1
            results.append({"check": label, "ok": False, "detail": "no trace id returned"})
            continue
        stored = fetch_trace(read_url, api_key, trace_id)
        if not stored:
            print(f"  FAIL  {label:24} sent as {trace_id}, but could not be read back - the key "
                  "you sent with may not be the key you are reading with", file=sys.stderr)
            failures += 1
            results.append({"check": label, "ok": False, "trace_id": trace_id,
                            "detail": "accepted but not readable with this key"})
            continue
        ok = predicate(stored)
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label:24} {trace_id}"
              f"{'' if ok else '  <- ' + why}", file=sys.stderr)
        results.append({"check": label, "ok": ok, "trace_id": trace_id,
                        "name": stored.get("name")})

    if root:
        print(f"\n  they are in Live Traces at {root}", file=sys.stderr)
    if failures:
        print("\n  The wiring is not proven. Until this passes, nothing the agent records will "
              "arrive either.", file=sys.stderr)
    else:
        print("\n  Wiring proven. That covers the key, the base URL and the engine - not the "
              f"agent's own code. Run the agent, then: verify_trace.py --check <agent-name>",
              file=sys.stderr)
    print(json.dumps({"base_url": base_url, "checks": results}, indent=2))
    return 0 if not failures else 7


if __name__ == "__main__":
    sys.exit(main())
