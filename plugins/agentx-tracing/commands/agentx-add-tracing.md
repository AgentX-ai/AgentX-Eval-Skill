---
description: Add AgentX tracing to this project - key, SDK, and instrumentation where it belongs
argument-hint: [engine address or extra instructions]
---

Wire AgentX production tracing into the repo in the current working directory, using the
`agentx-add-tracing` skill. The engine is local unless the user says otherwise.

Additional instructions from the user (may be empty): $ARGUMENTS

Carry this out yourself, in order. Do not delegate it to a subagent.

## 1. Ask where the engine is, once

**Before anything else, ask with AskUserQuestion which engine the traces should go to.** One
question, at the start, and then not again.

It goes first because the address decides which database every trace lands in, and it is
invisible until someone stares at an empty dashboard. Trace delivery is fire-and-forget by
design - it must never block or break the agent it watches - so a wrong address does not
error. It logs one warning and the traces go somewhere else. Instrumenting the whole repo
against the wrong engine costs the entire job.

Three choices, so it is a menu and not a prompt:

| Option | Address | Pass as |
|---|---|---|
| **local** (default) | `http://localhost:4700` | `--host local` |
| **agentx** | `https://api.agentx.so` | `--host agentx` |
| **other** | whatever they type | `--host <what they typed>` |

Put **local** first and mark it the default. Add a fourth option only when `$AGENTX_HOST` or
`$AGENTX_API_BASE_URL` already names something else - read them *before* asking and quote the
value literally. Skip the question entirely when the user named an address in `$ARGUMENTS`.

Then pass the answer as `--host <address>` on every script call. **Each Bash call is a fresh
shell**, so there is no exporting it once.

## 2. Survey the repo before touching it

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/agentx-add-tracing/scripts/detect_stack.py .
```

Report what it found in three lines: the framework and its integration, the entry point a run
begins at, and whether anything is already traced.

**If it reports no Python files, stop.** The tracing SDK is Python-only - `agentx-python` on
PyPI, no JavaScript equivalent. Say that plainly instead of improvising something else.

**If it is already traced, do not re-instrument it.** Extend what is there, and say so.

**If more than one entry point is plausible, ask which one is the agent** - with
AskUserQuestion, listing the starred candidates. That is the one judgement the script cannot
make, and getting it wrong means every trace is a fragment or a duplicate. One question; if
there is an obvious single starred candidate, do not ask at all.

## 3. Key, dependency, bootstrap

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/agentx-add-tracing/scripts/agentx_key.py \
  --host <the address from step 1> --list-projects
python3 ${CLAUDE_PLUGIN_ROOT}/skills/agentx-add-tracing/scripts/agentx_key.py \
  --host <the address from step 1> --write-env .env.agentx --project <id-or-name>
```

The script verifies every candidate key against that engine before using it, writes
`.env.agentx` at mode 0600, and adds it to `.gitignore`. **Never print a key** - the scripts
mask what they display and write the chosen one straight to disk.

If the repo already runs AgentX evaluations, put the traces in that same project, or the two
halves never appear next to each other. If no key resolves, relay the script's message: it
names where a key actually comes from. Only offer `--create-project` as an explicit choice -
it writes a project row the engine cannot delete.

Then install `agentx-python` (with the extra `detect_stack.py` named), add it to the repo's
manifest, and copy `${CLAUDE_PLUGIN_ROOT}/skills/agentx-add-tracing/assets/agentx_tracing.py`
into the repo. Copy that file; do not rewrite it from memory. Then:

```bash
<project-interpreter> ${CLAUDE_PLUGIN_ROOT}/skills/agentx-add-tracing/scripts/verify_trace.py --capabilities
```

The published package and its docs are not always in step. Generate code against what this
prints, not against a README.

## 4. Instrument

Follow `${CLAUDE_PLUGIN_ROOT}/skills/agentx-add-tracing/references/instrumentation-brief.md`
exactly, phases 2 through 5.

The single most important thing in it: **one span where the run begins, one line of framework
auto-instrumentation, and nothing else.** Nesting is automatic, so a decorator on every
function does not make the trace richer - it makes it a list of Python frames instead of a
trajectory. Phase 5 is the list of things to deliberately leave alone, and it is as much of
the job as phases 2 to 4.

## 5. Prove it, with a real trace

```bash
<project-interpreter> ${CLAUDE_PLUGIN_ROOT}/skills/agentx-add-tracing/scripts/verify_trace.py
```

Then run the agent's own entry point once and look at what arrived: one trace per run, model
and tool calls nested under it, `input` and `output` readable as a question and an answer,
token counts present. A flat trace means the span and the patched client never overlapped.

Do not report success on the strength of the diff. The SDK does not raise when it is
misconfigured, so the only evidence that tracing works is a trace you fetched back.

## 6. Say what is traced, and what is not

Name the entry point that became the span, the agent's name in the dashboard, the integration
wired up and what it does not cover (streaming, in particular), what you deliberately left
untraced, and the URL where the traces are.

Then, if the repo has an evaluation harness, name the payoff: a result carrying a `traceId` is
judged against the agent's real execution path, and one without it is judged on answer text
alone - which is how a judge concludes an agent has no retrieval when it plainly does. Offer
to wire `sync=True` and `span.trace_id` into that harness as a follow-up. Do not do it
unasked; it changes the evaluation path, not the tracing one.
