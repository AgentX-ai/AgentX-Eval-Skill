#!/usr/bin/env python3
"""Live check: the endpoint the skill picks graders from is the one the engine serves.

agentx-python 0.6.36 consolidated grading configs into an "LLM Judge Scorer" -
client.monitor.judge_scorers, backed by /agent-monitoring/judge-scorers - and the obvious
next move is to modernize this skill onto it. On a current self-host engine that route is
404. The SDK shipped the surface ahead of the engine, and /run-eval is self-host only, so
the modernization would replace a working call with a failing one.

That near-miss is what this file is for. It asserts the endpoint pick_eval.py actually
uses still answers with the shape summarize_settings() reads, and it reports where the
unified surface stands so the day it lands is visible rather than guessed at. The path is
read out of pick_eval.py rather than written down twice, so this cannot pass while the
script points somewhere else.

No engine reachable, no key -> SKIP with the reason, exit 0, same as test_live_link.py.

    python3 tests/test_live_grader_surface.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PICK_EVAL = ROOT / "plugins/agentx/skills/run-eval/scripts/pick_eval.py"
DEFAULT_BASE = "http://localhost:4700/api/v1"
UNIFIED = "/agent-monitoring/judge-scorers"

# What summarize_settings() reads off each row to build the evaluator picker. A row that
# lost these would still list, with every name showing as "(unnamed)".
SUMMARY_FIELDS = ("_id", "name", "isDefault")


def skip(reason: str) -> "NoReturn":  # noqa: F821
    print(f"  SKIP  {reason}")
    sys.exit(0)


def fail(reason: str) -> "NoReturn":  # noqa: F821
    print(f"  FAIL  {reason}")
    sys.exit(1)


def settings_path() -> str:
    """The list endpoint pick_eval.py calls, taken from the script itself."""
    found = re.findall(r'"(/custom-agent-evaluations/evaluation-settings)"', PICK_EVAL.read_text())
    if not found:
        fail("pick_eval.py no longer names /custom-agent-evaluations/evaluation-settings - "
             "if it moved to the unified surface, this check needs to move with it")
    return found[0]


def get(base: str, path: str, key: str | None = None, timeout: float = 15):
    req = urllib.request.Request(base + path, headers={"x-api-key": key} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> int:
    base = (os.environ.get("AGENTX_API_BASE_URL") or DEFAULT_BASE).rstrip("/")
    try:
        status, raw = get(base, "/auth/config", timeout=4)
        auth = json.loads(raw)
    except (urllib.error.URLError, OSError, ValueError) as e:
        skip(f"no engine at {base} ({getattr(e, 'reason', e)})")
    key = os.environ.get("AGENTX_API_KEY") or auth.get("defaultProjectApiKey") or auth.get("apiKey")
    if not key:
        skip(f"engine at {base} hands out no key (auth enabled) and AGENTX_API_KEY is unset")

    # 1. The endpoint the skill uses has to answer, in the shape the picker reads.
    path = settings_path()
    status, raw = get(base, path, key)
    if status != 200:
        fail(f"{path} answered {status} - the evaluator picker cannot list anything. "
             f"If self-host retired it for {UNIFIED}, the skill has to move with it.")
    try:
        rows = json.loads(raw).get("evaluationSettings")
    except ValueError:
        fail(f"{path} answered 200 with something that is not JSON")
    if not isinstance(rows, list):
        fail(f"{path} no longer returns an 'evaluationSettings' list - pick_eval.py reads "
             f"exactly that key, and would show an empty picker")
    print(f"  ok    {path} lists {len(rows)} grading config(s)")

    # Built-ins are seeded per project, which is what lets SKILL.md promise the evaluator
    # picker is never empty. An engine that stopped seeding them makes that a lie.
    if not rows:
        fail("the engine lists zero grading configs - SKILL.md tells the model this list is "
             "never empty because every project ships built-in judges")
    missing = [f for f in SUMMARY_FIELDS if f not in rows[0]]
    if missing:
        fail(f"rows no longer carry {', '.join(missing)} - summarize_settings() reads those, "
             f"and the picker would render every option as '(unnamed)'")
    print(f"  ok    rows carry {', '.join(SUMMARY_FIELDS)}, the fields the picker renders")

    # 2. Where the unified surface stands. Not a failure either way - the skill works on
    #    the legacy endpoint - but the day this turns 200 is the day modernizing is real.
    status, _ = get(base, UNIFIED, key)
    if status == 200:
        print(f"  note  {UNIFIED} is now served (200). The engine has caught up with the SDK: "
              "the skill can move to the unified judge-scorer surface deliberately.")
    else:
        print(f"  note  {UNIFIED} answers {status} on this engine - the SDK has the surface, "
              "self-host does not yet. The legacy endpoint is the only working path.")

    print("\n  the evaluator picker's endpoint is the one this engine serves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
