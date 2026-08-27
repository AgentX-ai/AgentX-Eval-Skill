# Preflight: what to check before writing a line of instrumentation

Two checks that cost a debugging session each when skipped. `SKILL.md` says to run them;
this file says what they are guarding against. Read it before generating SDK code, and
whenever a key resolves to something you did not expect.

## Offering git before you edit someone else's source

`detect_stack.py` reports `git` for this.
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

---

## Two traps in key resolution

- **`~/.agentx/config.json` records whichever engine last ran on this machine**, which need not
  be the engine you are pointing at. A Docker instance keeps its database in its own volume and
  mints its own keys. Every candidate is verified with a real authenticated read before use, and
  a stale one is reported as stale rather than written into the project.
- **The engine's handout is always the *default* project.** Right for a fresh install, wrong for
  anyone who has already chosen where their data goes - which is exactly why it is last in the
  resolution order, and why the question above exists.

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
