# agentx-add-tracing

A Claude Code skill that wires [AgentX](https://github.com/AgentX-ai/AgentX-trace-eval)
production tracing into a Python agent that has none — key, SDK, and instrumentation
where it belongs.

```
/agentx-add-tracing ─► survey the repo ─► key into .env.agentx ─► one span at the entry point
                                                                          │
        a trace you fetched back ◄── verify ◄── auto-instrument the model client
```

## The problem it solves

Adding tracing looks like a five-minute job and then goes wrong in three specific ways.

**It gets put in the wrong places.** The instinct is a decorator on every function. But
nesting is automatic in the AgentX SDK — any span opened inside an active span becomes its
child, and so does every call through an auto-instrumented client — so decorating everything
produces a flat list of Python frames where a trajectory should be. What a run actually needs
is one span where it begins, one line for the model client, and tool calls where the repo
dispatches its own.

**It fails silently.** Trace delivery is fire-and-forget by design: tracing must never block
or break the agent it watches, so nothing raises. `AGENTX_API_BASE_URL` unset means the SDK
sends to the hosted platform, and a self-host user's traces leave for `api.agentx.so` and
never arrive. A key from a different project means they arrive and are invisible. Both look
identical from outside — an agent that runs fine and a dashboard that stays empty.

**Or it takes the app down.** `AgentX.from_env()` *raises* when `AGENTX_API_KEY` is absent,
at import time, because the constructor eagerly builds its evaluations client. Unguarded,
that turns a missing secret in CI or on a teammate's checkout into a crash in their
application — tracing breaking the thing it was added to watch.

The skill handles all three: it puts one span where the run begins, writes the base URL next
to the key so there is no default to get wrong, ships a bootstrap module that degrades to a
no-op tracer instead of raising, and finishes by sending a real trace and fetching it back.

## Install

```bash
claude plugin marketplace add AgentX-ai/AgentX-Eval-Skill
claude plugin install agentx-tracing@agentx
```

**Restart Claude Code afterwards** — slash commands are loaded at startup.

This repo is a Cursor marketplace as well — **Dashboard → Plugins → Team Marketplaces →
Add Marketplace → Import from Repo**, and this plugin comes along with it.

Or, for any agent that reads the Agent Skills standard:

```bash
npx skills add AgentX-ai/AgentX-Eval-Skill --skill agentx-add-tracing
```

## Run it

From inside the repo that holds your agent:

```
/agentx-add-tracing
```

It asks one question up front — which engine, `local` by default — and one more only if the
repo has several plausible entry points. Then it surveys, wires, and proves it with a trace
it fetched back by id.

## What it writes into your repo

| File | What it is |
|---|---|
| `.env.agentx` | The project key and `AGENTX_API_BASE_URL`, mode 0600, added to `.gitignore` |
| `agentx_tracing.py` | The bootstrap module: loads that file, builds one client, degrades to a no-op |
| your entry point | One `tracer.trace(...)` span, and one line of framework auto-instrumentation |

Nothing else. Phase 5 of the brief is a list of things it deliberately leaves alone —
health probes, per-token callbacks, helper functions, retries — because each of them
crowds out the run it surrounds.

## Frameworks

Auto-instrumentation covers LangChain/LangGraph, CrewAI, the OpenAI Agents SDK, raw OpenAI
and Anthropic clients, Google ADK, Google GenAI, LiteLLM, LlamaIndex and AutoGen. Plain
Python needs no integration at all — the entry-point span is the whole story.

`scripts/detect_stack.py` decides which applies by parsing the repo with `ast`, so it never
imports or runs the code it is surveying.

## Requirements

Python 3.9+, and a reachable AgentX engine — self-host on `http://localhost:4700` by default,
or the hosted platform. The scripts need nothing beyond the standard library; the repo under
test needs `agentx-python` from PyPI, which the skill installs with whichever extra its
framework calls for.

Project API keys are **copy-pasted, not fetched** — the engine deliberately removed its old
unauthenticated `/dev/bootstrap` handout. `agentx_key.py` reads `$AGENTX_API_KEY`, then
`~/.agentx/config.json`, verifying each against the engine you named before it is used,
because that file records whichever engine last ran on the machine and not necessarily the
one you are talking to. On self-host in its default auth mode it can also mint a fresh
project with `--create-project`, which the skill offers as a choice and never as a fallback,
because the engine serves no way to delete one afterwards.

No key is ever printed. The scripts mask what they display and write the selected one
straight to disk.

## What's in here

| Path | What it is |
|---|---|
| `commands/agentx-add-tracing.md` | The `/agentx-add-tracing` slash command — the normal entry point |
| `skills/agentx-add-tracing/SKILL.md` | The model: what to trace, and the two silent failures |
| `skills/agentx-add-tracing/references/instrumentation-brief.md` | The core artifact. Eight phases, executed in order |
| `skills/agentx-add-tracing/assets/agentx_tracing.py` | Bootstrap module copied into the target repo |
| `skills/agentx-add-tracing/scripts/detect_stack.py` | Framework, entry points and existing instrumentation, via `ast` |
| `skills/agentx-add-tracing/scripts/agentx_key.py` | Verified key resolution, project selection, `.env.agentx` |
| `skills/agentx-add-tracing/scripts/verify_trace.py` | One real trace, fetched back — plus `--capabilities` |

## Why `--capabilities` exists

The published package and its documentation are not always in step. On PyPI 0.6.30 the
documented `span.add_tool_call(..., success=False, error=...)` does not exist — that
signature takes only `name`, `input`, `output` and `latency_ms` — so code generated from the
docs raises `TypeError` inside the agent at the first failed tool call.

```bash
.venv/bin/python verify_trace.py --capabilities
```

One command, before any code is generated, reporting what the installed SDK actually
supports. `tracer.trace_tool_call(...)` records the same `success`/`error` fields on every
version that has it, which is why the brief prefers it everywhere.

## Next

An evaluation result carrying a `traceId` is judged against the agent's real execution path;
one without it is judged on answer text alone, and a judge working from text alone reliably
concludes an agent has no retrieval when it plainly does. Once tracing is in,
[`agentx-eval-fix`](../agentx-eval-fix) picks the story up from there.
