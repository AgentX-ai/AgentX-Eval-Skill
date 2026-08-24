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
**No git is not a blocker and never changes what gets instrumented** - the traces are just as
real either way. It is worth one question only because tracing edits someone else's source, and
a repo gives them a diff to read it in and a way to put it back.

| What it reports | What to do |
|---|---|
| `NOT A REPOSITORY` | Offer `git init` in one AskUserQuestion, then proceed on whichever answer. |
| `repository with no commits` | Same offer, minus the `git init` - there is still nothing to diff against. |
| `uncommitted changes present` | Do not ask. Say it in one line, so the instrumentation diff is not confused with work already there. |
| clean repository | Nothing to do. |

Two options, both legitimate, with the consequence written into each:

- **`git init` and commit a baseline** - the instrumentation then arrives as a diff someone can
  read, `git checkout .` undoes it, and a branch or a PR is possible later.
- **Carry on without** - say once, in one sentence, that the edits will land in the working tree
  with nothing to compare against and no undo. Then do the whole job anyway.

**Ask once and move on.** If the answer is no, ambiguous, or slow in coming, instrument the repo.
A user who declined git wanted tracing, not a conversation about version control, and stopping to
insist is worse than the missing diff. `.env.agentx` is written with its `.gitignore` entry
regardless, so a later `git init` does not expose the key.

**If they do commit a baseline, look at what goes into it first.** `git status --short`. A first
commit made with `git add -A` in a directory nobody has ever gitignored is how `.env`, `.venv/`,
`node_modules/` and a set of credentials enter someone's history - and history is the one place a
mistake does not simply get deleted. Anything of that shape goes into `.gitignore` first, and say
which ones you added.

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
python3 <skill>/scripts/agentx_key.py --host <address> --json --limit 8
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

The published package and its documentation are not always in step. Concretely, on PyPI
0.6.30 the documented `span.add_tool_call(..., success=False, error=...)` does not exist -
that signature takes only `name`, `input`, `output` and `latency_ms` - so generated code using
those keywords raises `TypeError` inside the user's agent at the first failed tool call.
`tracer.trace_tool_call(...)` records the same `success`/`error` fields on every version that
has it, which is why the brief prefers it everywhere.

One command, before the code. It also confirms which interpreter has the SDK, which is the
other half of the same question.

**Ask the probe; do not hand-roll `inspect`.** When it does not answer what you need, import
from the right place: the tracer's internals are in the **`agentx.tracing.tracer`** submodule -
`Tracer`, `_TraceSpan` - while `agentx.tracing` itself re-exports only `Tracer`, `IngestClient`
and the CI types. `from agentx.tracing import _TraceSpan` is an `ImportError`, and a step that
opens with one reads as a broken skill before it has done anything. The same rule covers the
other helpers: extend the command rather than improvising your own version of it.

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

The last line of Phase 7 is an invitation, not a summary: **`/agentx:run-eval`**. Traces say
what the agent did and what it cost; an evaluation says whether the answers were any good, and
the traces just proven are what it scores against. Offer it in two lines - the command, and
that it builds the dataset itself when there is none (a template, a CSV, or cases curated from
the runs just recorded), so nothing has to be prepared first. Then stop. One offer is an
onboarding step; a second is a sales pitch.

One sentence of why belongs with it, because it is the payoff of the work just done: **an
evaluation result that carries a `traceId` is judged against the agent's real execution
path**, and one that does not is judged on answer text alone - a judge working from text alone
cannot tell a retrieval-backed citation from an invented one, so it reliably concludes the
agent has no working retrieval and may be fabricating tool results. That is a finding about
the wiring, not about the agent.

`/agentx:run-eval` writes a harness that attaches the id for every case. A harness the repo
already has needs the same two things by hand: `sync=True` on the span, and `span.trace_id`
read after the `with` block onto each result. `/agentx:eval-fix` then turns the run that comes
out into a code change - but name only the next command, not the whole roadmap.
