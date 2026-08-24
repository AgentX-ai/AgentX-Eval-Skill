# Running an evaluation, in order

You arrive here with a dataset id, an evaluation-settings id or the decision to use the
dataset's own config, and a repo holding the agent. Everything below happens inside that
repo. Each phase ends in something checkable; nothing expensive happens before the cheap
check that would have invalidated it.

`<skill>` is this skill's directory. The engine base URL and key come from the repo's
`.env.agentx` - the same file the instrument skill writes - and every helper here reads
it itself.

---

## Phase 0 - Orient: find the tracer, the client, and the callable

Three things to locate before writing a line. In a repo the instrument skill set up, all
three take a minute; in any other repo they are the judgment calls of this job.

**The tracer.** The module that initialises AgentX once - usually the repo's config
module, holding `tracer = AgentX.from_env().tracer` (possibly guarded to `None` without a
key). The harness MUST reuse this instance for its spans:

> **The two-tracer trap.** Span parenting lives on the tracer instance
> (`tracer.current_span`), not in any global. A span opened on a second `AgentX()`
> client does not become the parent of the repo's model-call records - the run produces
> two parallel traces, and every result row links to the empty one. The evaluations
> *client* is different: it is plain HTTP with no parenting, so `AgentX.from_env()` in
> the harness is fine for `.evaluations` - it is only the **span** that must come from
> the repo's own tracer.

**The model client.** If the repo patches its client (`patch_openai_client`) or passes a
callback handler, the harness gets model calls and tool records for free by calling
through the same construction path (`config.get_client()`, the agent's own invoke). Do
not re-instrument anything.

**The callable.** The function that answers one question - the thing the repo's
entry-point span wraps, not the entry point itself:

> **Call the callable, never the CLI.** Shelling out to `main.py` per case opens the
> repo's own top-level span with no way to read `trace_id` back, double-traces every
> case, and pays process startup per question. Import the inner function the instrument
> skill wrapped (`_plan`, `respond`, `ask` - whatever this repo calls it).

Two properties the callable must have for a fair run - check, and adapt in the adapter
if missing:

- **Independence.** Eval cases are unrelated questions. A repo that shares conversation
  state across calls (a module-level history, a session object) answers case 3 in the
  context of cases 1-2 - reset that state at the top of the adapter, the same way the
  repo's own tests would.
- **No user interaction.** The callable must not prompt or block on stdin.

If the repo's span-opening function is the only callable (no un-spanned inner function),
prefer the inner body it wraps; as a last resort call it as-is and accept that the
harness span nests around the repo's span - one trace, one extra level, still linked.

## Phase 1 - Confirm the ids against this engine

```bash
python3 <skill>/scripts/pick_eval.py --validate-dataset <dataset-id>
python3 <skill>/scripts/pick_eval.py --validate-settings <settings-id>   # when one is used
```

Exit 3 means the id does not exist on this engine under this project's key - a
wrong-engine id, a wrong-project key, and a typo all fail identically here, and this is
the last cheap place to find out. Do not proceed past a failed validation; go back to
the picker.

## Phase 2 - Write the harness

`eval/run_eval.py`, committed. This file *is* the evaluation's definition: the ids are
inline because `/eval-fix`'s re-run executes this exact file to produce a comparable v2.

The shape, adapted to the repo (names from Phase 0). Keep its comment density as well as
its lines - one comment where a line is not self-evident, none where it is. The reasoning
lives in this brief; the harness does not restate it:

```python
"""Evaluation harness for <agent>. Written by the run-eval skill; committed so a
later /eval-fix re-run reproduces v1 exactly."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config                      # the repo's own tracer (Phase 0)
import main                        # wherever the callable lives
from agentx import AgentX

DATASET_ID = "<dataset-id>"
SETTINGS_ID = None                 # or "<settings-id>"

eval_client = AgentX.from_env()    # evaluations client - plain HTTP, no parenting


def report_host() -> str:
    """The dashboard shares the engine's origin: the base URL minus its /api/v1
    suffix - read at RUNTIME, from the same variable the SDK ships the run through."""
    base = os.getenv("AGENTX_API_BASE_URL", "http://localhost:4700/api/v1").rstrip("/")
    return base[: -len("/api/v1")] if base.endswith("/api/v1") else base


def adapter(case):
    # reset per-case state here, if Phase 0 found any (histories, sessions)
    with config.tracer.trace(
        "<agent-name>", input={"query": case.query}, sync=True, monitor=False,
    ) as span:
        answer = main.<callable>(case.query)
        span.output = answer
    return {"output": answer, "trace_id": span.trace_id}   # trace_id: AFTER the block


def main_entry() -> None:
    dataset = eval_client.evaluations.datasets.get(DATASET_ID)
    print(f"dataset '{dataset.name}': {len(dataset.questions or [])} case(s)")

    run = eval_client.evaluations.run(
        dataset_id=DATASET_ID,
        subject={"kind": "custom_agent", "displayName": "<agent-name>"},
        **({"evaluation_settings_id": SETTINGS_ID} if SETTINGS_ID else {}),
    ).execute(adapter).finalize()

    print(f"run {run.run_id}: scored {run.rated_count}, average {run.average_rating}")

    try:
        run.analyze()
    except Exception as exc:       # the score exists either way
        print(f"analysis not generated here ({exc}) - /eval-fix can request it.")

    print(f"report in the browser:  {report_host()}/evaluations/{run.run_id}")
    print(f"next:                   /agentx:eval-fix {run.run_id}")


if __name__ == "__main__":
    try:
        main_entry()
    finally:
        config.tracer.flush()      # daemon delivery thread; a CLI must drain it
```

**`case` is flat; the dataset is not.** The runner hands `adapter()` a case whose text is
`case.query`. The dataset's own questions nest one level down -
`questions[i].main_question.query` - which is what the templates hold, what
`make_dataset.py` writes and what `datasets.get()` returns. Read the dataset, then reach
for `main_question` (or a bare `q.query`) inside the adapter, and you get empty strings
that look exactly like an empty dataset. Nested on the dataset side, flat on the adapter
side; the boundary is `adapter()` itself.

`subject.framework` is a **strict Literal** in the SDK - an off-list value fails pydantic
validation before any run is created. Valid: `raw_python`, `openai`, `anthropic`, `google`,
`langchain`, `llamaindex`, `crewai`, `autogen`, `n8n`, `flowise`, `other`. LangGraph is not
on the list - it runs under `langchain`. When in doubt, `other`.

Five lines of that skeleton carry the whole design - keep all five in the adaptation:

| Line | Why |
|---|---|
| `sync=True` | `span.trace_id` is populated when the block exits; without it the id is `None` and no result links to its trace. |
| `monitor=False` | The run's own judge scores each case; ingest-time checks re-judging the same traces would double the judge bill. |
| `trace_id` read **after** the `with` | Inside the block it is still `None` - reading it a line early looks exactly like a failed send. |
| `flush()` in `finally` | Traces ship on a daemon thread with no atexit hook; a harness that exits promptly takes its traces with it. |
| The guarded-tracer case | If the repo's tracer can be `None` (no key), the harness should fail loudly instead - an evaluation without `.env.agentx` cannot reach the engine at all. `AgentX.from_env()` raising is that loud failure; do not swallow it. |

The browser URL is built by the harness because **self-host runs return no
`dashboardUrl`** - the SDK field stays `None` here, so a report that relies on it prints
no link at all. The dashboard serves every run at `<host>/evaluations/<run-id>`, and
`<host>` is whatever the user's engine is - localhost, another box, or a reverse proxy -
so `report_host()` derives it **at runtime** from `AGENTX_API_BASE_URL`, the same
variable the SDK ships the run through. Never resolve it while writing the harness and
bake in the answer: the committed literal goes stale the moment the engine moves, and
the link then names a different engine than the one that received the run - including
during an /eval-fix re-run launched with an `AGENTX_API_BASE_URL=...` override, where
the runtime link follows the override automatically. (Suffix-strip, do not split on
`/api/`: a proxy path prefix like `https://example.com/agentx/api/v1` must keep its
prefix, and `api` may legitimately appear in a hostname.)

## Phase 3 - Preflight, then get a go-ahead

A run spends real money twice per case: the agent's own model calls, and the judge. So this
phase is two things - an honest number, and a question that carries it.

The numbers come off one read, and it is not a snippet you have to write:

```bash
python3 <skill>/scripts/pick_eval.py --validate-dataset <dataset-id> --json
```

It reads `.env.agentx` on its own, so it needs nothing imported from the repo, and the agent
model is what Phase 0 found.

**Quote `dataset.ratedItems`, not `cases x requests`.** A dataset question can declare
`smokeTest: {enabled, count}`, and the engine rates that many paraphrases of it beside the
original. They are declared data, not a surprise at run time - which is why the helper can
total them and the preflight can be exact. Two of the three shipped templates enable it:

| Template | Cases | Declared variants | Rated |
|---|---|---|---|
| `customer-support` | 6 | 2 | **8** |
| `tool-use` | 5 | 2 | **7** |
| `rag-grounding` | 5 | 0 | **5** |

Quote `cases x requests` on the first of those and the preflight under-promises by a third,
on the template most likely to be picked. Do not guess any of it.

**A throwaway snippet here must import the repo's config first.** `AgentX.from_env()` reads the
environment at the moment it is called, and the thing that puts `.env.agentx` into the
environment is the repo's own config module. So a preflight that opens `from agentx import
AgentX` and calls `from_env()` raises `AgentXAuthError` in a repo that is set up correctly - and
the error names a missing key, not the missing import that caused it. The skeleton has the order
right; ad-hoc snippets are where it gets dropped.

### Then ask, rather than narrating and proceeding

**One AskUserQuestion, two options, and the cost goes inside the option text.** A number in a
paragraph above the question is a number nobody read before approving.

| Option | What its description has to carry |
|---|---|
| **Run it now** | The dataset by name and id, and `<ratedItems> rated items on <model>, each answered by the agent and judged server-side`. This is the money sentence and it belongs where the click is. |
| **Later** | That nothing is lost by waiting: the harness is written and committed, the dataset row already exists. Name the one command that runs it - `.venv/bin/python eval/run_eval.py`. |

**Skip the question when the user has already answered it** - `/run-eval <id> and just run
it`, or an earlier "stop asking". Re-asking reads as not listening.

On **Later**, finish cleanly instead of trailing off: the dataset id, the committed harness
path, the command above, and that `/agentx:eval-fix` picks up from a run whenever one
happens. A declined run is a finished handoff, not an abandoned job.

## Phase 4 - Run it

```bash
.venv/bin/python eval/run_eval.py
```

Foreground for a handful of cases; background with the same 30-second abort check as
eval-fix's Phase 3 when the dataset is large (cases x requests > ~20). The run prints
per-case progress from the SDK; three log lines that look alarming and are not, and the
abort criteria, are the same as in eval-fix's brief - reuse that judgment.

If the process dies mid-run: the run id already exists on the engine with whatever
results were submitted; `finalize` is what closes it. Re-running the harness starts a
fresh run - the half-run stays as a dead row, which is unfortunate but harmless. Say it
happened; do not try to resume into the same run id.

## Phase 5 - Verify before reporting

The run printing a score is not the verification. Three reads:

1. **The run exists and is finalized** - `GET /custom-agent-evaluations/runs/<run-id>`
   returns it with `rated_count` matching the `dataset.ratedItems` quoted in Phase 3, which
   is cases x requests **plus** the dataset's declared smoke-test variants. A count that
   lands exactly on cases x requests is the near-miss worth catching: on a dataset that
   declares variants, it means they were not rated. `fetch_analysis.py` marks which rows
   are variants (`is_smoke_test_variant`), so the two stay separable when reporting.
2. **Results link to traces** - every result row carries a `traceId`. Zero linked
   results means the adapter pattern was broken (usually the two-tracer trap) - the
   scores are still real, but `/eval-fix` will be triaging answer text instead of
   execution paths, which is precisely the degradation this skill exists to avoid. Fix
   the harness and re-run rather than reporting success.
3. **The browser link serves** - request it once; the SPA answers 200 on
   `/evaluations/<run-id>`.

## Phase 6 - Report and hand off

In this order, because each line is consumed by a later step:

1. The score: average rating, rated count, and per-case range if the SDK printed it.
2. **The browser report**: `<host>/evaluations/<run-id>` - where the user watches the
   analysis, reads per-case judgments, and clicks View trace on any result.
3. The handoff: `/agentx:eval-fix <run-id>` ready to paste, and one line on what
   eval-fix will do with it.
4. The harness: path, the ids inside it, and the commit that holds it - this is what
   "re-run the same way" means later.
5. What was created along the way (datasets, settings), by name and id, with the
   reminder that those rows are permanent.
6. Whether all results linked to traces (Phase 5.2) - say it explicitly either way,
   because it decides what kind of evidence the analysis is built on.
