# agentx-eval-fix

A Claude Code skill that turns an AgentX evaluation into a triaged code fix, then
re-runs it on the same dataset so the before and after numbers mean something
next to each other.

```
evaluation ──► triage against the real source ──► mapping table ──► you approve
                                                                       │
               v1 vs v2 comparison ◄── re-run on same dataset ◄── fixes on a branch
```

Built for [AgentX self-host](https://github.com/AgentX-ai/AgentX-trace-eval) —
the local Trace/Evaluate/Monitor engine, `http://localhost:4700` by default.

## The problem it solves

An AgentX analysis is written by a judge that saw your agent's **answers** and
nothing else. Not the source, not the tool definitions, not the retrieval config.
So it describes symptoms accurately and prescribes badly, and you are left with a
document that says what was wrong with the output but not what to change.

A real case. An agent scored **1.86 out of 10**, every answer cut off mid-word.
The judge's top recommendation:

> Introduce and enforce a completion check that discourages truncation and
> requires the agent to finish its explanations.

Reasonable, and useless. The agent was already producing complete 800-character
answers ending on a full stop. The evaluation harness had this line:

```python
"output": out["text"][:MAX_OUTPUT_CHARS],   # MAX_OUTPUT_CHARS = 400
```

One line, in a file the report never mentions, in a component the judge cannot
see. Four of that report's five recommendations were downstream of it. Deleting
the slice took the agent to 10.00.

**That is the gap this closes.** It treats the report's evidence as reliable and
its recommendations as hypotheses, then checks each one against the code.

## How it works

**1. Reads the evaluation off your engine.** Standard library only — no SDK, no
virtualenv, no `.env`. It stitches together the stored per-result ratings, the
dataset's expected results and judge guidelines, and the multi-judge agreement
data into one export, and works out which grading config actually graded the run.

```bash
python3 scripts/fetch_analysis.py --list
python3 scripts/fetch_analysis.py <evaluation_id> --write-export eval-analysis/exports/
```

**2. Triages every recommendation against the source.** Each gets one of five
verdicts, and every rejection must cite the `file:line` or the rubric string that
disproves it. An assertion without evidence is not a verdict.

| Verdict | Meaning |
|---|---|
| `apply` | Right, as written |
| `apply-modified` | Real problem, wrong remedy — keep the observation, change the fix |
| `reject-wrong-premise` | Assumes something untrue about the code |
| `reject-already-done` | Asks for something that already exists |
| `reject-harmful` | Following it would lower the score |

**3. Writes down what the report could not see.** A second table for defects
found by reading the code: retrieval width, tool wiring, sampling parameters,
whether a tool's output ever reaches the model, whether the model's answer
reaches the judge. There is almost always something here, and it is usually the
thing that actually mattered.

**4. Fixes on a branch, in a worktree.** Your checkout is never touched. Changes
land in `.worktrees/eval-fix-<id>` on `eval-fix/<id>`.

**5. Re-runs and compares.** Same dataset, same criteria, same model — anything
the comparison is keyed on stays frozen, or the second number means nothing.

## Install

```bash
git clone git@github.com:AgentX-ai/AgentX-Eval-Skill.git \
  ~/.claude/skills/agentx-eval-fix
```

Or per-project, keeping it out of the repo under test — the repo is the thing
being measured, and tooling in its `git status` ends up in the diff a reviewer
reads:

```bash
git clone git@github.com:AgentX-ai/AgentX-Eval-Skill.git \
  <your-repo>/.claude/skills/agentx-eval-fix
echo '.claude/skills/agentx-eval-fix/' >> <your-repo>/.git/info/exclude
```

## Run it

From inside the repo that holds your agent:

```
Use the agentx-eval-fix skill. Follow references/triage-brief.md exactly,
in order, against this repo. The analysis is evaluation oE1YMG5wqmu4j2bhTtw1X.
```

It fetches the evaluation, reads the source, writes the mapping table, applies
what survived, and stops. Review `eval-analysis/mapping-<id>.md`, then:

```
Follow references/eval-brief.md and re-run the evaluation.
```

You get `eval-analysis/v2-report-<id>.md` with the before and after side by side.

## Reviewing the mapping table

The stop is deliberate: everything before it is free, the re-run is not. Worth
thirty seconds:

- **Every recommendation has a verdict.** A skipped row is an unexamined claim.
- **Rejections cite a file and line**, rather than asserting.
- **The second table isn't empty.** That usually means the code was skimmed.
- **No tools were added, and no hedging language.** Both are common wrong turns,
  and adding tools breaks the comparison — runs record a tool count.
- **`RUBRIC-CONFORMING` rows.** These are the only ones you must settle
  personally. They mark a value changed purely because the expected results
  disagreed with the code, where nothing in the repo could say which is right. If
  the rubric has a typo, the change makes your agent confidently wrong to real
  users, the score goes *up* anyway, and nothing downstream will catch it.

## Requirements

A running self-host engine, Python 3.9+, and git. `scripts/fetch_analysis.py`
needs nothing beyond the standard library; `scripts/bootstrap.sh` builds a
virtualenv for the repo under test when it needs one.

The engine needs a provider key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or
`GEMINI_API_KEY`, or the dashboard's Platform Settings) to score anything.

## What's in here

| Path | What it is |
|---|---|
| `SKILL.md` | Entry point — connecting to the engine, and where the analysis comes from |
| `references/triage-brief.md` | The core artifact. Six phases, executed in order |
| `references/eval-brief.md` | The re-run: guardrails, launch, analysis, comparison |
| `scripts/fetch_analysis.py` | Evaluation, rubric and judge evidence by id, over plain HTTP |
| `scripts/bootstrap.sh` | Virtualenv setup for the repo under test |

Validated end to end on nine agents with deliberately planted defects, spanning
levers in code, in a YAML config, in a data file, and in the evaluation harness
itself; then on three LangChain agents scoring 3.60, 5.50 and 4.80, triaged to
10.00, 9.60 and 9.80 on the same datasets.
