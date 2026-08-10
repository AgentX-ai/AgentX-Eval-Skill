# agentx-eval-fix

A Claude Code skill that turns an evaluation on a local **AgentX self-host**
engine into a triaged code fix, then re-runs the evaluation on the same dataset
so the before and after numbers actually mean something next to each other.

```
evaluation ──► triage against the real source ──► mapping table ──► you approve
                                                                       │
               v1 vs v2 comparison ◄── re-run on same dataset ◄── fixes on a branch
```

Targets [AgentX-trace-eval](https://github.com/AgentX-ai/AgentX-trace-eval), the
self-hostable Trace/Evaluate/Monitor engine, on `http://localhost:4700` by
default. Not the hosted platform — several endpoints differ.

## The problem it solves

An AgentX analysis is written by a judge that saw your agent's **answers** and
nothing else. Not the source. Not the tool definitions. Not the retrieval
config. So it describes symptoms accurately and prescribes badly, and you are
left holding a document that says what was wrong with the output but not what
to change in the code.

Here is a real case. An agent scored **1.86 out of 10**. Every one of its seven
answers came back cut off mid-word, so the judge's top recommendation was:

> Introduce and enforce a completion check that discourages truncation and
> requires the agent to finish its explanations.

Reasonable, and useless. The agent was already producing complete 800-character
answers ending on a full stop. The evaluation harness had this line:

```python
"output": out["text"][:MAX_OUTPUT_CHARS],   # MAX_OUTPUT_CHARS = 400
```

The defect was one line, in a file the report never mentions, in a component
the judge cannot see. Four of that report's five recommendations were
downstream of it. Deleting the slice took the agent to 10.00.

**That is the gap this skill closes.** It treats the report's evidence as
reliable and its recommendations as hypotheses, then checks each one against
the code before touching anything.

## How it works

### 1. It reads the evaluation straight off your engine

```bash
python3 scripts/fetch_analysis.py --list
python3 scripts/fetch_analysis.py <evaluation_id> --write-export eval-analysis/exports/
```

Standard library only — no `agentx-python`, no virtualenv, no `.env`. It resolves
the base URL from `$AGENTX_API_BASE_URL` (default `http://localhost:4700/api/v1`)
and the key from `$AGENTX_API_KEY`, falling back to asking the engine itself via
`GET /dev/bootstrap`.

What it pulls together, which no single endpoint gives you:

| From | What |
|---|---|
| `GET /evaluate/{id}` | stored per-result ratings, judge justifications, similarity scores, code-scorer results, the analysis narrative |
| `GET /custom-agent-evaluations/datasets/{id}` | the questions, expected results and per-case judge guidelines |
| `GET /evaluate/analyze/{id}/metrics` | multi-judge agreement per sampled answer |

It writes a markdown export in the shape the triage brief reads, plus a
**Grading criteria** section the dashboard has no equivalent of.

### 2. It gets the rubric right, including which rubric

Self-host splits the grading surface. The criteria come from the run's grading
config — which may be a standalone one that **overrides** the dataset's own
criteria — while `expectedResults` and `judgeGuideline` always come from the
dataset. The judge prompt, judge model and code scorers live only on the config.

Read the wrong half and you will reject a good recommendation for conflicting
with criteria that never graded the run. The export labels which is which and
prints the overridden ones under a heading saying so.

### 3. It triages, and writes down its reasoning before changing code

Every numbered recommendation gets one of five verdicts:

| Verdict | Meaning |
|---|---|
| `apply` | Right, as written |
| `apply-modified` | Real problem, wrong remedy. Keep the observation, change the fix |
| `reject-wrong-premise` | Assumes something untrue about the code |
| `reject-already-done` | Asks for something that already exists |
| `reject-harmful` | Following it would lower the score |

Every rejection must cite the `file:line` or the rubric string that disproves
it. An assertion without evidence is not a verdict.

Then a second table: **defects the report could not see.** An answers-only
judge structurally cannot observe retrieval width, tool wiring, sampling
parameters, or whether a tool's output ever reaches the model. There is almost
always something here, and it is often the thing that actually matters.

### 4. It works on a branch, in a worktree

Your checkout is never touched. Fixes land in `.worktrees/eval-fix-<EVAL_ID>`
on branch `eval-fix/<EVAL_ID>`, and can be pushed and opened as a PR.

### 5. You approve, then it re-runs

The mapping table is a hard stop. Everything before it is free; the re-run
costs a full model pass plus a judge pass. See "What to check" below.

## Install

Clone straight into your skills directory:

```bash
git clone git@github.com:AgentX-ai/AgentX-Eval-Skill.git \
  ~/.claude/skills/agentx-eval-fix
```

Or per-project, so it travels with the repo. If you do that, keep it out of the
repo under test, because the repo is the thing being measured and tooling in
its `git status` ends up in the diff a reviewer reads:

```bash
git clone git@github.com:AgentX-ai/AgentX-Eval-Skill.git \
  <your-repo>/.claude/skills/agentx-eval-fix
echo '.claude/skills/agentx-eval-fix/' >> <your-repo>/.git/info/exclude
```

`.git/info/exclude` is per-clone and never committed, which is what you want.

## Run it

From inside the repo that holds your agent:

```
Use the agentx-eval-fix skill. Follow references/triage-brief.md exactly,
in order, against this repo. The analysis is evaluation oE1YMG5wqmu4j2bhTtw1X.
```

That is the whole invocation. It fetches the evaluation, reads the source,
writes the mapping table, applies what survived, and stops.

Review the table it leaves at `eval-analysis/mapping-<EVAL_ID>.md`. When you
are happy:

```
Follow references/eval-brief.md and re-run the evaluation.
```

You get `eval-analysis/v2-report-<EVAL_ID>.md` with the before and after side
by side.

## What to check before you approve

The checkpoint exists so a human looks. Five things worth thirty seconds:

- **Does every recommendation have a verdict?** A skipped row is an unexamined
  claim.
- **Are the rejections evidenced** with a file and line, or merely asserted?
- **Is the second table empty?** That usually means the code was skimmed rather
  than read.
- **Did it add tools, or add hedging language?** Both are common wrong turns.
  Adding tools also breaks the comparison, since runs record a tool count.
- **Are there `RUBRIC-CONFORMING` rows?** These are the ones only you can
  settle. They mark a value changed purely because the expected results said
  something different, where nothing in the repo could confirm which figure is
  right. If the rubric has a typo, the change makes your agent confidently
  wrong to real users, the score goes *up* anyway, and nothing downstream will
  ever catch it. Go and check the real policy.

## Six things that will bite you

**A result with no `traceId` is a score with no evidence behind it.** The skill
teaches the harness to attach one (see the triage brief), because a low rating
tells you the answer was bad and nothing about what the agent did to produce it.
Two halves are needed together: open your own span with `sync=True` *and* pass
the framework's callback handler into the invoke. The handler folds its LLM and
tool spans into an already-open span, so the handler alone gives you a trace with
no id to attach, and the span alone gives you an id with nothing under it.
Without `sync=True` the trace sends on a background thread, `span.trace_id` is
`None`, and every result stores an empty `traceId` while appearing to work.

**Nothing generates an analysis until you ask.** A finished run has ratings but
no narrative — `analysis_status: not_started` — until someone presses Analyze.
Pass `--analyze` to run one, but know that it is a real judge pass over a sample
of the run, needing a provider key on the *engine* (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY` or `GEMINI_API_KEY`, or Platform Settings in the dashboard).
It is synchronous: one call, no polling.

**`AGENTX_API_BASE_URL` unset means production.** The SDK defaults to the hosted
platform, so a harness with no base URL set quietly runs the whole evaluation
somewhere else. This is the inverse of the hosted advice, and it is the single
most expensive thing to get wrong after the dataset id.

**The SDK's `.analyze()` and `get_report()` do not work here.** Those routes
aren't implemented on self-host. `.analyze()` doesn't even raise — it swallows
the 404 and prints an empty report, which looks exactly like an evaluation that
scored nothing. Read runs from `GET /api/v1/evaluate/{id}` instead, where
`liveStatistics` carries `averageRating`, `minRating`, `maxRating` and
`ratedCount` recomputed from the stored ratings. The SDK-facing
`/api/v1/custom-agent-evaluations/runs/{id}` is a smaller object with none of
those fields.

**Keys are per project, and `~/.agentx/config.json` can be the wrong one.** Each
project has its own key and its own data; an evaluation is a 404 under any other
key. That file records whichever engine last ran on this machine, so it goes
stale the moment anyone runs the Docker image, which keeps its database in its
own volume. `GET /dev/bootstrap` asks the engine actually listening;
`GET /api/v1/projects` lists every project with its key.

**Check the dataset id before launching.** Hand the harness an id and it reuses
that dataset; leave it unset and it silently publishes a brand new one, prints
`Published` instead of `Reusing`, and errors nowhere. The first line of the run
log should name the dataset you expect.

## Requirements

- A running AgentX self-host engine (`agentx-server --dev`, or the container)
- Python 3.9+ — the standard library is enough for `fetch_analysis.py`
- The repo under test, with its dependencies installed
- Git, for the worktree and the branch

`scripts/bootstrap.sh` builds a virtualenv for the repo under test from
`requirements.txt`, a `pyproject.toml`, or packages you name on the command
line.

## What is in here

| Path | What it is |
|---|---|
| `SKILL.md` | Entry point. Connecting to the engine, and where the analysis comes from |
| `references/triage-brief.md` | The core artifact. Six phases, executed in order |
| `references/eval-brief.md` | The re-run: guardrails, launch, analysis, and the comparison |
| `scripts/fetch_analysis.py` | Evaluation, rubric and judge evidence by id, over plain HTTP |
| `scripts/bootstrap.sh` | Virtualenv setup for the repo under test |

Validated end to end on nine agents carrying deliberately planted defects,
spanning levers in code, in a YAML config, in a data file, and in the
evaluation harness itself.
