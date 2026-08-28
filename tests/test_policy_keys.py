#!/usr/bin/env python3
"""Hold skillevaluator-policy.yaml against a live scan.

The policy overlay moves three findings off the blocking path. Every entry is an accepted
risk, and an accepted risk is only honest while it still describes exactly what was
reviewed. Three ways it rots, all silent:

  - **A key stops matching.** SkillSpector embeds line numbers in its taint-flow check
    names - `... from os.environ.get (line 77, ...)`. Insert a line above `get()` in
    pick_eval.py and the finding returns under a new name, unsuppressed, and the build
    goes red for a reason unrelated to the change. The fix is to re-key, but only if
    someone can tell that is what happened.

  - **A key outlives its finding.** The code is fixed, the entry stays, and the repo
    carries a suppression for a risk it no longer takes.

  - **A key covers more than it was meant to.** A severity override is keyed by RULE,
    not by file, so downgrading `Credential Access (PE3)` downgrades it in files nobody
    reviewed - including ones added later. This is a property of the mechanism rather
    than a failure anyone has caught in the act; an attempt to demonstrate it was
    inconclusive because the scanner did not flag the planted file at all, reporting a
    per-file `runtime_limit` instead. A scanner whose reach is uneven is a reason to
    bound the acceptance explicitly, not to lean on it.

So all three are checked: every accepted key must still be produced, every gating finding
must be fixed or accepted, and every accepted finding must occur only in the files
`accepted_sites` names.

No skillevaluator on PATH -> SKIP with the reason, exit 0, same as test_live_link.py.

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


def parse_policy() -> tuple[set[str], dict[str, set[str]]]:
    """The overridden keys, and the files each is allowed to appear in.

    Parsed by hand rather than with PyYAML: this has to run on a bare CI Python on 3.9,
    and what it reads is two flat blocks of quoted keys.
    """
    if not POLICY.is_file():
        skip(f"no policy file at {POLICY.relative_to(ROOT)}")

    keys: set[str] = set()
    sites: dict[str, set[str]] = {}
    block = None
    current: str | None = None

    for line in POLICY.read_text().splitlines():
        if line.startswith("severity_overrides:"):
            block, current = "sev", None
            continue
        if line.startswith("accepted_sites:"):
            block, current = "sites", None
            continue
        if line and not line[0].isspace():          # any other top-level key ends a block
            block, current = None, None
            continue
        if block == "sev":
            m = re.match(r'\s+"(.+)":\s*\S+\s*$', line)
            if m:
                keys.add(m.group(1))
        elif block == "sites":
            m = re.match(r'\s+"(.+)":\s*$', line)
            if m:
                current = m.group(1)
                sites.setdefault(current, set())
                continue
            m = re.match(r"\s+-\s+(\S+)\s*$", line)
            if m and current:
                sites[current].add(m.group(1))
    return keys, sites


def normalize_path(raw) -> str:
    """`[eval-fix] references/eval-brief.md` -> `skills/eval-fix/references/eval-brief.md`.

    The reports carry the owning skill in a bracketed prefix and the path relative to that
    skill. accepted_sites is written plugin-relative so it reads the way the tree looks, so
    one of the two has to be converted. Getting this wrong is not loud: an unparsed path
    yields an empty file set, every acceptance then trivially satisfies its site list, and
    the check passes while enforcing nothing - which is exactly what it did on first write.
    """
    if not isinstance(raw, str) or not raw.strip():
        return ""
    raw = raw.strip()
    m = re.match(r"\[([^\]]+)\]\s*(.+)$", raw)
    if m:
        return f"skills/{m.group(1)}/{m.group(2)}".split(":")[0]
    return raw.split(":")[0].lstrip("./")


def live_gating_findings() -> dict[str, set[str]]:
    """CRITICAL/HIGH check names from a scan run WITHOUT the policy, to the files they hit.

    Run from a scratch cwd: skillevaluator writes its HTML+JSON pair to ./reports, and a
    test that dirties the tree it is validating is a bug this repo has already fixed once
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

    found: dict[str, set[str]] = {}
    for f in walk(data):
        if str(f.get("severity", "")).lower() not in GATING:
            continue
        key = f"{f.get('category')}.{f.get('check_name') or f.get('check')}"
        found.setdefault(key, set())
        where = normalize_path(f.get("file_path"))
        if where:
            found[key].add(where)
    return found


def main() -> None:
    declared, allowed_sites = parse_policy()
    live = live_gating_findings()
    failures = 0

    for key in sorted(declared - set(live)):
        failures += 1
        print(f"  FAIL  stale entry - delete it or re-key it:\n          {key}")

    for key in sorted(set(live) - declared):
        failures += 1
        print(f"  FAIL  gating finding neither fixed nor accepted:\n          {key}")

    for key in sorted(declared & set(live)):
        allowed = allowed_sites.get(key)
        if allowed is None:
            failures += 1
            print(f"  FAIL  accepted with no accepted_sites entry - it would cover any file:"
                  f"\n          {key}")
            continue
        if not live[key]:
            failures += 1
            print(f"  FAIL  no file could be parsed for this finding, so accepted_sites "
                  f"enforces nothing:\n          {key}")
            continue
        strayed = sorted(f for f in live[key] if f and f not in allowed)
        if strayed:
            failures += 1
            print(f"  FAIL  {key.split('.', 1)[1][:60]}\n"
                  f"          accepted for {sorted(allowed)}\n"
                  f"          but now also fires in {strayed} - review it, do not widen "
                  f"the list reflexively")
            continue
        print(f"  ok    accepted, still real, seen only in {sorted(live[key])}")

    if failures:
        print(f"\n  {failures} policy/scan disagreement(s) - the overlay no longer "
              f"describes this repo")
        sys.exit(1)

    print(f"\n  {len(declared)} accepted finding(s), every one still produced, none of them "
          f"reaching a file outside the list it was reviewed for")


if __name__ == "__main__":
    main()
