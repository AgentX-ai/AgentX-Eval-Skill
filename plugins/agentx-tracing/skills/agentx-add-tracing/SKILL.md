---
name: agentx-add-tracing
description: >-
  Wire AgentX production tracing into an existing Python agent, end to end: read the project
  API key off the engine, write .env.agentx, install agentx-python from PyPI, initialise the
  SDK once, and instrument the calls that are worth tracing. Use whenever someone wants to add
  tracing, observability, or monitoring to their own agent with AgentX; asks to "trace my
  agent", "instrument this repo", "hook this up to AgentX", or "get my runs into Live Traces";
  or has a self-host engine (AgentX-trace-eval, normally http://localhost:4700) and nothing
  reporting into it yet. Also use when traces are configured but not arriving, since the SDK
  fails silently by design. The core move is one span where the run begins plus one line of
  framework auto-instrumentation - not a decorator on every function.
---

# Put an agent's real runs into AgentX

An evaluation tells you how an agent scored on questions you wrote. A trace tells you what it
actually did on traffic you did not write: the input, the answer, the model calls, the tools,
what each of them cost, and which of them failed. This skill wires the second one into a repo
that has none.

The work is smaller than it looks, and its shape is easy to get wrong. A traced agent is:

- **one span where a run begins** - the request handler, the task, the CLI main;
- **one line of auto-instrumentation** for whatever calls the model;
- **tool calls recorded** where the repo dispatches its own tools.

Nesting is automatic. Any span opened while another is active becomes its child, and so does
every call through a patched client. So decorating more functions does not produce a richer
trace - it produces a flat list of Python frames where a trajectory should be. **The failure
mode this skill exists to avoid is over-instrumentation**, not under-.

**Python only.** The tracing SDK is `agentx-python` on PyPI; there is no JavaScript package.
If the repo has no Python in it, say so plainly and stop rather than improvising.

---

## Do this

Follow `references/instrumentation-brief.md`, in order, against the repo you are inside. It
is written to be executed and each phase ends in something checkable.

| Phase | What happens |
|---|---|
| 0 | Survey the repo - framework, entry points, whether it is already traced |
| 1 | Key into `.env.agentx`, dependency installed, bootstrap module copied in |
| 2 | One span at the entry point |
| 3 | Auto-instrument the model client |
| 4 | Tool calls, where the repo rolls its own |
| 5 | Deliberately leave the rest alone |
| 6 | Send a real trace and read it back |
| 7 | Report what is traced and what is not |

Three helpers do the parts that should not be improvised:

```bash
<skill>/scripts/detect_stack.py .          # framework, entry points, existing instrumentation
<skill>/scripts/agentx_key.py --write-env  # verified key, .env.agentx, gitignored
<skill>/scripts/verify_trace.py            # send one trace, fetch it back by id
<skill>/scripts/verify_trace.py --capabilities   # what the INSTALLED sdk supports
```

`<skill>/assets/agentx_tracing.py` is the bootstrap module to copy into the repo. Copy it; do not
rewrite it from memory. What it guards against is in the brief's Phase 1c.

---

## Ask which engine, once, before the first read

`http://localhost:4700` is right for most people and wrong for anyone whose engine runs on
another box - and which engine a key came from is invisible until traces fail to appear in
the dashboard someone is watching. So **open with one AskUserQuestion**, three choices:

| Option | Address | Pass as |
|---|---|---|
| **local** (default) | `http://localhost:4700` | `--host local` |
| **agentx** | `https://api.agentx.so` | `--host agentx` |
| **other** | whatever the user types | `--host <address>` |

Add a fourth only when `$AGENTX_HOST` or `$AGENTX_API_BASE_URL` already names something else -
read them before asking and quote the value, since someone who configured it once should not
retype it. Skip the question entirely when the user named an address in the same breath as the
request, because an answer just given is not a question.

A scheme-less address is completed from the local default's shape, so `10.0.0.5` means
`http://10.0.0.5:4700`. A scheme the user supplies is left alone, so a reverse-proxied
`https://traces.example.com` stays on 443. **Plain `http://` on an internal network is a normal
answer, not a mistake to correct.**

---

## Identify the engine, then ask which project

`agentx_key.py` opens with one unauthenticated call to `/auth/config`, and that single answer
decides everything downstream: which engine this is, whether a key can be had without asking,
and **whether the user gets to choose a project at all**.

| Engine | `/auth/config` says | Key without asking? | Choose a project? |
|---|---|---|---|
| self-host, `AGENTX_AUTH=disabled` (the default) | `mode: disabled` **plus the default project's key** | yes, that key | **yes** - `/projects` returns every project with its key |
| self-host, `AGENTX_AUTH=enabled` | `mode: enabled`, no key | no | no - listing needs a signed-in session, so the dashboard picks |
| hosted (`api.agentx.so`) | no such route | no | no - the key selects the workspace on its own |

The script reports which row it is on, in words, on its first two lines of stderr.

### When projects can be listed, ask - do not just take the default

**This is an AskUserQuestion, and it is the one place the user's intent cannot be inferred.**
The key *is* the project selector: every trace lands in exactly one project, the one whose key
sent it, and is invisible under every other key. Defaulting silently puts a month of a
production agent's traffic somewhere the user did not choose, and there is no move command.

Run the script once with `--json` - it returns the engine's verdict and the project list in the
same call - then ask, with the engine's default marked as such:

```bash
<skill>/scripts/agentx_key.py --host <address> --json --limit 8
```

Build the options from `projects[]`: name, and `(default)` where `isDefault`. Two more things
belong in that question:

- **If the repo already runs AgentX evaluations, recommend that project** and say why in the
  option description - traces and runs in different projects never appear next to each other,
  which defeats most of the reason to have both. `detect_stack.py` tells you when that is the
  case.
- **Offer "a new project for this app"** when the engine is self-host in disabled mode, since
  `--create-project <name>` works there without credentials. Say in the description that it
  **writes a project row the engine cannot delete** - that is a real consequence, not a
  footnote, and it belongs where the user is deciding.

Then pass the answer through: `--write-env .env.agentx --project <id>`. Prefer the id; project
names are not unique on self-host, and the script refuses an ambiguous name rather than
guessing.

**When projects cannot be listed, do not ask.** Say in one line which row of the table you are
on and that the key already fixes the destination, then move on. A question the user cannot act
on is worse than no question.

### Two traps in key resolution

- **`~/.agentx/config.json` records whichever engine last ran on this machine**, which need not
  be the engine you are pointing at. A Docker instance keeps its database in its own volume and
  mints its own keys. Every candidate is verified with a real authenticated read before use, and
  a stale one is reported as stale rather than written into the project.
- **The engine's handout is always the *default* project.** Right for a fresh install, wrong for
  anyone who has already chosen where their data goes - which is exactly why it is last in the
  resolution order, and why the question above exists.

**Never print a key.** The scripts mask every key they display and write the selected one
straight to disk, so the secret goes from the engine to `.env.agentx` without passing through
the conversation. `.env.agentx` is written at mode 0600 and added to `.gitignore`.

---

## Two things that fail silently

Both are the same design decision seen from two sides: **trace delivery is fire-and-forget,
because tracing must never block or break the agent it watches.** Nothing here raises.

- **`AGENTX_API_BASE_URL` unset.** The SDK defaults to the hosted platform, so a self-host
  user's traces leave for `api.agentx.so` and never arrive anywhere they are looking. This is
  why the base URL is written into `.env.agentx` next to the key, not left to a default.
- **A key from a different project.** The traces are accepted and stored, and are invisible to
  the key doing the looking.

Both look identical from the outside: an agent that runs fine and a dashboard that stays empty.
`verify_trace.py` exists to close that loop before anyone believes the wiring - it sends one
synchronous trace and then fetches it back by id.

There is a third, opposite failure that does raise: **`AgentX.from_env()` throws
`AgentXAuthError` when `AGENTX_API_KEY` is absent**, at import time, because the constructor
eagerly builds its evaluations client. Unguarded, that turns a missing secret in CI or on a
teammate's checkout into a crash in *their* application. The bootstrap module in `assets/`
degrades to a no-op tracer instead. Do not skip it.

---

## Check the installed SDK before generating code against it

```bash
<project-interpreter> <skill>/scripts/verify_trace.py --capabilities
```

The published package and its documentation are not always in step. Concretely, on PyPI
0.6.30 the documented `span.add_tool_call(..., success=False, error=...)` does not exist -
that signature takes only `name`, `input`, `output` and `latency_ms` - so generated code using
those keywords raises `TypeError` inside the user's agent at the first failed tool call.
`tracer.trace_tool_call(...)` records the same `success`/`error` fields on every version that
has it, which is why the brief prefers it everywhere.

One command, before the code. It also confirms which interpreter has the SDK, which is the
other half of the same question.

---

## Reading a finished job

Before calling it done, check the trace itself rather than the diff:

- **Is there one trace per run?** Two means a second top-level span, usually a decorator left
  on a function that is also called directly.
- **Does the span tree have children?** Flat means either no span was open when the patched
  client ran, or an integration was constructed but never handed to the framework.
- **Are `input` and `output` the question and the answer?** A serialised request object in
  `input` is a trace that cannot be evaluated later, only looked at.
- **Do token counts appear?** If not, the model client is not auto-instrumented, and the run
  has no cost attached to it.
- **Is anything traced that should not be?** Health probes, per-token callbacks and helper
  functions all crowd out the run they surround. The brief's Phase 5 is the list.
- **Did secrets get in?** `input`, `output` and `metadata` are stored and read by judges.

---

## The payoff worth naming at the end

An evaluation result that carries a `traceId` is judged against the agent's **real execution
path**. One that does not is judged on answer text alone - and a judge working from text alone
cannot tell a retrieval-backed citation from an invented one, so it reliably concludes the
agent has no working retrieval and may be fabricating tool results. That is a finding about the
wiring, not about the agent.

So if the repo has an evaluation harness, trace its runs with `sync=True` and attach
`span.trace_id` to each result. Tracing and evaluation stop being two features and become one
picture. The `agentx-eval-fix` skill picks the story up from there.
