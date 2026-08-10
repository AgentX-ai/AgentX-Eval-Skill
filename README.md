# agentx-eval-fix

A Claude Code skill that turns an AgentX evaluation analysis into a triaged
code fix, then re-runs the evaluation on the same dataset so the before and
after numbers actually mean something next to each other.

```
analysis export ──► triage against the real source ──► mapping table ──► you approve
                                                                            │
                    v1 vs v2 comparison ◄── re-run on same dataset ◄── fixes on a branch
```

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

### 1. It gets the analysis, with the rubric

```bash
python3 scripts/fetch_analysis.py <evaluation_id> --write-export eval-analysis/exports/
```

Fetching by id beats using a downloaded `.md` export, because the dashboard
export is rendered in the browser and drops four fields: `acceptance_criteria`,
`rejection_criteria`, `evaluation_criteria`, and the per-case `judge_guideline`.

Those four strings are the rubric your answers were graded against. Without
them a triage has to guess what "good" meant, and the most common way a report
misleads is by recommending something the rubric actively penalises. A report
will happily tell you to hedge and express uncertainty when the rejection
criteria fail any answer that dodges the question.

If you only have the file, `scripts/parse_export.py <file>` reads it and emits
the same JSON. Everything downstream is identical.

### 2. It triages, and writes down its reasoning before changing code

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

### 3. It works on a branch, in a worktree

Your checkout is never touched. Fixes land in `.worktrees/eval-fix-<EVAL_ID>`
on branch `eval-fix-<EVAL_ID>`, and can be pushed and opened as a PR.

### 4. You approve, then it re-runs

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
in order, against this repo. The analysis is evaluation 6a7a1f9a7d2cf4fc7bec81b7.
```

That is the whole invocation. It fetches the analysis, reads the source, writes
the mapping table, applies what survived, and stops.

Review the table it leaves at `eval-analysis/mapping-<EVAL_ID>.md`. When you
are happy:

```
Follow references/eval-brief.md and re-run the evaluation.
```

You get `eval-analysis/v2-report-<EVAL_ID>.md` with the before and after side
by side.

### If your repo lives on a remote box

`references/orchestration.md` covers driving a project over MCP: installing the
skill into the workspace, secret naming, staging the analysis, and the two
prompts. Read `references/server4agent-runtime.md` first for the constraints
that shape it.

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

## Two things that will bite you

**Read the stored ratings, not the report's statistics.** `report.statistics`
is an independent estimate, not an aggregate of the judge's scores, and it is
wrong in both directions. Measured across three runs it was off by -0.79, +0.72
and +3.14. On the worst, an agent that truly scored 1.86 reported as 5.00, with
a maximum of 8 when no stored rating exceeded 3. Use `run.liveStatistics`,
which is computed server-side from the stored per-result ratings:

```bash
GET {api}/custom-agent-evaluations/runs/{run_id}
# run.liveStatistics -> averageRating, minRating, maxRating, ratedCount
```

Overstating is the dangerous direction: it ships a broken agent.

**Check the dataset id before launching.** This is the one mistake that costs
full price and produces a plausible number answering a different question. Hand
the harness an id and it reuses that dataset; leave it unset and it silently
publishes a brand new one, prints `Published` instead of `Reusing`, and errors
nowhere. The first line of the run log should name the dataset you expect.

## Requirements

- Python 3.9+ and the `agentx` SDK
- An API key that can reach the dataset, via `AGENTX_API_KEY`, a `.env`, or
  passed to `AgentX(api_key=...)` in code
- The repo under test, with its dependencies installed
- Git, for the worktree and the branch

`scripts/bootstrap.sh` builds a virtualenv from `requirements.txt`, a
`pyproject.toml`, or packages you name on the command line, and skips its `apt`
branch wherever a working pip already exists.

## What is in here

| Path | What it is |
|---|---|
| `SKILL.md` | Entry point. Routes by whether you are executing, orchestrating, or working locally |
| `references/triage-brief.md` | The core artifact. Six phases, executed in order |
| `references/eval-brief.md` | The re-run: guardrails, launch, polling, and the comparison |
| `references/orchestration.md` | Driving a remote project from outside, over MCP |
| `references/server4agent-runtime.md` | Remote runtime constraints |
| `scripts/fetch_analysis.py` | Analysis by evaluation id, rubric included |
| `scripts/parse_export.py` | Analysis from a downloaded export |
| `scripts/bootstrap.sh` | Virtualenv setup |

Validated end to end on nine agents carrying deliberately planted defects,
spanning levers in code, in a YAML config, in a data file, and in the
evaluation harness itself.
