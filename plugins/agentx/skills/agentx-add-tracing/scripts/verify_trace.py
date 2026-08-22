#!/usr/bin/env python3
"""Prove the wiring works: send one trace and read it back.

Trace delivery is deliberately fire-and-forget - it must never block or break the agent it
is watching - so a misconfigured SDK does not raise. It logs one warning and the traces go
nowhere, which looks exactly like an agent nobody has run yet. Two settings fail that way
in particular:

  - AGENTX_API_BASE_URL unset. The SDK then defaults to the hosted platform, so a self-host
    user's traces leave for api.agentx.so and never appear in the dashboard they are watching.
  - a key from a different project. The traces arrive, and are invisible to every other key.

This script closes that loop before anyone believes the instrumentation: it authenticates,
sends one synchronous trace so a trace_id comes back, and then fetches that id. The trace it
writes is real and will show up in Live Traces, named so it is obvious what it was.

Run it with the SAME interpreter the agent runs under - the one that has agentx-python
installed - e.g. `.venv/bin/python verify_trace.py`.

Usage:
  verify_trace.py [--env .env.agentx] [--name agentx-tracing-smoke]
  verify_trace.py --ping-only        # authenticate, write nothing
  verify_trace.py --capabilities     # what the INSTALLED sdk supports, before writing code
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional


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


def find_bootstrap(root: Path) -> Optional[Path]:
    """Locate the repo's copy of agentx_tracing.py, if it has been placed yet."""
    skip = {".venv", "venv", ".git", "node_modules", "__pycache__", ".tox", "site-packages"}
    for path in sorted(root.rglob("agentx_tracing.py")):
        if not any(part in skip for part in path.parts):
            return path
    return None


def check_bootstrap_parity(path: Path, real_tracer, real_span) -> List[str]:
    """Does the bootstrap's no-op fallback still match the real tracer's surface?

    The whole point of that fallback is that code written against the real tracer keeps
    working when no key is set. Every name the SDK gains and the shim does not is an
    AttributeError waiting inside somebody's agent in exactly the configuration - a
    teammate's first checkout, CI - the fallback exists to protect.

    `current_span` is called out separately because it is the one that fails *without* an
    AttributeError: it is a property on the real Tracer, and a shim that defines it as a
    method returns a truthy bound method instead of None. The LangChain integration reads
    `tracer.current_span` and branches on `is not None`, so a method here sends it down the
    "inside a span" branch to call `_merge_child_run` on a method object.

    Parsed rather than imported: importing the bootstrap constructs a client, and
    --capabilities promises to touch no network.
    """
    import ast

    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError) as exc:
        return [f"could not parse {path}: {exc}"]

    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    problems: List[str] = []

    for shim_name, real in (("_NoopTracer", real_tracer), ("_NoopSpan", real_span)):
        node = classes.get(shim_name)
        if node is None:
            problems.append(f"{path.name} defines no {shim_name}; cannot check parity")
            continue

        defined = {
            b.target.id if isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name) else
            getattr(b, "name", None)
            for b in node.body
        } | {
            t.id for b in node.body if isinstance(b, ast.Assign)
            for t in b.targets if isinstance(t, ast.Name)
        }
        missing = sorted(n for n in dir(real) if not n.startswith("_") and n not in defined)
        if missing:
            problems.append(f"{shim_name} is missing: {', '.join(missing)}")

        for b in node.body:
            if isinstance(b, ast.FunctionDef) and b.name == "current_span":
                is_property = any(
                    isinstance(d, ast.Name) and d.id == "property" for d in b.decorator_list
                )
                if not is_property and isinstance(getattr(real, "current_span", None), property):
                    problems.append(
                        f"{shim_name}.current_span is a method; the real Tracer's is a property, "
                        "so this returns a truthy bound method instead of None"
                    )

    return problems


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

    # The other half of the same question: the SDK moves, and the bootstrap's no-op fallback
    # has to move with it or it stops being a fallback.
    bootstrap = find_bootstrap(Path.cwd())
    if bootstrap is None:
        print("\n  bootstrap: no agentx_tracing.py in this tree yet - Phase 1c copies it in.",
              file=sys.stderr)
        caps["bootstrap"] = None
    else:
        problems = check_bootstrap_parity(bootstrap, Tracer, _TraceSpan)
        caps["bootstrap"] = {"path": str(bootstrap), "problems": problems}
        if problems:
            print(f"\n  bootstrap {bootstrap}: the no-op fallback has drifted from this SDK",
                  file=sys.stderr)
            for problem in problems:
                print(f"    - {problem}", file=sys.stderr)
            print("    Code that works with a key set will raise without one - which is the "
                  "only case the fallback exists for. Re-copy <skill>/assets/agentx_tracing.py.",
                  file=sys.stderr)
        else:
            print(f"\n  bootstrap {bootstrap}: no-op fallback matches this SDK's surface",
                  file=sys.stderr)

    print(json.dumps(caps, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default=".env.agentx", help="env file to load first (default: .env.agentx)")
    ap.add_argument("--name", default="agentx-tracing-smoke", help="name for the smoke trace")
    ap.add_argument("--ping-only", action="store_true", help="authenticate only; do not write a trace")
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

    if args.ping_only:
        return 0

    # sync=True is what makes span.trace_id available: the default queues the trace on a
    # background thread and returns nothing to look up.
    # monitor=False skips every ingest-time check, so a smoke test never spends a judge call.
    with client.tracer.trace(args.name, framework="raw_python", sync=True, monitor=False) as span:
        span.input = {"query": "agentx-add-tracing smoke test"}
        with client.tracer.trace_tool_call("smoke_tool", input="ping") as tool:
            tool.output = "pong"
        span.output = "Tracing is wired correctly."

    trace_id = span.trace_id
    if not trace_id:
        print("error: the trace was sent but the engine returned no id. Nothing was stored.",
              file=sys.stderr)
        return 6
    print(f"sent trace {trace_id}", file=sys.stderr)

    stored = fetch_trace(base_url or "https://api.agentx.so/api/v1", os.environ["AGENTX_API_KEY"], trace_id)
    if not stored:
        print("warning: the trace was accepted but could not be read back. Check that the key "
              "you sent with is the key you are reading with - a trace is visible only to its "
              "own project.", file=sys.stderr)
        return 7

    tools = stored.get("toolCalls") or stored.get("tool_calls") or []
    print(f"read it back: name={stored.get('name')!r} "
          f"latency={stored.get('latencyMs')}ms tool_calls={len(tools)}", file=sys.stderr)
    root = (base_url or "").replace("/api/v1", "")
    if root:
        print(f"it is in Live Traces at {root}", file=sys.stderr)
    print(json.dumps({"trace_id": trace_id, "name": stored.get("name"),
                      "tool_calls": len(tools), "base_url": base_url}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
