# Instrumenting a repo, in order

`<skill>` below is this skill's own directory. Installed as a Claude Code plugin that is
`${CLAUDE_PLUGIN_ROOT}/skills/agentx-init`; installed standalone with `npx skills add`
it is wherever the agent unpacked the skill. Resolve it once and reuse it.

Carry this out against the repo you are inside. Every phase ends in something checkable, and
the phases are ordered so that nothing expensive happens before the cheap thing that would
have invalidated it.

The whole job is smaller than it looks. A traced agent is **one span at the entry point**,
**one line of auto-instrumentation for the model client**, and **tool calls where the repo
rolls its own tools**. Nesting is automatic: any span opened inside an active span becomes
its child, and so does every call made through a patched client. Decorating more functions
does not produce a better trace - it produces a flat list of Python frames where a trajectory
should be.

---

## Phase 0 - Survey before touching anything

```bash
python3 <skill>/scripts/detect_stack.py .
```

Read the three things it reports:

- **Frameworks**, each with the integration that covers it and the pip extra to install.
- **Entry-point candidates**, starred where the file both receives work and calls a model.
  A starred file is where a run begins. Everything else is a weaker guess.
- **State** - whether the repo already traces (do not double it), whether it already imports
  `agentx` for an evaluation harness (reuse that project's key), and which manifest to add
  the dependency to.

If it reports no Python files, stop and say so: the AgentX tracing SDK is Python-only, and
there is no JavaScript package to fall back to.

**If more than one starred candidate looks plausible, ask which one is the agent.** A repo
with an HTTP handler and a Celery worker has two real entry points and probably wants two
named agents; a repo with six starred files usually has one agent and five scripts. This is
the one judgement the script cannot make, and getting it wrong means every trace is either
a fragment or a duplicate.

---

## Phase 1 - Key, then dependency, then one client

### 1a. Identify the engine and pick the project

```bash
python3 <skill>/scripts/agentx_key.py --host <address> --json --limit 8
```

One call. It probes `/auth/config` unauthenticated, reports what kind of engine answered,
resolves a key, and - when the engine allows it - returns the project list you are about to
ask about. Read three fields off the JSON:

| Field | What to do with it |
|---|---|
| `engine_kind` / `auth_mode` | `self-host` + `disabled` is the common case and the only one where projects can be enumerated |
| `can_list_projects` | `true` → ask the user. `false` → say why (`reason`) in one line and move on |
| `projects[]` | the options: `id`, `name`, `isDefault` |

**When `can_list_projects` is true, ask with AskUserQuestion. Do not take the default
silently.** The key *is* the project selector - a trace is visible to its own project's key and
to no other - and there is no way to move traces afterwards. Put the engine's default first and
marked as such, and add two things to the question:

- **The project the repo's evaluations already use, recommended**, when `detect_stack.py` found
  an eval harness. Traces and runs in different projects never appear next to each other.
- **"A new project for this app"**, when the engine is self-host in disabled mode. Say in the
  option's own description that it writes a project row **the engine cannot delete**.

Then write it:

```bash
python3 <skill>/scripts/agentx_key.py \
  --host <address> --write-env .env.agentx --project <id>
```

Prefer the id. Project names are not unique on self-host, and the script refuses an ambiguous
name rather than guessing which one you meant.

The key itself resolves from `--api-key`, then `$AGENTX_API_KEY`, then `~/.agentx/config.json`,
then the engine's own handout - **each verified against the engine you named** before it is
used. On self-host in disabled mode `/auth/config` returns the default project's key outright,
which is what makes a cold start work with no environment variable and nothing to paste. It is
last in that order deliberately: it is always the *default* project, which is right for a fresh
install and wrong for anyone who has already chosen where their data goes.

The script prints nothing derived from a key - not a masked form, not a hash. It names the
project and where the key came from, and writes the key itself straight to disk, so the secret
never enters the transcript. If you find yourself about to `cat
.env.agentx`, don't - `agentx_key.py` will tell you what you want to know without it.

### 1b. The dependency

```bash
pip install "agentx-python[<extra>]"     # the extra detect_stack.py named, if any
```

Add it to the repo's manifest too - `requirements.txt`, `pyproject.toml`, whichever it has.
An install that only exists in one shell is an import error on the next machine.

Then check what you actually got:

```bash
<project-interpreter> <skill>/scripts/verify_trace.py --capabilities
```

Not `python3` for this one - unlike the other two helpers it imports `agentx`, so it has to run
where the SDK is installed. **The published package and its docs are
not always in step**, and this is not hypothetical: on PyPI 0.6.30 the documented
`span.add_tool_call(..., success=False, error=...)` does not exist - that signature takes
only `name`, `input`, `output` and `latency_ms`, so those keywords raise `TypeError` inside
the agent at the first failed tool call. Generate against what the probe reports, not against
what a README says.

And ask the probe rather than reaching for `inspect` yourself. If you do need to read the SDK
directly, its tracer internals live in `agentx.tracing.tracer` (`Tracer`, `_TraceSpan`);
`agentx.tracing` re-exports only `Tracer`, `IngestClient` and the CI types, so
`from agentx.tracing import _TraceSpan` raises `ImportError`.

### 1c. Initialise the SDK once, in a file the repo already has

**Do not add a module for this.** The repo already has somewhere that reads configuration and
hands out clients - the file that calls `load_dotenv()`, the settings module, whatever it calls
its `config.py`. Two lines go there, and everything else imports `tracer` from it:

```python
# myapp/config.py - beside the load_dotenv() this file already calls
from agentx import AgentX

load_dotenv(Path(__file__).resolve().parents[1] / ".env.agentx")   # the key and the base URL
tracer = AgentX.from_env().tracer
```

Three things those two lines are doing, each of which breaks quietly if it moves:

- **Loading `.env.agentx`.** Nothing reads it on its own - it is not `.env`, and Python reads
  neither. Build the path from `__file__`, not a bare filename: a relative path resolves against
  the working directory, so running the agent from anywhere but the repo root silently untraces
  it. A repo without python-dotenv reads the file in four lines with `os.environ.setdefault`,
  which also keeps the precedence right - a real environment variable wins, so production's key
  is never overridden by a checked-out dev file.
- **One client.** It owns a background delivery thread. One module-level instance, imported
  everywhere; not one per call site.
- **The base URL from that file, never a literal in the source.** Hardcode the wrong port and
  nothing raises - the traces just leave for somewhere nobody is watching.

**`AgentX.from_env()` raises `AgentXAuthError` when `AGENTX_API_KEY` is missing**, at import,
because the constructor eagerly builds its evaluations client. So these two lines make AgentX a
dependency of the repo: a checkout without `.env.agentx` fails at startup rather than running
untraced. For a repo one team runs that is usually right, and it is loud rather than silent -
but **say so in the Phase 7 report**, because it changes what happens for everyone else who runs
this code.

When the repo has to run for people who have no key - CI, an open-source repo, a deploy where the
secret lands later - guard it. That costs exactly one `if`, because Phase 2 gives you exactly one
span:

```python
tracer = AgentX.from_env().tracer if os.getenv("AGENTX_API_KEY") else None
```

```python
if tracer is None:                      # no key here: run untraced rather than not at all
    return handle(query)
with tracer.trace(...) as span:
    ...
```

and Phase 3's integration becomes `[AgentXCallbackHandler(tracer, ...)] if tracer else []`.

Earlier versions of this skill copied a no-op tracer object into the repo so the call sites
needed no branch at all. That is not worth a vendored file: a copied shim has to track the SDK's
surface, and when it drifts it fails in precisely the configuration it exists to protect -
`current_span` is a property on the real `Tracer`, and a shim defining it as a method hands the
LangChain integration a truthy bound method instead of `None`. One `if` at one span site cannot
drift.

---

## Phase 2 - One span where the run begins

Wrap the handler body, not the framework and not the whole file.

```python
from myapp.config import tracer        # wherever Phase 1c initialised it

@tracer.trace("support-agent", framework="langchain", model="gpt-4o")
def handle(query: str) -> str:
    return chain.invoke(query)
```

Use the context-manager form when the input or the output needs shaping, which is most of
the time in a web handler:

```python
@app.post("/chat")
async def chat(body: ChatRequest):
    with tracer.trace("support-agent", framework="langchain", session_id=body.thread_id) as span:
        span.input = body.question          # the question, not the request object
        answer = await agent.run(body.question)
        span.output = answer                # the answer, not the response envelope
        return {"answer": answer}
```

Four rules, each of which is a real trace someone has had to throw away:

| Rule | Why |
|---|---|
| **One top-level span per run** | Nesting is automatic. A second `@tracer.trace` two frames down becomes a child, which is right; a second one on a *separately invoked* function becomes a second trace of the same run, which is not. |
| **`input`/`output` are the question and the answer** | They are what a judge reads and what an evaluation scores. A serialised `Request` object is not an answer, and a trace full of them cannot be evaluated later. |
| **`session_id` groups a conversation** | Multi-turn agents are scored as conversations on self-host - Observe > Sessions, and the session-coherence judge - and that only works if the turns share an id. Omit it and every turn is its own session. |
| **`name` is the agent's identity** | One stable agent per distinct name, created on first use. Do not interpolate a user id or a timestamp into it, or the dashboard fills with thousands of one-trace agents. |

Set `framework=` and `model=` when you know them. They cost nothing and they are what the
dashboard groups and prices by.

### Then flush on the way out, if the process is short-lived

```python
def main() -> None:
    try:
        run()
    finally:
        tracer.flush()      # default timeout 5s; pass timeout= over a slow link
```

Traces are queued in memory and drained by a background **daemon** thread, and the SDK
registers no `atexit` hook - so interpreter shutdown kills that thread mid-queue instead of
waiting for it. A process that exits promptly takes its undelivered traces with it. This is
the silent failure that looks most like a bug in the instrumentation: the wiring is right, the
run happened, the diff is correct, and the dashboard stays empty.

| Shape | Flush? |
|---|---|
| CLI, script, cron job, one-shot task | **Yes** - in a `finally`, so a crash still ships what it has |
| Test suite that asserts on traces | **Yes** - before the assertions, not after |
| Serverless / FaaS handler | **Yes** - the runtime freezes or reaps the process between invocations |
| Long-running server or worker | No - the thread keeps up, and flushing per request just adds latency |

Flush once, at the process boundary - not per run, and not inside the span. `verify_trace.py`
needs none of this: `sync=True` already blocks until the engine acknowledges.

---

## Phase 3 - Auto-instrument the model client

One line at construction, and every LLM call underneath gets captured - arguments, output,
latency, token usage, and the prompt-cache read/write counts that self-host prices separately.

| Detected | Install | Wire it up |
|---|---|---|
| LangChain / LangGraph | `agentx-python[langchain]` | `AgentXCallbackHandler(tracer)` in `callbacks=[...]` |
| CrewAI | `agentx-python[crewai]` | `AgentXCrewObserver` |
| OpenAI Agents SDK | `agentx-python[openai-agents]` | `AgentXTracingProcessor` |
| OpenAI (raw client) | `agentx-python[openai]` | `patch_openai_client(client, tracer, name=...)` |
| Anthropic | `agentx-python[anthropic]` | `patch_anthropic_client(client, tracer, name=...)` |
| Google ADK | `agentx-python[google-adk]` | `AgentXADKPlugin` |
| Google GenAI (Gemini) | `agentx-python[google-genai]` | `patch_genai_client(client, tracer, name=...)` |
| LiteLLM | `agentx-python[litellm]` | `AgentXLiteLLMLogger` |
| LlamaIndex | `agentx-python[llamaindex]` | `AgentXLlamaIndexHandler` |
| AutoGen | `agentx-python[autogen]` | `AgentXAutoGenObserver` |
| none of these | `agentx-python` | nothing - the Phase 2 span is the whole story |

The patch functions are called once, on the client object, right where it is constructed:

```python
from openai import OpenAI
from agentx.integrations.openai import patch_openai_client
from myapp.config import tracer

oai = OpenAI()
patch_openai_client(oai, tracer, name="support-agent")
```

Two things to know before wiring one up:

- **A patched client inside an active span becomes a child span**, which is the point. A
  patched client called with no span open produces its own standalone trace instead - fine
  for a one-shot script, wrong for a request handler, and the reason Phase 2 comes first.
- **`patch_openai_client` does not trace `stream=True` calls.** Streaming responses are
  passed through untouched rather than risk a half-consumed iterator. If the agent streams,
  the Phase 2 span is what records the run, and that is the honest answer - say so rather
  than leaving the user to discover the gap in the dashboard.

---

## Phase 4 - Tool calls, where the repo rolls its own

Skip this entirely if a framework integration from Phase 3 is in play: it already records
every tool the framework dispatches, and recording them again double-counts.

What needs doing by hand is the pattern where a model returns a function call and the repo's
own dispatch loop executes it:

```python
with tracer.trace_tool_call("lookup_order", input=order_id) as t:
    t.output = orders.get(order_id)
```

An exception escaping that block records the call as failed - `success=False` plus the error
text - and then propagates unchanged, so the repo's own error handling still runs. That
`success: false` is not decoration: it is what Monitor's built-in "Tool failure" check reads
and what the dashboard's Tool quality column counts.

Prefer `trace_tool_call` over `span.add_tool_call`. It times the call itself, it captures
failures without being asked, and it works the same way on every version that has it - where
`add_tool_call`'s `success=`/`error=` keywords do not exist on the currently published
package (Phase 1b).

**Trace the tool, not the helper.** `lookup_order` hitting a database is a tool call.
`_format_order_line` is not, and a trace with forty spans of string formatting in it is
harder to read than one with none.

---

## Phase 5 - What not to trace

Everything here has a real cost - context in the trace viewer, latency in the request path,
or a support conversation later.

- **Helpers, formatters, parsers, validators.** They are frames, not steps.
- **Hot loops and per-token callbacks.** One span per streamed token is a denial of service
  against your own dashboard.
- **Health and readiness probes.** `/healthz` will out-trace the agent by three orders of
  magnitude and drag the agent's health rate with it.
- **Retries as separate top-level traces.** A retried call belongs inside the run's span.
- **`sync=True` in a request path.** It blocks until the engine acknowledges. It is for smoke
  tests and for the moment you need `span.trace_id` back - not for serving traffic. And when
  you do use it, read `span.trace_id` **after** the `with` block, not inside it: the id is
  assigned on exit, so reading it a line early returns `None` and looks like a failed send.
- **Secrets and personal data in `input`, `output` or `metadata`.** Traces are stored and
  read by judges. If the handler carries an authorization header, an API key or a customer
  record, put the question in `span.input`, not the envelope that contains them.

---

## Phase 6 - Verify the connection, then verify the agent

Two commands, and they answer different questions. Running only the first is the most common
way a job gets reported as finished while the dashboard stays empty.

### 6a. The connection

```bash
<project-interpreter> <skill>/scripts/verify_trace.py
```

This authenticates and reads the project back. That proves the key, the base URL and the engine -
the three settings that stop failing loudly the moment the agent is running - and it closes a loop
that is otherwise open: **trace delivery is fire-and-forget by design**, so a wrong base URL or a
key from another project does not raise. It logs one warning and the traces go nowhere, which
looks exactly like an agent nobody has run yet.

**It writes nothing, and that is deliberate.** The engine serves no route to delete a trace, so a
synthetic one sent to prove a point stays in Live Traces beside the agent's real traffic for good -
three of them, named after this skill, in the project the user just chose for their production
runs. `--self-test` still sends them when there is a reason to: a pipeline where nothing arrives
at all, or an agent that cannot easily be run once. It is opt-in because it cannot be undone.

### 6b. The agent - and the write path with it

A reachable engine proves **nothing** about the code just instrumented, and nothing about
ingestion. Both come from the same place: run the agent's own entry point once, for real, and
grade what it wrote. Those traces belong in the project, which is why they are the ones to
prove the write path with:

```bash
<project-interpreter> <skill>/scripts/verify_trace.py --check <agent-name>
```

| Check | Verdict | What a bad answer means |
|---|---|---|
| traces registered | FAIL | The span never opened - or the process exited before the queue drained (Phase 2). `verify_trace.py` passing while this fails is the flush case, not a wiring case. |
| `input`/`output` are prose | FAIL | A serialised request object is in there. It renders, it looks instrumented, and no evaluation can score it. |
| token counts present | FAIL | The model client is not auto-instrumented (Phase 3). |
| tool calls recorded | WARN | The integration was constructed but never handed to the framework, or Phase 4 was skipped - unless the agent genuinely calls no tools. |
| turns share a session | WARN | `session_id` is not being passed, so every turn is its own conversation (Phase 2) - unless these really were independent one-shot runs. |

Only FAIL moves the exit code, so a pass cannot be assumed from output nobody read. The two
WARNs are the questions the traces cannot answer by themselves: no tool calls is correct for an
agent with no tools, and one session per trace is correct for one-shot runs. **Both are
identical in the data to the wiring being broken**, which is why they are neither green nor red
- answer them from the repo, and say which it was in Phase 7.

Two more judgements no script makes for you:

- **Is there one trace per run, not three?** Two traces for one run is a second top-level span,
  usually a decorator left on a function that is also called directly.
- **Did anything get traced that should not have been?** Phase 5 is that list.

---

## Phase 7 - Report what you did, and what you did not

Say, in this order:

1. Which entry point became the top-level span, and what the agent is named in the dashboard.
2. Which integration was wired up, and which model calls it does *not* cover - streaming, in
   particular.
3. Which tool calls are recorded, and which are left to the framework.
4. What you deliberately did not trace, from Phase 5, and why.
5. Whether the repo now **requires** an AgentX key to start, or degrades to running untraced
   without one - whichever §1c left it as. It is the one change that affects people who never
   asked for tracing.
6. What the two verification passes returned - that the connection check reached the engine,
   and the `--check` verdicts on the agent's own run - and the URL where the traces are.

Then the payoff worth naming: **an evaluation result that carries a `traceId` is judged
against the agent's real execution path**, and one that does not is judged on answer text
alone. A judge working from text alone cannot tell a retrieval-backed citation from an
invented one, and reliably concludes the agent has no working retrieval - a finding about the
wiring, not the agent. If the repo has an evaluation harness, trace its runs with `sync=True`
and attach `span.trace_id` to each result. That is what makes the two halves one picture.
