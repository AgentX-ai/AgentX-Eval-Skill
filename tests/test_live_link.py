#!/usr/bin/env python3
"""Live check: the report link always names the engine that holds the run.

This is the property the harness skeleton's report_host() exists for, proven against a
real engine instead of asserted: a reverse proxy on an ephemeral port stands in for
"the address the user provided", and the link derived under that override must serve
the run through it - while the run itself is verifiably the same row on the engine
behind the proxy.

Read-only by design: it picks an EXISTING run off the engine rather than creating one,
because run rows are permanent and a health check that leaves a dropping per execution
is not a health check. The derivation under test is extracted from the run-brief's own
fenced skeleton - the same bytes real harnesses are generated from - so this cannot
pass while the documented code is wrong.

No engine reachable, no key, or no runs yet -> SKIP with the reason, exit 0. That is
what lets this sit in CI (which has no engine) and still be the one command that
answers "is it right?" on any box that has one:

    python3 tests/test_live_link.py
"""

from __future__ import annotations

import http.server
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIEF = ROOT / "plugins/agentx/skills/run-eval/references/run-brief.md"
DEFAULT_BASE = "http://localhost:4700/api/v1"


def skip(reason: str) -> "NoReturn":  # noqa: F821
    print(f"  SKIP  {reason}")
    sys.exit(0)


def fail(reason: str) -> "NoReturn":  # noqa: F821
    print(f"  FAIL  {reason}")
    sys.exit(1)


def brief_report_host():
    """The documented derivation, executed - not a copy that could drift."""
    fences = re.findall(r"```python\n(.*?)```", BRIEF.read_text(), flags=re.S)
    skeleton = next((f for f in fences if "def report_host()" in f), "")
    if not skeleton:
        fail("run-brief.md no longer defines report_host() in its skeleton")
    src = skeleton[skeleton.index("def report_host()"):]
    src = src[: src.index("\n\n\ndef ")] if "\n\n\ndef " in src else src

    class FakeOS:
        value = None

        def getenv(self, key, default=None):
            return self.value if self.value is not None else default

    fake = FakeOS()
    ns = {"os": fake}
    exec(src, ns)  # noqa: S102 - executing our own documented skeleton is the point

    def derive(base):
        fake.value = base
        return ns["report_host"]()

    return derive


def get(url: str, key: str | None = None, timeout: float = 10):
    headers = {"x-api-key": key} if key else {}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def main() -> int:
    engine_base = (os.environ.get("AGENTX_API_BASE_URL") or DEFAULT_BASE).rstrip("/")
    derive = brief_report_host()
    engine_origin = derive(engine_base)

    # 1. An engine, a key, and at least one existing run - or a clean SKIP.
    try:
        status, raw = get(f"{engine_base}/auth/config", timeout=4)
        auth = json.loads(raw)
    except (urllib.error.URLError, OSError) as e:
        skip(f"no engine at {engine_base} ({getattr(e, 'reason', e)})")
    key = os.environ.get("AGENTX_API_KEY") or auth.get("defaultProjectApiKey") or auth.get("apiKey")
    if not key:
        skip(f"engine at {engine_base} hands out no key (auth enabled) and AGENTX_API_KEY is unset")
    try:
        status, raw = get(f"{engine_base}/custom-agent-evaluations/runs", key)
        runs = json.loads(raw)
        rows = runs.get("runs") if isinstance(runs, dict) else runs
        rows = rows or []
    except (urllib.error.URLError, OSError, ValueError) as e:
        skip(f"cannot list runs on {engine_base} ({e})")
    if not rows:
        skip(f"engine at {engine_base} has no evaluation runs yet - run one, then re-check")
    run_id = rows[0].get("runId") or rows[0].get("_id") or rows[0].get("id")
    if not run_id:
        fail(f"runs list rows carry no runId/_id/id - fields: {sorted(rows[0])}")

    # 2. A proxy on an ephemeral port: the stand-in for a user-provided address.
    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                status, body = get(engine_origin + self.path, key=self.headers.get("x-api-key"), timeout=30)
            except urllib.error.HTTPError as e:
                status, body = e.code, e.read()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    override_base = f"http://127.0.0.1:{port}/api/v1"

    try:
        # 3. The property: derived under the override, the link names the override...
        link = f"{derive(override_base)}/evaluations/{run_id}"
        want_prefix = f"http://127.0.0.1:{port}/evaluations/"
        if not link.startswith(want_prefix):
            fail(f"derivation ignored the override: {link} (want {want_prefix}...)")
        print(f"  ok    link follows the override address: {link}")

        # ...serves the dashboard through it...
        status, _ = get(link)
        if status != 200:
            fail(f"{link} answered {status}")
        print(f"  ok    the link serves through the override (200)")

        # ...and the API through the override shows the SAME run the engine holds.
        status, raw = get(f"{override_base}/custom-agent-evaluations/runs/{run_id}", key)
        via_proxy = json.loads(raw)
        got = via_proxy.get("runId") or via_proxy.get("_id") or via_proxy.get("id") or (via_proxy.get("run") or {}).get("_id")
        status2, raw2 = get(f"{engine_base}/custom-agent-evaluations/runs/{run_id}", key)
        direct = json.loads(raw2)
        got2 = direct.get("runId") or direct.get("_id") or direct.get("id") or (direct.get("run") or {}).get("_id")
        if not (status == status2 == 200 and got == got2):
            fail(f"run mismatch across addresses: proxy={got!r}/{status}, direct={got2!r}/{status2}")
        print(f"  ok    same run on both addresses ({run_id})")
    finally:
        server.shutdown()

    print("\n  the report link and the run name the same engine, under an overridden address")
    return 0


if __name__ == "__main__":
    sys.exit(main())
