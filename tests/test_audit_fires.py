#!/usr/bin/env python3
"""Prove every check in audit_skills.py actually fires.

An audit is a smoke detector: the failure mode that matters is not a false alarm but a
silent one - a regex that stopped matching, a glob that stopped finding, a check that
green-lights everything because its input dried up. audit_skills.py guards its inputs with
sweep floors; this file guards its *checks*, by breaking the repo one way at a time in a
disposable copy and requiring the audit to catch each one by name.

Every case here is either a bug that actually shipped (the first three) or the nearest
neighbour of one. A new check in audit_skills.py is not done until it has a case here.

Run from the repo root, with the same interpreter you run the audit with:

    python3 tests/test_audit_fires.py            # SDK cases skip if agentx is absent
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AUDIT = "tests/audit_skills.py"

INSTRUMENT = "plugins/agentx/skills/instrument"
EVAL = "plugins/agentx/skills/eval-fix"
RUNEVAL = "plugins/agentx/skills/run-eval"

try:
    import agentx  # noqa: F401
    HAVE_SDK = True
except ImportError:
    HAVE_SDK = False


# --------------------------------------------------------------------------------------
# mutations: name -> (needs_sdk, expected message fragment, function that breaks the copy)
# --------------------------------------------------------------------------------------
def edit(root: Path, rel: str, old: str, new: str, count: int = 1) -> None:
    path = root / rel
    text = path.read_text()
    assert text.count(old) >= count, f"cannot stage the break: {old[:50]!r} not in {rel}"
    path.write_text(text.replace(old, new, count))


CASES = {}
def case(name: str, expect: str, needs_sdk: bool = False):
    def register(fn):
        CASES[name] = (needs_sdk, expect, fn)
        return fn
    return register


# -- the three that shipped -------------------------------------------------------------
@case("exec bit stripped", "not executable")
def _(root):  # the original exit 126
    os.chmod(root / INSTRUMENT / "scripts/detect_stack.py", 0o644)

@case("probe under bare python3", "use <project-interpreter>")
def _(root):  # the original exit 4
    edit(root, f"{INSTRUMENT}/references/instrumentation-brief.md",
         "<project-interpreter> <skill>/scripts/verify_trace.py --capabilities",
         "python3 <skill>/scripts/verify_trace.py --capabilities")

@case("guessed import path", "agentx.tracing has no _TraceSpan", needs_sdk=True)
def _(root):  # the original ImportError, staged where an agent would copy it
    edit(root, f"{INSTRUMENT}/SKILL.md", "```bash\npython3 <skill>/scripts/detect_stack.py .\n```",
         "```python\nfrom agentx.tracing import _TraceSpan\n```")

# -- scripts ----------------------------------------------------------------------------
@case("shebang removed", "no shebang")
def _(root):
    p = root / INSTRUMENT / "scripts/agentx_key.py"
    p.write_text(p.read_text().split("\n", 1)[1])

@case("python syntax error", "does not parse")
def _(root):
    p = root / INSTRUMENT / "scripts/verify_trace.py"
    p.write_text(p.read_text() + "\ndef broken(:\n")

@case("script that cannot start", "`--help` exits")
def _(root):  # parses, has the exec bit, dies on import
    p = root / INSTRUMENT / "scripts/detect_stack.py"
    lines = p.read_text().splitlines()
    lines.insert(1, "import module_that_does_not_exist_xyz")
    p.write_text("\n".join(lines))

@case("bash syntax error", "bash -n")
def _(root):
    p = root / EVAL / "scripts/bootstrap.sh"
    p.write_text(p.read_text() + "\nif [ missing_fi ]; then\n  echo broken\n")

# -- documented commands ----------------------------------------------------------------
@case("flag that does not exist", "has no --nonexistent-flag")
def _(root):
    edit(root, f"{INSTRUMENT}/SKILL.md", "python3 <skill>/scripts/detect_stack.py .",
         "python3 <skill>/scripts/detect_stack.py . --nonexistent-flag")

@case("no interpreter named", "names no interpreter")
def _(root):
    edit(root, f"{INSTRUMENT}/SKILL.md", "python3 <skill>/scripts/detect_stack.py .",
         "<skill>/scripts/detect_stack.py .")

@case("project interpreter where python3 does", "python3 is enough")
def _(root):
    edit(root, f"{INSTRUMENT}/SKILL.md", "python3 <skill>/scripts/detect_stack.py .",
         "<project-interpreter> <skill>/scripts/detect_stack.py .")

@case("script that does not exist", "does not exist")
def _(root):
    edit(root, f"{INSTRUMENT}/SKILL.md", "python3 <skill>/scripts/detect_stack.py .",
         "python3 <skill>/scripts/detect_stak.py .")

@case("stale flag in a README", "has no --capabilitees")
def _(root):
    edit(root, "plugins/agentx/README.md", "verify_trace.py --capabilities",
         "verify_trace.py --capabilitees")

# -- the SDK contract -------------------------------------------------------------------
@case("integration class that is not in the SDK", "AgentXImaginaryHandler", needs_sdk=True)
def _(root):
    edit(root, f"{INSTRUMENT}/references/instrumentation-brief.md", "AgentXCallbackHandler(tracer)",
         "AgentXImaginaryHandler(tracer)")

@case("tracer method that does not exist", ".record_everything(), which is on neither", needs_sdk=True)
def _(root):
    edit(root, f"{INSTRUMENT}/references/instrumentation-brief.md", "tracer.flush()",
         "tracer.record_everything()")

@case("trace() keyword not in the signature", "not a parameter", needs_sdk=True)
def _(root):
    edit(root, f"{INSTRUMENT}/references/instrumentation-brief.md",
         'tracer.trace("support-agent", framework="langchain", model="gpt-4o")',
         'tracer.trace("support-agent", grouping="langchain", model="gpt-4o")')

# -- manifests --------------------------------------------------------------------------
@case("version drift between ecosystems", "disagree on the version")
def _(root):
    p = root / "plugins/agentx/.cursor-plugin/plugin.json"
    p.write_text(re.sub(r'"version": "[^"]+"', '"version": "0.0.1"', p.read_text()))

@case("marketplace names a plugin the source does not declare", "declares")
def _(root):
    import json
    p = root / ".claude-plugin/marketplace.json"
    data = json.loads(p.read_text())
    data["plugins"][0]["name"] = "agentx-renamed"   # the entry, not the marketplace's own name
    p.write_text(json.dumps(data, indent=2))

@case(".agents source path broken", "does not exist")
def _(root):  # proves the nested manifest is actually reached
    edit(root, ".agents/plugins/marketplace.json", "./plugins/agentx", "./plugins/gone")

@case("frontmatter without a description", "frontmatter has no description:")
def _(root):
    edit(root, f"{EVAL}/SKILL.md", "description:", "desccription:")

@case("template with a snake_case field", "must be 'expectedResults'")
def _(root):  # the drift that would silently upload cases with no reference answers
    edit(root, f"{RUNEVAL}/templates/tool-use.json", '"expectedResults"', '"expected_results"')

@case("template that is not JSON", "not valid JSON")
def _(root):
    p = root / RUNEVAL / "templates/tool-use.json"
    p.write_text(p.read_text()[:-3])

@case("skeleton report link hardcoded", "hardcoded report link")
def _(root):  # the regression that shipped in four real harnesses before the runtime helper
    edit(root, f"{RUNEVAL}/references/run-brief.md",
         'print(f"report in the browser:  {report_host()}/evaluations/{run.run_id}")',
         'print(f"report in the browser:  http://localhost:4700/evaluations/{run.run_id}")')

@case("skeleton derives by splitting on /api/", "suffix-strip, not split")
def _(root):  # split eats reverse-proxy path prefixes and chokes on api-in-hostname
    edit(root, f"{RUNEVAL}/references/run-brief.md",
         'base[: -len("/api/v1")] if base.endswith("/api/v1") else base',
         'base.split("/api/")[0]')

# -- the skeleton's grading path --------------------------------------------------------
# The SDK renamed the grader keyword under all of these (scorer_id, with
# evaluation_settings_id kept as an alias) and the audit did not notice, because nothing
# reached client.evaluations. Each case is a way that rename could have landed badly.
@case("harness names a grader keyword the SDK dropped", "is not a parameter", needs_sdk=True)
def _(root):  # what a dropped evaluation_settings_id alias looks like from here
    edit(root, f"{RUNEVAL}/references/run-brief.md",
         '**({"evaluation_settings_id": SETTINGS_ID} if SETTINGS_ID else {}),',
         '**({"evaluation_settings": SETTINGS_ID} if SETTINGS_ID else {}),')

@case("harness chains a run method that is gone", "chains .commit()", needs_sdk=True)
def _(root):
    edit(root, f"{RUNEVAL}/references/run-brief.md",
         ".execute(adapter).finalize()", ".execute(adapter).commit()")

@case("harness reads a run field that is gone", "run.mean_rating", needs_sdk=True)
def _(root):
    edit(root, f"{RUNEVAL}/references/run-brief.md", "average_rating", "mean_rating")

@case("harness reads a dataset field that is gone", "not a field of Dataset", needs_sdk=True)
def _(root):
    edit(root, f"{RUNEVAL}/references/run-brief.md", "dataset.questions", "dataset.cases")

@case("harness subject the SDK rejects", "does not validate", needs_sdk=True)
def _(root):  # the framework="langgraph" bug, one field over - pydantic refuses both
    edit(root, f"{RUNEVAL}/references/run-brief.md", '"custom_agent"', '"custom_agnet"')

# -- the audit's own safety net ---------------------------------------------------------
@case("skills directory renamed", "a glob or regex is no longer finding")
def _(root):  # the vacuous-pass case: zero of everything must be a failure, not a pass
    (root / "plugins/agentx/skills").rename(root / "plugins/agentx/skills-renamed")


# --------------------------------------------------------------------------------------
def fresh_copy(dst: Path) -> None:
    shutil.copytree(
        REPO, dst, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"),
    )


def main() -> int:
    parser_args = set(sys.argv[1:])
    failures = []
    ran = skipped = 0

    with tempfile.TemporaryDirectory(prefix="audit-fires-") as tmp:
        for name, (needs_sdk, expect, mutate) in CASES.items():
            if needs_sdk and not HAVE_SDK:
                skipped += 1
                print(f"  skip  {name}  (needs agentx-python)")
                continue
            root = Path(tmp) / re.sub(r"\W+", "-", name)
            fresh_copy(root)
            mutate(root)
            proc = subprocess.run(
                [sys.executable, AUDIT] + (["--skip-sdk"] if not HAVE_SDK else []),
                cwd=root, capture_output=True, text=True, timeout=300,
            )
            output = proc.stdout + proc.stderr
            ran += 1
            if proc.returncode == 0:
                failures.append(f"{name}: the audit PASSED a repo broken this way")
                print(f"  MISS  {name}")
            elif expect not in output:
                failures.append(f"{name}: failed, but not for this reason "
                                f"(wanted {expect!r} in output)")
                print(f"  MISS  {name}  (wrong reason)")
            else:
                print(f"  ok    {name}")

    print(f"\n  {ran} breaks staged, {skipped} skipped"
          + (f", {len(failures)} NOT caught:" if failures else " - every one caught"))
    for f in failures:
        print(f"    ✗ {f}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
