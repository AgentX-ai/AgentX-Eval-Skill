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

# Every sweep counts what it saw. A broken glob otherwise turns the audit into a
# green light wired to nothing - it would "pass" a repo whose skills directory had
# been renamed, checking zero commands against zero scripts.
from collections import Counter
stats: Counter = Counter()

FLOORS = {
    "scripts": 6,            # 3 in instrument + fetch_analysis.py + 2 in run-eval
    "commands": 20,          # documented invocations across the skills
    "flags": 15,             # --flags validated inside those commands
    "fenced imports": 2,     # from agentx ... import ... in fenced blocks
    "sdk symbols": 8,        # integration classes and patch functions named
    "manifests": 3,          # marketplace.json files reached
    "skill frontmatter": 3,  # SKILL.md files with name/description checked
    "templates": 3,          # dataset templates parsed and shape-checked
    "brief skeleton": 1,     # run-brief harness skeleton derives the link at runtime
    "harness call sites": 12,  # evaluations calls in that skeleton, resolved against the SDK
}


def fail(where: str, msg: str) -> None:
    problems.append(f"{where}: {msg}")


# --------------------------------------------------------------------------------------
# 1. The scripts themselves
# --------------------------------------------------------------------------------------
def script_files() -> list[Path]:
    return sorted(p for p in SKILLS.rglob("scripts/*") if p.suffix in (".py", ".sh"))


def check_scripts() -> None:
    for path in script_files():
        stats["scripts"] += 1
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
        elif path.suffix == ".sh":
            import subprocess
            proc = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
            if proc.returncode != 0:
                fail(str(rel), f"bash -n: {proc.stderr.strip()[:90]}")


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
    """Every --flag the script accepts, read out of its add_argument calls.

    A file that does not parse yields no flags rather than an exception - check_scripts
    has already reported the syntax error, and the auditor crashing on a broken input is
    the auditor failing at its one job."""
    flags: set[str] = set()
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return flags
    for node in ast.walk(tree):
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


def check_readme_flags() -> None:
    """READMEs mention the scripts too, in a looser register - `.venv/bin/python
    verify_trace.py --capabilities`. They are not held to the skills' invocation contract,
    but a flag they name still has to exist."""
    known = {p.name: argparse_flags(p) for p in script_files() if p.suffix == ".py"}
    for md in [ROOT / "README.md", *ROOT.glob("plugins/*/README.md")]:
        if not md.is_file():
            continue
        for line in bash_lines(md.read_text()):
            for script in known:
                if script in line:
                    for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]+", line):
                        if flag not in known[script]:
                            fail(str(md.relative_to(ROOT)),
                                 f"{script} has no {flag} -> `{line[:60]}`")


def check_commands() -> None:
    known = {p.name: argparse_flags(p) for p in script_files() if p.suffix == ".py"}
    for md in sorted(SKILLS.rglob("*.md")):
        rel = md.relative_to(ROOT)
        for line in bash_lines(md.read_text()):
            match = re.search(r"scripts/([\w.]+)", line)
            if not match:
                continue
            script, interp = match.group(1), line.split()[0]
            stats["commands"] += 1

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
                stats["flags"] += 1
                if flag not in known.get(script, set()):
                    fail(str(rel), f"{script} has no {flag} -> `{line[:60]}`")


# --------------------------------------------------------------------------------------
# 3. Every SDK symbol the docs name  (needs agentx-python)
# --------------------------------------------------------------------------------------
def fenced_python(text: str) -> str:
    """Only fenced code is checked. Prose is where the docs *warn* about wrong imports -
    `from agentx.tracing import _TraceSpan` appears twice on purpose, as the mistake."""
    return "\n".join(re.findall(r"```(?:python|py)\n(.*?)```", text, re.S))


def check_sdk() -> bool:
    try:
        import agentx  # noqa: F401
        from agentx.tracing.tracer import Tracer, _TraceSpan
        from agentx.version import VERSION
    except ImportError:
        notes.append("agentx-python is not installed - SDK checks skipped "
                     '(pip install "agentx-python[langchain]" to run them)')
        return False

    notes.append(f"SDK checks ran against agentx-python {VERSION}")
    docs = {md: md.read_text() for md in sorted(SKILLS.rglob("*.md"))}
    for md in [ROOT / "README.md", *sorted(ROOT.glob("plugins/*/README.md"))]:
        if md.is_file():
            docs[md] = md.read_text()

    # 3a. imports in fenced blocks must resolve
    for md, text in docs.items():
        for module, names in re.findall(r"from (agentx[\w.]*) import ([\w, ]+)", fenced_python(text)):
            try:
                mod = importlib.import_module(module)
            except ImportError as exc:
                fail(str(md.relative_to(ROOT)), f"`from {module} import ...` fails: {exc}")
                continue
            for name in (n.strip() for n in names.split(",")):
                stats["fenced imports"] += 1
                if not hasattr(mod, name):
                    fail(str(md.relative_to(ROOT)), f"{module} has no {name}")

    # 3b. integration classes and patch functions named anywhere must exist in the package
    integrations = Path(importlib.import_module("agentx").__file__).parent / "integrations"
    sources = {p.name: p.read_text() for p in integrations.glob("*.py")}
    everything = "\n".join(docs.values())
    for symbol in sorted(set(re.findall(r"\b(AgentX[A-Z]\w+|patch_\w+_client)\b", everything))):
        stats["sdk symbols"] += 1
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

    check_evaluations_surface()
    return True


# --------------------------------------------------------------------------------------
# 3d. The grading path the generated harness runs on  (needs agentx-python)
# --------------------------------------------------------------------------------------
def check_evaluations_surface() -> None:
    """Resolve the run-brief skeleton's `client.evaluations` calls against the SDK.

    Everything above this reaches imports, integration classes and the tracer. Nothing
    reached `client.evaluations`, so the entire grading path went unchecked: the call
    that starts a run, the keyword naming its grader, and the fields the harness prints
    off the finished run. That keyword is the one the judge-scorer consolidation renamed
    - `scorer_id` now, with `evaluation_settings_id` kept as an alias - and the alias is
    the only thing standing between this skeleton and a TypeError. The audit stayed
    green through the whole rename, which is the silent failure this file exists to
    prevent.

    It matters more here than in the other briefs because the skeleton is not read, it
    is copied: it lands in a user's repo as eval/run_eval.py and is committed there, so
    it breaks in their harness long after the SDK moved.

    The skeleton is not valid Python (`main.<callable>` is a placeholder), so it is read
    by regex like the rest of the docs - over the same bytes real harnesses come from.
    """
    brief = SKILLS / "run-eval/references/run-brief.md"
    if not brief.exists():
        return  # check_brief_skeleton already reported it
    skeleton = next((f for f in re.findall(r"```python\n(.*?)```", brief.read_text(), re.S)
                     if "def adapter(case):" in f), "")
    if not skeleton:
        return  # ditto
    rel = str(brief.relative_to(ROOT))

    try:
        from agentx.evaluations.datasets import DatasetClient
        from agentx.evaluations.models import Dataset, EvaluationSubject
        from agentx.evaluations.runner import EvaluationRunContext, EvaluationsRunner
    except ImportError as exc:
        fail(rel, f"the SDK's evaluations surface moved: {exc}")
        return

    import inspect

    # The call that starts the run, and whatever is chained onto it.
    call = re.search(r"evaluations\.run\((.*?)\n\s*\)((?:\.\w+\([^()]*\))*)", skeleton, re.S)
    if not call:
        fail(rel, "skeleton no longer calls evaluations.run(...) - nothing starts a run")
        return
    args, chain = call.group(1), call.group(2)

    # Keywords passed directly, plus a grader passed through a **{...} splat. The keys
    # inside subject={...} are data, not keywords, which is why the splat is matched on
    # its own rather than by sweeping every quoted key in the argument list.
    passed = set(re.findall(r"^\s*(\w+)=", args, re.M))
    passed |= set(re.findall(r"\*\*\(\s*\{\s*[\"'](\w+)[\"']\s*:", args))
    run_params = set(inspect.signature(EvaluationsRunner.run).parameters) - {"self"}
    for keyword in sorted(passed):
        stats["harness call sites"] += 1
        if keyword not in run_params:
            fail(rel, f"skeleton passes evaluations.run({keyword}=...), which is not a "
                      f"parameter - it takes {', '.join(sorted(run_params))}")

    for method in sorted(set(re.findall(r"\.(\w+)\(", chain))):
        stats["harness call sites"] += 1
        if not hasattr(EvaluationRunContext, method):
            fail(rel, f"skeleton chains .{method}() onto the run, which "
                      "EvaluationRunContext does not have")

    # Everything read off the finished run - and the reason reading it there is legal.
    for attr in sorted(set(re.findall(r"\brun\.(\w+)", skeleton))):
        stats["harness call sites"] += 1
        if not hasattr(EvaluationRunContext, attr):
            fail(rel, f"skeleton reads run.{attr}, which EvaluationRunContext does not have")
    if hasattr(EvaluationRunContext, "finalize"):
        returns = str(inspect.signature(EvaluationRunContext.finalize).return_annotation)
        if "EvaluationRunContext" not in returns:
            fail(rel, f"finalize() now returns {returns} - the skeleton reads run.<attr> "
                      "off whatever it hands back")

    for method in sorted(set(re.findall(r"evaluations\.datasets\.(\w+)\(", skeleton))):
        stats["harness call sites"] += 1
        if not hasattr(DatasetClient, method):
            fail(rel, f"skeleton calls evaluations.datasets.{method}(), which DatasetClient "
                      "does not have")
    for field in sorted(set(re.findall(r"\bdataset\.(\w+)", skeleton))):
        stats["harness call sites"] += 1
        if field not in Dataset.model_fields:
            fail(rel, f"skeleton reads dataset.{field}, which is not a field of Dataset")

    # subject= is a literal dict here, and pydantic rejects an off-list kind before any
    # run is created - the same way it rejects an off-list framework (see the brief).
    subject = re.search(r"subject=(\{[^{}]*\})", args)
    if subject:
        stats["harness call sites"] += 1
        try:
            EvaluationSubject(**ast.literal_eval(subject.group(1)))
        except Exception as exc:
            first = (str(exc).splitlines() or ["no message"])[0]
            fail(rel, f"skeleton's subject={subject.group(1)} does not validate: {first[:90]}")


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

    # .claude-plugin and .cursor-plugin keep marketplace.json one level down;
    # .agents keeps it under plugins/, and writes `source` as a dict, not a string.
    manifests = sorted(set(ROOT.glob(".*/marketplace.json")) | set(ROOT.glob(".*/plugins/marketplace.json")))
    if not manifests:
        fail("repo", "no marketplace.json found anywhere")
    for marketplace in manifests:
        stats["manifests"] += 1
        rel = str(marketplace.relative_to(ROOT))
        try:
            data = json.loads(marketplace.read_text())
        except json.JSONDecodeError as exc:
            fail(rel, f"invalid JSON: {exc}")
            continue
        for plugin in data.get("plugins", []):
            raw = plugin.get("source")
            path = raw.get("path") if isinstance(raw, dict) else raw
            if not path:
                fail(rel, f"plugin {plugin.get('name')} has no source path")
                continue
            source = (ROOT / path).resolve()
            if not source.is_dir():
                fail(rel, f"source {path} does not exist")
                continue
            # The marketplace name is what `claude plugin install <name>@...` resolves; the
            # plugin.json name is what lands on disk. A rename that touches one but not the
            # other strands every existing install.
            declared = {json.loads(m.read_text()).get("name")
                        for m in source.glob(".*-plugin/plugin.json")}
            if declared and plugin.get("name") not in declared:
                fail(rel, f"lists plugin '{plugin.get('name')}' but {path} declares {sorted(declared)}")

    for skill in sorted(SKILLS.glob("*/SKILL.md")):
        stats["skill frontmatter"] += 1
        head = skill.read_text()[:2000]
        if not head.startswith("---"):
            fail(str(skill.relative_to(ROOT)), "no YAML frontmatter")
        else:
            for key in ("name:", "description:"):
                if key not in head.split("---")[1]:
                    fail(str(skill.relative_to(ROOT)), f"frontmatter has no {key}")


# --------------------------------------------------------------------------------------
# Dataset templates the run-eval skill ships
# --------------------------------------------------------------------------------------
def check_templates() -> None:
    """A template is a POST /datasets payload frozen into the repo. The engine validates
    only name and questions[].main_question.query at the door, so a drifted field name
    (expected_results for expectedResults, say) would not fail creation - it would
    silently upload cases with no reference answers. This is where that drift fails."""
    for path in sorted(SKILLS.glob("*/templates/*.json")):
        stats["templates"] += 1
        rel = str(path.relative_to(ROOT))
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            fail(rel, f"not valid JSON: {exc}")
            continue
        if not (isinstance(data.get("name"), str) and data["name"].strip()):
            fail(rel, "no 'name'")
        questions = data.get("questions")
        if not (isinstance(questions, list) and questions):
            fail(rel, "no 'questions'")
            continue
        for i, q in enumerate(questions):
            main = (q or {}).get("main_question") or {}
            if not (isinstance(main.get("query"), str) and main["query"].strip()):
                fail(rel, f"questions[{i}].main_question.query missing")
            if "expected_results" in main or "expectedResults" not in main:
                fail(rel, f"questions[{i}]: reference answer must be 'expectedResults' "
                          "(camelCase, the wire field) and must be present")
            smoke = main.get("smokeTest")
            if smoke is not None and not (isinstance(smoke, dict) and smoke.get("enabled") is True
                                          and isinstance(smoke.get("count"), int)):
                fail(rel, f"questions[{i}].smokeTest must be {{enabled: true, count: int}}")


# --------------------------------------------------------------------------------------
# The run-eval brief's harness skeleton
# --------------------------------------------------------------------------------------
def check_brief_skeleton() -> None:
    """The report link must be derived at RUNTIME from AGENTX_API_BASE_URL. This
    regressed once already: the skeleton carried a placeholder the generating agent
    resolved to a literal, so four of five real harnesses shipped with localhost
    baked in while the run itself followed the env. Structure is checked here; the
    derivation's behavior is executed against real address shapes in
    tests/test_runeval_scripts.py."""
    brief = SKILLS / "run-eval/references/run-brief.md"
    if not brief.exists():
        fail("run-eval/references/run-brief.md", "missing")
        return
    stats["brief skeleton"] += 1
    text = brief.read_text()
    fences = re.findall(r"```python\n(.*?)```", text, flags=re.S)
    skeleton = next((f for f in fences if "def adapter(case):" in f), "")
    if not skeleton:
        fail(str(brief.relative_to(ROOT)), "no fenced python skeleton with adapter()")
        return
    if "def report_host()" not in skeleton or "AGENTX_API_BASE_URL" not in skeleton:
        fail(str(brief.relative_to(ROOT)),
             "skeleton must derive the report link at runtime via report_host() "
             "reading AGENTX_API_BASE_URL")
    if "report_host()" not in skeleton.split("def main_entry")[-1]:
        fail(str(brief.relative_to(ROOT)), "main_entry must print via report_host()")
    for bad, why in (
        ("<engine-base-url", "the resolve-at-generation-time placeholder is the bug"),
        ('host = "http', "a literal host baked into the skeleton"),
        ('localhost:4700/evaluations', "a hardcoded report link"),
        ('.split("/api/")', "suffix-strip, not split - proxy prefixes and api-in-hostname break split"),
    ):
        if bad in skeleton:
            fail(str(brief.relative_to(ROOT)), f"skeleton contains {bad!r} - {why}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-sdk", action="store_true",
                    help="structure checks only; do not import agentx-python")
    args = ap.parse_args()

    check_scripts()
    check_scripts_run()
    check_commands()
    check_readme_flags()
    check_manifests()
    check_templates()
    check_brief_skeleton()
    if args.skip_sdk:
        notes.append("SDK checks skipped (--skip-sdk)")
        sdk_ran = False
    else:
        sdk_ran = check_sdk()

    # The SDK floors guard the SDK sweeps, so they only mean anything when those sweeps
    # ran. Keying the exemption on --skip-sdk alone made the docstring's "runs anywhere"
    # false: without agentx-python the audit skipped the checks, correctly said so, then
    # failed on the floor for the checks it had just skipped.
    sdk_floors = {"fenced imports", "sdk symbols", "harness call sites"}
    for what, floor in FLOORS.items():
        if not sdk_ran and what in sdk_floors:
            continue
        if stats[what] < floor:
            fail("audit", f"swept only {stats[what]} {what} (floor {floor}) - "
                          "a glob or regex is no longer finding what it should")

    for note in notes:
        print(f"  note: {note}")
    print("  swept: " + ", ".join(f"{v} {k}" for k, v in sorted(stats.items())))
    if problems:
        print(f"\n{len(problems)} problem(s):\n", file=sys.stderr)
        for problem in problems:
            print(f"  ✗ {problem}", file=sys.stderr)
        return 1
    print("\n  all consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
