#!/usr/bin/env python3
"""Hold the skills' documentation against the code it tells an agent to run.

Everything here exists because it has already gone wrong once. A skill is prose that a model
executes literally, so a stale flag or a guessed module path is not a typo - it is a command
that fails in front of a user, in the first thirty seconds of the skill, and reads as "this
thing is broken" long before anyone looks at what the error said.

The three that prompted this file:

  - `<skill>/scripts/detect_stack.py .` invoked exec-style while the file shipped 100644.
    Exit 126, permission denied, on the skill's very first command.
  - the capabilities probe documented as `python3 ...` when it imports agentx and therefore
    needs the interpreter the agent's project runs under. Exit 4.
  - `from agentx.tracing import _TraceSpan`, guessed by an agent that wanted to read the SDK.
    It lives in `agentx.tracing.tracer`. ImportError.

Checks that need the SDK are skipped, loudly, when agentx-python is not installed, so this
runs anywhere; CI installs it so they actually run. Run it from the repo root:

    python3 tests/audit_skills.py
"""
from __future__ import annotations

import argparse
import ast
import importlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "plugins/agentx/skills"

problems: list[str] = []
notes: list[str] = []


def fail(where: str, msg: str) -> None:
    problems.append(f"{where}: {msg}")


# --------------------------------------------------------------------------------------
# 1. The scripts themselves
# --------------------------------------------------------------------------------------
def script_files() -> list[Path]:
    return sorted(p for p in SKILLS.rglob("scripts/*") if p.suffix in (".py", ".sh"))


def check_scripts() -> None:
    for path in script_files():
        rel = path.relative_to(ROOT)
        first = path.read_text().splitlines()[0] if path.read_text() else ""
        if not first.startswith("#!"):
            fail(str(rel), "no shebang, but it lives in scripts/ and the docs invoke it")
        # A shebang is a promise that the file can be run. 100644 breaks that promise with
        # exit 126, and the docs have no way to know.
        if not path.stat().st_mode & 0o111:
            fail(str(rel), "has a shebang but is not executable (chmod +x)")
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text())
            except SyntaxError as exc:
                fail(str(rel), f"does not parse: {exc}")


def check_scripts_run() -> None:
    """`--help` has to work under the running interpreter.

    It is the cheapest proof that the file imports at all and that its argparse is well
    formed - and, run on the oldest Python the README promises, that the syntax is within
    reach of it. None of the three helpers touch the network or the SDK to print help.
    """
    import subprocess
    for path in script_files():
        if path.suffix != ".py":
            continue
        proc = subprocess.run([sys.executable, str(path), "--help"],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            first = (proc.stderr.strip().splitlines() or ["no stderr"])[-1]
            fail(str(path.relative_to(ROOT)),
                 f"`--help` exits {proc.returncode} on {sys.version_info.major}."
                 f"{sys.version_info.minor}: {first[:90]}")


def argparse_flags(path: Path) -> set[str]:
    """Every --flag the script accepts, read out of its add_argument calls."""
    flags: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and arg.value.startswith("-"):
                    flags.add(arg.value)
    return flags


# --------------------------------------------------------------------------------------
# 2. Every command the docs tell an agent to run
# --------------------------------------------------------------------------------------
# verify_trace.py imports agentx, so it has to run under the interpreter the agent's project
# uses. The other two are stdlib-only and run under any python3. Getting this backwards is
# exit 4, "agentx-python is not installed in this interpreter".
NEEDS_PROJECT_INTERPRETER = {"verify_trace.py"}


def bash_lines(text: str) -> list[str]:
    """Command lines from ```bash blocks, with backslash-continuations joined."""
    out = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        joined = re.sub(r"\\\n\s*", " ", block)
        out.extend(line.strip() for line in joined.splitlines()
                   if line.strip() and not line.strip().startswith("#"))
    return out


def check_commands() -> None:
    known = {p.name: argparse_flags(p) for p in script_files() if p.suffix == ".py"}
    for md in sorted(SKILLS.rglob("*.md")):
        rel = md.relative_to(ROOT)
        for line in bash_lines(md.read_text()):
            match = re.search(r"scripts/([\w.]+)", line)
            if not match:
                continue
            script, interp = match.group(1), line.split()[0]

            if script not in {p.name for p in script_files()}:
                fail(str(rel), f"invokes scripts/{script}, which does not exist")
                continue

            if script.endswith(".sh"):
                if interp != "bash":
                    fail(str(rel), f"invoke {script} with `bash` -> `{line[:60]}`")
                continue

            # An interpreter has to be named. Relying on the exec bit alone means a packaging
            # step that drops file modes silently reintroduces exit 126.
            if not (interp == "python3" or interp.startswith("<project-interpreter>")):
                fail(str(rel), f"names no interpreter -> `{line[:60]}`")
            elif script in NEEDS_PROJECT_INTERPRETER and interp == "python3":
                fail(str(rel), f"{script} imports agentx; use <project-interpreter> -> `{line[:60]}`")
            elif script not in NEEDS_PROJECT_INTERPRETER and interp != "python3":
                fail(str(rel), f"{script} is stdlib-only; python3 is enough -> `{line[:60]}`")

            for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]+", line):
                if flag not in known.get(script, set()):
                    fail(str(rel), f"{script} has no {flag} -> `{line[:60]}`")


# --------------------------------------------------------------------------------------
# 3. Every SDK symbol the docs name  (needs agentx-python)
# --------------------------------------------------------------------------------------
def fenced_python(text: str) -> str:
    """Only fenced code is checked. Prose is where the docs *warn* about wrong imports -
    `from agentx.tracing import _TraceSpan` appears twice on purpose, as the mistake."""
    return "\n".join(re.findall(r"```(?:python|py)\n(.*?)```", text, re.S))


def check_sdk() -> None:
    try:
        import agentx  # noqa: F401
        from agentx.tracing.tracer import Tracer, _TraceSpan
        from agentx.version import VERSION
    except ImportError:
        notes.append("agentx-python is not installed - SDK checks skipped "
                     '(pip install "agentx-python[langchain]" to run them)')
        return

    notes.append(f"SDK checks ran against agentx-python {VERSION}")
    docs = {md: md.read_text() for md in sorted(SKILLS.rglob("*.md"))}

    # 3a. imports in fenced blocks must resolve
    for md, text in docs.items():
        for module, names in re.findall(r"from (agentx[\w.]*) import ([\w, ]+)", fenced_python(text)):
            try:
                mod = importlib.import_module(module)
            except ImportError as exc:
                fail(str(md.relative_to(ROOT)), f"`from {module} import ...` fails: {exc}")
                continue
            for name in (n.strip() for n in names.split(",")):
                if not hasattr(mod, name):
                    fail(str(md.relative_to(ROOT)), f"{module} has no {name}")

    # 3b. integration classes and patch functions named anywhere must exist in the package
    integrations = Path(importlib.import_module("agentx").__file__).parent / "integrations"
    sources = {p.name: p.read_text() for p in integrations.glob("*.py")}
    everything = "\n".join(docs.values())
    for symbol in sorted(set(re.findall(r"\b(AgentX[A-Z]\w+|patch_\w+_client)\b", everything))):
        if hasattr(importlib.import_module("agentx"), symbol):
            continue  # exceptions and top-level exports, e.g. AgentXAuthError
        if not any(re.search(rf"^(class|def) {symbol}\b", src, re.M) for src in sources.values()):
            fail("docs", f"name {symbol}, which is in neither agentx nor agentx.integrations")

    # 3c. methods and trace() keywords the docs use
    for method in sorted(set(re.findall(r"\b(?:tracer|span|client\.tracer)\.(\w+)\(", everything))):
        if not (hasattr(Tracer, method) or hasattr(_TraceSpan, method)):
            fail("docs", f"call .{method}(), which is on neither Tracer nor _TraceSpan")

    import inspect
    params = set(inspect.signature(Tracer.trace).parameters)
    for keyword in sorted(set(re.findall(r"tracer\.trace\([^)]*?(\w+)=", everything))):
        if keyword not in params:
            fail("docs", f"pass tracer.trace({keyword}=...), which is not a parameter")


# --------------------------------------------------------------------------------------
# 4. The manifests - three ecosystems that have drifted apart before (see c5882fa)
# --------------------------------------------------------------------------------------
def check_manifests() -> None:
    versions = {}
    for manifest in sorted((ROOT / "plugins/agentx").glob(".*-plugin/plugin.json")):
        try:
            versions[manifest.parent.name] = json.loads(manifest.read_text())["version"]
        except (json.JSONDecodeError, KeyError) as exc:
            fail(str(manifest.relative_to(ROOT)), f"unreadable or has no version: {exc}")
    if len(set(versions.values())) > 1:
        fail("plugins/agentx", f"the ecosystems disagree on the version: {versions}")

    for marketplace in sorted(ROOT.glob(".*/marketplace.json")):
        try:
            data = json.loads(marketplace.read_text())
        except json.JSONDecodeError as exc:
            fail(str(marketplace.relative_to(ROOT)), f"invalid JSON: {exc}")
            continue
        for plugin in data.get("plugins", []):
            source = (ROOT / plugin["source"]).resolve()
            if not source.is_dir():
                fail(str(marketplace.relative_to(ROOT)), f"source {plugin['source']} does not exist")

    for skill in sorted(SKILLS.glob("*/SKILL.md")):
        head = skill.read_text()[:2000]
        if not head.startswith("---"):
            fail(str(skill.relative_to(ROOT)), "no YAML frontmatter")
        else:
            for key in ("name:", "description:"):
                if key not in head.split("---")[1]:
                    fail(str(skill.relative_to(ROOT)), f"frontmatter has no {key}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-sdk", action="store_true",
                    help="structure checks only; do not import agentx-python")
    args = ap.parse_args()

    check_scripts()
    check_scripts_run()
    check_commands()
    check_manifests()
    if args.skip_sdk:
        notes.append("SDK checks skipped (--skip-sdk)")
    else:
        check_sdk()

    for note in notes:
        print(f"  note: {note}")
    if problems:
        print(f"\n{len(problems)} problem(s):\n", file=sys.stderr)
        for problem in problems:
            print(f"  ✗ {problem}", file=sys.stderr)
        return 1
    print(f"\n  {len(script_files())} scripts, "
          f"{len(list(SKILLS.rglob('*.md')))} documents - all consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
