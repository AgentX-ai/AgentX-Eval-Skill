#!/usr/bin/env python3
"""Offline regression battery for the run-eval helper scripts.

Every case here started life as a real traceback or a silent wrong-mode fall-through,
found by feeding the scripts what users actually produce: empty variable expansions,
Excel CSVs with a BOM, a base URL missing /api/v1, JSON of the wrong shape.

Hermetic by construction: the environment points AGENTX_API_BASE_URL at a port nothing
listens on, so any case that unexpectedly reaches for the network dies with exit 2 and
fails its assertion - the guard and the offline-ness are the same mechanism.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins/agentx/skills/run-eval/scripts"
ENV = {
    "AGENTX_API_KEY": "dummy-offline-key",
    "AGENTX_API_BASE_URL": "http://127.0.0.1:1/api/v1",   # nothing listens here
    "PATH": "/usr/bin:/bin",
}

failures = []


def run(script, *argv):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *argv],
        capture_output=True, text=True, env=ENV, timeout=30,
    )


def case(desc, proc, want_exit, want_text):
    blob = proc.stdout + proc.stderr
    ok = proc.returncode == want_exit and want_text in blob
    print(f"  {'ok   ' if ok else 'FAIL '} {desc}")
    if not ok:
        failures.append(f"{desc}: exit {proc.returncode} (want {want_exit}); output: {blob[:200]!r}")


SHAPES = {
    None:                                  "http://localhost:4700",       # unset -> default
    "http://localhost:4700/api/v1":        "http://localhost:4700",       # standard self-host
    "http://10.0.0.5:4700/api/v1":         "http://10.0.0.5:4700",        # user-provided box
    "https://traces.example.com/api/v1":   "https://traces.example.com",  # reverse proxy on 443
    "https://example.com/agentx/api/v1/":  "https://example.com/agentx",  # path prefix + trailing /
    "http://api.mycorp:4700/api/v1":       "http://api.mycorp:4700",      # 'api' in the hostname
    "https://api.agentx.so":               "https://api.agentx.so",       # no suffix -> untouched
}


def check_brief_report_host():
    """Execute the report_host() the brief actually documents - not a copy of it - so the
    doc and the behavior cannot drift apart. The skeleton is the code four real harnesses
    were generated from; when it was a resolve-at-generation placeholder, all four shipped
    with a stale literal."""
    import re
    brief = ROOT / "plugins/agentx/skills/run-eval/references/run-brief.md"
    fences = re.findall(r"```python\n(.*?)```", brief.read_text(), flags=re.S)
    skeleton = next((f for f in fences if "def report_host()" in f), "")
    if not skeleton:
        failures.append("run-brief.md: no fenced skeleton defines report_host()")
        print("  FAIL  brief skeleton defines report_host()")
        return
    src = skeleton[skeleton.index("def report_host()"):]
    src = src[: src.index("\n\n\ndef ")] if "\n\n\ndef " in src else src

    class FakeOS:  # the skeleton reads os.getenv; feed it each shape
        def __init__(self):
            self.value = None
        def getenv(self, key, default=None):
            return self.value if self.value is not None else default

    fake = FakeOS()
    ns = {"os": fake}
    exec(src, ns)  # noqa: S102 - executing our own documented skeleton is the point
    for base, want in SHAPES.items():
        fake.value = base
        got = ns["report_host"]()
        ok = got == want
        print(f"  {'ok   ' if ok else 'FAIL '} brief report_host({base!r}) -> {got}")
        if not ok:
            failures.append(f"brief report_host({base!r}) = {got!r}, want {want!r}")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "list.json").write_text("[1,2]")
        (tmp / "broken.json").write_text("{broken")
        (tmp / "bom.csv").write_bytes(b"\xef\xbb\xbfquery,expected_results\nhello,world\n")

        case("empty --validate-dataset refused, not a silent list",
             run("pick_eval.py", "--validate-dataset", ""),
             1, "empty id")
        case("empty --validate-settings refused",
             run("pick_eval.py", "--validate-settings", ""),
             1, "empty id")
        case("--from-json list dies cleanly, no traceback",
             run("make_dataset.py", "--from-json", str(tmp / "list.json")),
             1, "JSON OBJECT")
        case("--from-json invalid JSON dies cleanly",
             run("make_dataset.py", "--from-json", str(tmp / "broken.json")),
             1, "not valid JSON")
        case("Excel-BOM CSV parses (dry-run prints the payload)",
             run("make_dataset.py", "--from-csv", str(tmp / "bom.csv"), "--name", "BOM probe", "--dry-run"),
             0, '"query": "hello"')
        case("template name traversal refused",
             run("make_dataset.py", "--template", "../../eval-fix/SKILL", "--dry-run"),
             1, "bare names")
        case("empty --add-case value refused",
             run("make_dataset.py", "--add-case", "", "--query", "q", "--trace-id", "t"),
             1, "empty value")
        case("empty --create-settings value refused",
             run("make_dataset.py", "--create-settings", "", "--acceptance", "x"),
             1, "empty value")
        case("shipped template dry-run needs no engine",
             run("make_dataset.py", "--template", "tool-use", "--dry-run"),
             0, "Template: Tool Use")
        # The offline guard proving itself: a call that DOES need the engine dies with
        # exit 2 and names the unreachable address, rather than hanging or lying.
        case("network-needing call fails loudly against the dead port",
             run("pick_eval.py"),
             2, "cannot reach")

    check_brief_report_host()

    if failures:
        print(f"\n  {len(failures)} failure(s):")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("\n  all offline regressions hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
