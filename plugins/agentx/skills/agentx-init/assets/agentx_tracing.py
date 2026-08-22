"""AgentX tracing bootstrap - the single place this project configures tracing.

Import `tracer` from here and nothing else needs to know about AgentX::

    from agentx_tracing import tracer

    @tracer.trace("support-agent", framework="langchain")
    def handle(query: str) -> str:
        ...

Three things this file is responsible for, in order.

**Loading .env.agentx.** Nothing reads that file on its own - it is not `.env`, and Python
has no notion of either. The loader below walks up from this file to find it, so an import
from any working directory still finds the key, and it uses `setdefault`, so a real
environment variable always wins. That direction matters: in production the platform sets
AGENTX_API_KEY itself, and a checked-out dev file that overrode it would silently redirect
production traces into a developer's project.

**Never breaking the app it watches.** `AgentX.from_env()` raises when AGENTX_API_KEY is
absent - which happens on a teammate's first checkout, in CI, and in any deploy where the
secret has not been added yet. Tracing must not be the reason those fail, so a missing or
rejected key degrades to a no-op tracer with the same interface. The agent runs; the traces
are simply not recorded, and one line says so.

**Being constructed once.** The client owns a background delivery thread. One module-level
instance, imported everywhere, is the whole design.

One thing this module deliberately does *not* do is flush. That thread is a daemon and the SDK
registers no `atexit` hook, so an interpreter shutdown kills it mid-queue and a short-lived
process - a CLI, a cron job, a serverless handler, a test run - can exit with its last traces,
or all of them, undelivered. Only the entry point knows when it is finished, so it is the entry
point that calls `tracer.flush()` on the way out. A long-running server does not need to.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)

ENV_FILENAME = ".env.agentx"


def _load_env_file(filename: str = ENV_FILENAME) -> Optional[Path]:
    """Find `filename` at or above this file and load it, without overriding real env vars."""
    here = Path(__file__).resolve()
    for directory in [here.parent, *here.parents]:
        candidate = directory / filename
        if not candidate.is_file():
            continue
        for raw in candidate.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, sep, value = line.partition("=")
            if not sep:
                continue
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return candidate
    return None


# ---------------------------------------------------------------------------
# No-op fallback - same surface as the real tracer, does nothing at all.
#
# "The same surface" is the entire contract here, and approximately-the-same is what breaks
# it. Two shapes have to match `agentx.tracing.tracer` exactly:
#
#   - **`current_span` is a property there, not a method.** The LangChain integration reads
#     it as `tracer.current_span` and branches on `is not None`. A method here hands back a
#     truthy bound method, so the handler takes the "inside a span" branch and calls
#     `_merge_child_run` on it - and tracing breaks the agent in precisely the configuration
#     this class exists to keep running.
#   - **every tracing call the SDK offers has to exist.** A RAG project reaching for
#     `tracer.record_retrieval(...)` with no key set dies on AttributeError otherwise.
#
# The evaluation and CI methods at the bottom are deliberately *not* no-ops; the note there
# explains why.
# ---------------------------------------------------------------------------
class _NoopRecorder:
    """Stands in for the handles `trace_tool_call()` and `trace_retrieval()` yield.

    Carries every attribute either real recorder exposes, so a `with` block that assigns to
    `output`, `success`, `error` or `doc_count` reads the same with tracing off as on.
    """

    def __init__(self) -> None:
        self.input: Any = None
        self.output: Any = None
        self.success: Optional[bool] = None
        self.error: Optional[str] = None
        self.doc_count: Optional[int] = None


class _NoopSpan:
    trace_id = None
    span_id = None

    def __init__(self) -> None:
        self.input: Any = None
        self.output: Any = None

    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def __call__(self, fn):
        # Decorator form: hand the function straight back. Works for async functions too,
        # because an untouched coroutine function is still a coroutine function.
        return fn

    def child_span(self, *args: Any, **kwargs: Any) -> "_NoopSpan":
        return _NoopSpan()

    def add_tool_call(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set_error(self, *args: Any, **kwargs: Any) -> None:
        pass


class _NoopTracer:
    def trace(self, *args: Any, **kwargs: Any) -> _NoopSpan:
        return _NoopSpan()

    @contextmanager
    def trace_tool_call(self, name: str, *, input: Any = None) -> Iterator[_NoopRecorder]:
        yield _NoopRecorder()

    def record_tool_call(self, *args: Any, **kwargs: Any) -> None:
        pass

    @contextmanager
    def trace_retrieval(self, name: str = "Retrieval", *, query: Any = None) -> Iterator[_NoopRecorder]:
        yield _NoopRecorder()

    def record_retrieval(self, *args: Any, **kwargs: Any) -> None:
        pass

    @contextmanager
    def use_span(self, span: Any) -> Iterator[Any]:
        yield span

    @property
    def current_span(self) -> None:
        # A property, not a method - see the note above this class.
        return None

    def flush(self, timeout: float = 5.0) -> None:
        pass

    # -- evaluation and CI --------------------------------------------------------------
    # These return a result the caller acts on: a score, a pass rate, a gate decision. A
    # silent no-op returning None would protect nothing - it would let a scoring run report
    # nothing as though it were something, which is the same silent-success failure the rest
    # of this file exists to prevent. So: spans and tool calls degrade quietly, because the
    # agent's own work must not depend on tracing; a scoring run that cannot score says so.
    def _unavailable(self, method: str) -> None:
        raise RuntimeError(
            f"tracer.{method}() needs a real AgentX client, and tracing is disabled because "
            "AGENTX_API_KEY is not set. Tracing calls degrade to no-ops on purpose; "
            "evaluation and CI calls do not, because they return a result you act on."
        )

    def evaluate_trace(self, *args: Any, **kwargs: Any) -> None:
        self._unavailable("evaluate_trace")

    def run_eval(self, *args: Any, **kwargs: Any) -> None:
        self._unavailable("run_eval")

    def create_ci_run(self, *args: Any, **kwargs: Any) -> None:
        self._unavailable("create_ci_run")

    def get_ci_run(self, *args: Any, **kwargs: Any) -> None:
        self._unavailable("get_ci_run")

    def finalize_ci_run(self, *args: Any, **kwargs: Any) -> None:
        self._unavailable("finalize_ci_run")

    def submit_result(self, *args: Any, **kwargs: Any) -> None:
        self._unavailable("submit_result")


# ---------------------------------------------------------------------------
def _build():
    env_file = _load_env_file()

    if not os.getenv("AGENTX_API_KEY"):
        log.warning(
            "AgentX tracing is off: AGENTX_API_KEY is not set (looked for %s). "
            "The agent runs normally; nothing is recorded.", env_file or ENV_FILENAME
        )
        return None, _NoopTracer()

    if not os.getenv("AGENTX_API_BASE_URL"):
        # Unset means the hosted platform, which is the wrong destination for a self-host
        # user and fails silently - the traces leave and never arrive anywhere they look.
        log.warning(
            "AGENTX_API_BASE_URL is not set; traces will go to the hosted platform "
            "(https://api.agentx.so). For self-host, set it to http://localhost:4700/api/v1."
        )

    try:
        from agentx import AgentX

        client = AgentX.from_env()
        return client, client.tracer
    except Exception as exc:  # noqa: BLE001 - tracing must never take the app down with it
        log.warning("AgentX tracing is off (%s: %s). The agent runs normally.",
                    exc.__class__.__name__, exc)
        return None, _NoopTracer()


client, tracer = _build()

#: True when traces are really being delivered. Useful in a health endpoint.
tracing_enabled = client is not None

__all__ = ["client", "tracer", "tracing_enabled"]
