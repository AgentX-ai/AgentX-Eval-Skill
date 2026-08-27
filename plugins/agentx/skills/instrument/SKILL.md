---
name: instrument
description: >-
  Set an existing Python agent up on AgentX, end to end: read the project API key off the
  engine, write .env.agentx, install agentx-python from PyPI, initialise the SDK once,
  instrument the calls that are worth tracing, and then prove it by running the agent and reading
  its own traces back. Use whenever someone wants to set up, initialise, add tracing to, or monitor their
  own agent with AgentX; asks to "trace my agent", "instrument this repo", "hook this up to
  AgentX", "set up AgentX here", or "get my runs into Live Traces"; or has a self-host engine
  (AgentX-trace-eval, normally http://localhost:4700) and nothing reporting into it yet. Also
  use when traces are configured but not arriving, since the SDK fails silently by design. The
  core move is one span where the run begins plus one line of framework auto-instrumentation -
  not a decorator on every function - and it is not finished until a trace has been fetched back.
version: 2.8.4
metadata:
  author: AgentX <marcin@agentx.so>
  tags:
    - agentx
    - tracing
    - observability
    - self-host
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

## The workflow

Carry this out yourself, in order. Do not delegate it to a subagent. Anything the user typed
after the command name is extra instruction: `$ARGUMENTS`.

**1. Ask where the engine is, once.** See the next section. One question, before anything else,
because that address decides where every trace lands and a wrong one does not error.

**2. Survey the repo before touching it.**

```bash
python3 <skill>/scripts/detect_stack.py .
```

Report what it found in three lines: the framework and its integration, the entry point a run
begins at, and whether anything is already traced. Then:

- **No Python files → stop.** The SDK is Python-only; say that rather than improvising.
- **Already traced → do not re-instrument.** Extend what is there, and say so.
- **Several plausible entry points → ask which one is the agent**, with AskUserQuestion, listing
  the starred candidates. It is the one judgement the script cannot make, and getting it wrong
  makes every trace a fragment or a duplicate. One obvious candidate means no question.

**3. Offer git, then carry on either way.** `detect_stack.py` reports `git` for this.
**No git is not a blocker and never changes what gets instrumented** - it is worth one
AskUserQuestion only because tracing edits someone else's source. Offer `git init` plus a
baseline commit where there is no repository; say uncommitted changes out loud rather than
asking. **Ask once and move on** - if the answer is no, ambiguous, or slow, instrument the
repo anyway. `.env.agentx` is written with its `.gitignore` entry regardless, so a later
`git init` does not expose the key. `references/preflight-brief.md` has the per-state table
and the `git add -A` hazard to check before any baseline commit.

**4. Key, dependency, client.** Pick the project (see below), install `agentx-python` with the
extra `detect_stack.py` named, and add it to the repo's manifest. Then initialise the SDK **once**,
in a file the repo already has - the one that reads config and hands out clients. Two lines, no
new module; the brief's §1c has them. Then check what you actually got:

```bash
<project-interpreter> <skill>/scripts/verify_trace.py --capabilities
```

Generate code against what that prints, not against a README.

**5. Instrument.** Follow `references/instrumentation-brief.md`, phases 2 through 5, exactly.

| Phase | What happens |
|---|---|
| 0 | Survey - framework, entry points, whether it is already traced |
| 1 | Key into `.env.agentx`, dependency installed, SDK initialised once in an existing module |
| 2 | One span at the entry point |
| 3 | Auto-instrument the model client |
| 4 | Tool calls, where the repo rolls its own |
| 5 | Deliberately leave the rest alone |
| 6 | Prove the connection, then run the agent and grade its own traces |
| 7 | Report what is traced and what is not, then offer `/agentx:run-eval` |

Phase 5 - the list of things to leave alone - is as much of the job as the phases that add code.

**Keep the comments short.** The instrumentation is three or four lines in a file someone else
wrote; comments about it should not outnumber it. One line where the code cannot say it itself,
none where it can, and no paragraph of rationale - that goes in the Phase 7 report, which is
read once, rather than into source, which is maintained forever. The brief's *House style for
the lines you add* has the same edit written both ways.

**6. Prove it, in two stages. Neither one is optional.**

```bash
<project-interpreter> <skill>/scripts/verify_trace.py            # 1. the connection
<project-interpreter> <skill>/scripts/verify_trace.py --check <agent-name>   # 2. the agent
```

The first authenticates and reads the project back, which proves the key, the base URL and the
engine - the three settings that fail silently once the agent is running. It **writes nothing**,
deliberately: **the engine serves no route to delete a trace**, so anything sent to prove a point
sits in Live Traces beside the agent's real traffic permanently. `--self-test` will send three
synthetic traces if you need them - a pipeline where nothing arrives at all, or an agent you
cannot easily run - and it says on the way in that they are permanent.

**A reachable engine proves nothing about the agent's own code**, which is the half people skip.
So then run the agent's real entry point once - that is the write path proven on traffic that
belongs in the project - and grade what it produced:

| What `--check` asks | What a bad answer means |
|---|---|
| Did any trace register? | The span never opened, or the process exited before the queue drained (Phase 2) |
| Are `input`/`output` prose? | A serialised request object is in there, and no judge can score it |
| Are token counts present? | The model client is not auto-instrumented (Phase 3) |
| Are tool calls recorded? | Phase 3's integration was built but never handed to the framework, or Phase 4 was skipped |
| Do turns share a session? | `session_id` is not being passed, so every turn is its own conversation |

The last two come back as **WARN**, not FAIL, and only FAIL moves the exit code. An agent with
no tools should record no tool calls, and four independent one-shot runs should each have their
own session - in the data those are indistinguishable from broken wiring. **A WARN is a question
addressed to you**, and you are the one who knows which kind of agent this is: read it against
what the repo actually does and say which it was in the Phase 7 report.

**Do not report success on the strength of the diff.** The SDK does not raise when it is
misconfigured, so the only evidence tracing works is a trace you fetched back.

**7. Say what is traced, and what is not.** The entry point that became the span, the agent's
name in the dashboard, the integration wired up and what it does not cover (streaming, in
particular), what you deliberately left untraced, whether the repo now needs an AgentX key to
start, and the URL where the traces are. Then end on the next command - the last section
has the wording.

### The helpers

```bash
python3 <skill>/scripts/detect_stack.py .   # framework, entry points, existing instrumentation
python3 <skill>/scripts/agentx_key.py --json   # engine verdict, verified key, project list
<project-interpreter> <skill>/scripts/verify_trace.py   # authenticate and read the project; writes nothing
<project-interpreter> <skill>/scripts/verify_trace.py --check <agent-name>   # grade the agent's own runs
<project-interpreter> <skill>/scripts/verify_trace.py --capabilities   # what the INSTALLED sdk supports
```

`<skill>` is this skill's own directory - `${CLAUDE_PLUGIN_ROOT}/skills/instrument` under the
plugin, or wherever `npx skills add` unpacked it. Resolve it once and reuse it.

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
and **whether the user gets to choose a project at all**. The script reports which case it is
on, in words, on its first two lines of stderr. §1a of the brief has the three fields to read
off `--json` and the engine modes behind them.

**When projects can be listed, ask - do not just take the default.** This is an
AskUserQuestion, and it is the one place the user's intent cannot be inferred. The key *is*
the project selector: every trace lands in exactly one project, the one whose key sent it, and
is invisible under every other key. Defaulting silently puts a month of a production agent's
traffic somewhere the user did not choose, and there is no move command. §1a has the options to
build, including the two that are easy to miss - the project the repo's evaluations already
use, and "a new project for this app", which writes a row the engine cannot delete.

**When projects cannot be listed, do not ask.** Say in one line which case you are on and that
the key already fixes the destination, then move on. A question the user cannot act on is worse
than no question.

`references/preflight-brief.md` names the two traps in key resolution:
`~/.agentx/config.json` **records whichever engine last ran on this machine**, and the
engine's handout is always the ***default*** project - which is why it is last in the
resolution order, and why the question above exists.

**Nothing prints anything derived from a key** - not a masked form, not a hash. Every line
that might have wanted one already carries the project id, which identifies the project better
and is not a secret. The key goes from the engine to `.env.agentx` without passing through the
conversation, and `assert_no_secret()` enforces that at runtime rather than trusting the review. `.env.agentx` is written at mode 0600 and added to `.gitignore`.

---

## Three things that fail silently

All three are the same design decision seen from different sides: **trace delivery is
fire-and-forget, because tracing must never block or break the agent it watches.** Nothing
here raises.

- **`AGENTX_API_BASE_URL` unset.** The SDK defaults to the hosted platform, so a self-host
  user's traces leave for `api.agentx.so` and never arrive anywhere they are looking. This is
  why the base URL is written into `.env.agentx` next to the key, not left to a default.
- **A key from a different project.** The traces are accepted and stored, and are invisible to
  the key doing the looking.
- **The process exits before the queue drains.** Traces ship on a background *daemon* thread
  and the SDK registers no `atexit` hook, so interpreter shutdown kills it mid-queue rather
  than waiting. A CLI, a cron job, a serverless handler or a test run can therefore finish
  with its last trace - or every trace - never sent. One `tracer.flush()` at the entry point
  is the whole fix; see the brief's Phase 2.

All three look identical from the outside: an agent that runs fine and a dashboard that stays empty.
`verify_trace.py` exists to close that loop before anyone believes the wiring - it authenticates
and reads the project first, then grades the agent's own runs with `--check`.

There is a fourth, opposite failure that does raise: **`AgentX.from_env()` throws
`AgentXAuthError` when `AGENTX_API_KEY` is absent**, at import time, because the constructor
eagerly builds its evaluations client. That is the one failure here that announces itself, and
for a repo one team runs it is the right trade - AgentX becomes a dependency, and a missing key
stops the process instead of quietly untracing it. It is the wrong trade when other people have
to run this code without a key: CI, an open-source repo, a deploy where the secret lands later.
The brief's §1c has the one-line guard for that case, and Phase 7 says to name whichever one you
chose.

---

## Check the installed SDK before generating code against it

```bash
<project-interpreter> <skill>/scripts/verify_trace.py --capabilities
```

One command, before the code. The published package and its documentation are not always
in step, and it also confirms which interpreter has the SDK - the other half of the same
question. **Ask the probe; do not hand-roll `inspect`**, and when it does not answer what
you need, import from the right place: the tracer's internals are in
**`agentx.tracing.tracer`**, so `from agentx.tracing import _TraceSpan` is an `ImportError`.
`references/preflight-brief.md` has the version specifics behind both rules.

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

## End the report on the next command

The last line of Phase 7 is an invitation, not a summary: **`/agentx:run-eval`**. Two lines -
the command, and that it builds the dataset itself when the repo has none - then stop. One
offer is an onboarding step; a second is a sales pitch.

It is worth offering because it is the payoff of the work just done: an evaluation whose
results carry a `traceId` is judged against the agent's real execution path rather than its
answer text. Phase 7 of the brief has the sentence to say it in, and what a run without those
ids concludes instead.
