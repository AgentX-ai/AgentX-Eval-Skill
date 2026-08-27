#!/usr/bin/env python3
"""Hold skillevaluator-policy.yaml against a live scan.

The policy overlay moves three findings off the blocking path. Every entry in it is an
accepted risk, and an accepted risk is only honest while it still describes something
real. Two ways it rots, both silent:

  - **A key stops matching.** SkillSpector embeds line numbers in its taint-flow check
    names - `... from os.environ.get (line 90, ...)`. Insert a line above `get()` in
    pick_eval.py and the finding comes back under a new name, unsuppressed, and the
    build goes red for a reason that has nothing to do with the change. The fix is to
    re-key the policy, but only if someone can tell that is what happened.

  - **A key outlives its finding.** The code is fixed, the entry stays, and the repo
    now carries a suppression for a risk it no longer takes - which is how a policy
    file turns into a place blocking findings go to disappear.

So: every policy key must match a finding the scanner still produces, and every
CRITICAL/HIGH finding the scanner produces must be either fixed or listed. Neither
direction is allowed to drift.

No skillevaluator on PATH -> SKIP with the reason, exit 0, same as test_live_link.py.
CI installs it where the full toolchain is available.

    python3 tests/test_policy_keys.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "skillevaluator-policy.yaml"
PLUGIN = ROOT / "plugins/agentx"

GATING = ("critical", "high")


def skip(reason: str) -> None:
    print(f"  SKIP  {reason}")
    sys.exit(0)


def policy_keys() -> set[str]:
    """The quoted keys under severity_overrides.

    Parsed with a regex rather than PyYAML: this file has to run on a bare CI Python
    on 3.9, and the one thing it reads is a flat block of quoted string keys.
    """
    if not POLICY.is_file():
        skip(f"no policy file at {POLICY.relative_to(ROOT)}")
    keys = set()
    in_block = False
    for line in POLICY.read_text().splitlines():
        if line.startswith("severity_overrides:"):
            in_block = True
            continue
        if in_block:
            if line and not line[0].isspace():
                break
            m = re.match(r'\s+"(.+)":\s*\S+\s*$', line)
            if m:
                keys.add(m.group(1))
    return keys


def live_gating_findings() -> set[str]:
    """CRITICAL/HIGH check names from a scan run WITHOUT the policy.

    Run from a scratch cwd: skillevaluator writes its HTML+JSON pair to ./reports,
    and a test that dirties the tree it is validating is the bug this repo just fixed
    in its own suite.
    """
    exe = shutil.which("skillevaluator")
    if not exe:
        skip("skillevaluator not on PATH (uv tool install skillevaluator)")

    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [exe, "validate", str(PLUGIN), "--type", "plugin", "--no-dedup"],
            cwd=tmp, capture_output=True, text=True, timeout=900,
        )
        reports = sorted((Path(tmp) / "reports").glob("*.json"), key=os.path.getmtime)
        if not reports:
            skip(f"scan produced no JSON report (exit {proc.returncode})")
        data = json.loads(reports[-1].read_text())

    def walk(obj):
        if isinstance(obj, dict):
            if "severity" in obj and ("check_name" in obj or "check" in obj):
                yield obj
            for v in obj.values():
                yield from walk(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from walk(v)

    return {
        f"{f.get('category')}.{f.get('check_name') or f.get('check')}"
        for f in walk(data)
        if str(f.get("severity", "")).lower() in GATING
    }


def main() -> None:
    declared = policy_keys()
    live = live_gating_findings()

    failures = []

    stale = sorted(declared - live)
    for key in stale:
        failures.append(f"policy key matches nothing the scanner reports: {key}")
        print(f"  FAIL  stale entry - delete it or re-key it:\n          {key}")

    unlisted = sorted(live - declared)
    for key in unlisted:
        failures.append(f"gating finding is neither fixed nor accepted: {key}")
        print(f"  FAIL  unaccepted {key.split('.', 1)[0]} finding:\n          {key}")

    for key in sorted(declared & live):
        print(f"  ok    accepted, and still real: {key[:88]}")

    if failures:
        print(f"\n  {len(failures)} policy/scan disagreement(s) - the overlay no longer "
              f"describes this repo")
        sys.exit(1)

    print(f"\n  {len(declared)} accepted finding(s), every one still produced by the scanner, "
          f"and nothing gating left over")


if __name__ == "__main__":
    main()
