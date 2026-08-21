#!/usr/bin/env python3
"""Survey a Python repo before instrumenting it: what it is built on, and where a run begins.

Tracing is only useful if the span boundaries match what an agent run actually is. That
boundary is almost never "every function" - it is the one place a request arrives and an
answer leaves. This script finds the candidates for that place and names the framework
integration that covers everything underneath it, so the instrumentation step starts from
the repo's own shape instead of a guess.

It parses with `ast` and never imports the code: a repo that cannot even be installed yet
still gets surveyed, and nothing in it runs.

Usage:
  detect_stack.py [PATH]           # readable summary
  detect_stack.py [PATH] --json    # the same, machine-readable
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", ".venv", "venv", ".env", "env", "node_modules", "__pycache__", "site-packages",
    "dist", "build", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".eggs",
    "migrations", ".next", ".idea", ".vscode", ".worktrees", "worktrees",
}

# module root -> (label, AgentX integration, pip extra). Order matters only for reporting.
INTEGRATIONS = {
    "langgraph":         ("LangGraph",           "AgentXCallbackHandler",   "langchain"),
    "langchain":         ("LangChain",           "AgentXCallbackHandler",   "langchain"),
    "langchain_core":    ("LangChain",           "AgentXCallbackHandler",   "langchain"),
    "langchain_openai":  ("LangChain",           "AgentXCallbackHandler",   "langchain"),
    "langchain_anthropic": ("LangChain",         "AgentXCallbackHandler",   "langchain"),
    "crewai":            ("CrewAI",              "AgentXCrewObserver",      "crewai"),
    "agents":            ("OpenAI Agents SDK",   "AgentXTracingProcessor",  "openai-agents"),
    "llama_index":       ("LlamaIndex",          "AgentXLlamaIndexHandler", "llamaindex"),
    "autogen":           ("AutoGen",             "AgentXAutoGenObserver",   "autogen"),
    "autogen_agentchat": ("AutoGen",             "AgentXAutoGenObserver",   "autogen"),
    "litellm":           ("LiteLLM",             "AgentXLiteLLMLogger",     "litellm"),
    "openai":            ("OpenAI (raw client)", "patch_openai_client",     "openai"),
    "anthropic":         ("Anthropic",           "patch_anthropic_client",  "anthropic"),
}
# Dotted imports, checked against the full module path rather than its root.
DOTTED_INTEGRATIONS = {
    "google.adk":   ("Google ADK",           "AgentXADKPlugin",   "google-adk"),
    "google.genai": ("Google GenAI (Gemini)", "patch_genai_client", "google-genai"),
}

# module root -> what kind of entry point it implies
ENTRYPOINT_LIBS = {
    "fastapi": "HTTP handler", "flask": "HTTP handler", "starlette": "HTTP handler",
    "django": "HTTP handler", "quart": "HTTP handler", "sanic": "HTTP handler",
    "aiohttp": "HTTP handler", "litestar": "HTTP handler", "langserve": "HTTP handler",
    "celery": "background task", "rq": "background task", "dramatiq": "background task",
    "click": "CLI command", "typer": "CLI command", "argparse": "CLI command",
    "streamlit": "UI callback", "chainlit": "UI callback", "gradio": "UI callback",
    "discord": "bot handler", "slack_bolt": "bot handler", "telegram": "bot handler",
}

MANIFESTS = ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py", "environment.yml")


def iter_python(root: Path) -> list[Path]:
    out = []
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return sorted(out)


def decorator_name(node: ast.AST) -> str:
    """`@app.post("/chat")` -> 'app.post'. Best effort; unknown shapes come back empty."""
    if isinstance(node, ast.Call):
        return decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        return f"{decorator_name(node.value)}.{node.attr}".lstrip(".")
    if isinstance(node, ast.Name):
        return node.id
    return ""


class FileFacts:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.imports: set[str] = set()
        self.dotted: set[str] = set()
        self.decorated: list[tuple[str, str, int]] = []   # (func, decorator, lineno)
        self.has_main_guard = False
        self.functions: list[tuple[str, int, bool]] = []  # (name, lineno, is_async)
        self.agentx = False          # imports agentx at all - an eval harness does too
        self.agentx_tracing = False  # actually traces, which is what must not be doubled


def scan_file(path: Path) -> FileFacts | None:
    facts = FileFacts(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (SyntaxError, ValueError, OSError):
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                facts.imports.add(alias.name.split(".")[0])
                facts.dotted.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            facts.imports.add(node.module.split(".")[0])
            facts.dotted.add(node.module)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts.functions.append((node.name, node.lineno, isinstance(node, ast.AsyncFunctionDef)))
            for dec in node.decorator_list:
                name = decorator_name(dec)
                if name:
                    facts.decorated.append((node.name, name, node.lineno))
        elif isinstance(node, ast.If):
            test = node.test
            if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"):
                facts.has_main_guard = True

    facts.agentx = "agentx" in facts.imports
    # Importing agentx is not the same as tracing with it: an evaluation harness imports the
    # same package and must not be mistaken for an already-instrumented agent.
    if facts.agentx:
        source = path.read_text(encoding="utf-8", errors="replace")
        facts.agentx_tracing = ("tracer" in source) or ("agentx.integrations" in source)
    return facts


def entrypoint_kind(facts: FileFacts) -> str | None:
    """What kind of run this file starts, if any. HTTP beats CLI when a file does both."""
    kinds = {ENTRYPOINT_LIBS[mod] for mod in facts.imports if mod in ENTRYPOINT_LIBS}
    for preferred in ("HTTP handler", "background task", "bot handler", "UI callback", "CLI command"):
        if preferred in kinds:
            return preferred
    return "script main" if facts.has_main_guard else None


def uses_llm(facts: FileFacts) -> bool:
    if facts.imports & set(INTEGRATIONS):
        return True
    return any(d.startswith(prefix) for d in facts.dotted for prefix in DOTTED_INTEGRATIONS)


HANDLER_DECORATORS = ("route", "get", "post", "put", "patch", "delete", "task", "command",
                      "callback", "on_message", "event", "websocket", "shared_task")
TOOL_DECORATORS = ("tool", "function_tool", "agentx_tool")


def handler_lines(facts: FileFacts) -> list[str]:
    """Route/task/command handlers - the functions a top-level span most likely wraps."""
    out = []
    for func, dec, lineno in facts.decorated:
        tail = dec.rsplit(".", 1)[-1]
        if tail in HANDLER_DECORATORS and tail not in TOOL_DECORATORS:
            out.append(f"{facts.path}:{lineno} {func}() @{dec}")
    return out


def tool_lines(facts: FileFacts) -> list[str]:
    """Declared tools. A framework integration records these on its own; a hand-rolled
    dispatch loop does not, and that is where trace_tool_call() earns its place."""
    out = []
    for func, dec, lineno in facts.decorated:
        if dec.rsplit(".", 1)[-1] in TOOL_DECORATORS:
            out.append(f"{facts.path}:{lineno} {func}() @{dec}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=".", help="repo root (default: .)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=12, help="how many candidates to print (default 12)")
    args = ap.parse_args()

    root = Path(args.path).resolve()
    files = iter_python(root)
    scanned = [f for f in (scan_file(p) for p in files) if f is not None]

    integrations: dict[str, dict] = {}
    for facts in scanned:
        for mod in facts.imports:
            if mod in INTEGRATIONS:
                label, entry, extra = INTEGRATIONS[mod]
                integrations.setdefault(label, {"integration": entry, "extra": extra, "files": []})
                integrations[label]["files"].append(str(facts.path.relative_to(root)))
        for dotted in facts.dotted:
            for prefix, (label, entry, extra) in DOTTED_INTEGRATIONS.items():
                if dotted == prefix or dotted.startswith(prefix + "."):
                    integrations.setdefault(label, {"integration": entry, "extra": extra, "files": []})
                    integrations[label]["files"].append(str(facts.path.relative_to(root)))

    entrypoints = []
    for facts in scanned:
        kind = entrypoint_kind(facts)
        if not kind:
            continue
        entrypoints.append({
            "file": str(facts.path.relative_to(root)),
            "kind": kind,
            "calls_llm": uses_llm(facts),
            "handlers": [h.replace(str(root) + "/", "") for h in handler_lines(facts)],
            "tools": [t.replace(str(root) + "/", "") for t in tool_lines(facts)],
        })
    # A file that both receives requests and calls a model is where a run begins; everything
    # else is a weaker candidate and is reported below it rather than mixed in.
    entrypoints.sort(key=lambda e: (not e["calls_llm"], e["kind"] != "HTTP handler", e["file"]))

    already = [str(f.path.relative_to(root)) for f in scanned if f.agentx_tracing]
    uses_agentx = [str(f.path.relative_to(root)) for f in scanned if f.agentx and not f.agentx_tracing]
    manifests = [m for m in MANIFESTS if (root / m).is_file()]
    dotenv = any("dotenv" in f.imports for f in scanned)

    report = {
        "root": str(root),
        "python_files": len(files),
        "integrations": integrations,
        "entrypoints": entrypoints[: args.limit],
        "entrypoint_count": len(entrypoints),
        "already_instrumented": already,
        "imports_agentx_without_tracing": uses_agentx,
        "env_file_present": (root / ".env.agentx").is_file(),
        "manifests": manifests,
        "python_dotenv_imported": dotenv,
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"{len(files)} Python file(s) under {root}")
    if not files:
        print("\nNo Python here. The AgentX tracing SDK is Python-only - there is no JavaScript")
        print("equivalent to install, so this repo cannot be instrumented with it.")
        return 1

    print("\nframeworks and the integration that covers them:")
    if integrations:
        for label, info in integrations.items():
            where = ", ".join(sorted(set(info["files"]))[:3])
            print(f"  {label:22} {info['integration']:24} pip install \"agentx-python[{info['extra']}]\"")
            print(f"  {'':22} seen in {where}")
    else:
        print("  none recognised - plain @tracer.trace(...) on the entry point covers this repo")

    print(f"\nentry-point candidates ({len(entrypoints)} found, showing {len(report['entrypoints'])}):")
    for e in report["entrypoints"]:
        mark = "*" if e["calls_llm"] else " "
        print(f" {mark} {e['file']}  [{e['kind']}]")
        for h in e["handlers"][:4]:
            print(f"      {h}")
        for t in e["tools"][:4]:
            print(f"      tool: {t}")
    if entrypoints:
        print("  (* = also calls a model, so the run really does begin here)")

    print("\nstate:")
    print(f"  manifest:            {', '.join(manifests) or 'none found'}")
    print(f"  .env.agentx:         {'present' if report['env_file_present'] else 'absent'}")
    print(f"  python-dotenv used:  {'yes' if dotenv else 'no'}")
    if uses_agentx:
        print(f"  imports agentx already: {', '.join(uses_agentx[:5])}")
        print("  Probably an evaluation harness. Reuse its project key so traces and runs match.")
    if already:
        print(f"  ALREADY TRACED: {', '.join(already[:5])}")
        print("  Re-instrumenting these would double-count. Extend what is there instead.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
