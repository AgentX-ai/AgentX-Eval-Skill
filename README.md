# agentx-eval-fix

A Claude Code skill that turns an AgentX evaluation analysis into a triaged code
fix and a re-run against the same dataset.

An AgentX analysis tells you what was wrong with an agent's *answers*. It cannot
tell you what to change in the code, because the judge that wrote it never saw
the code. This skill closes that gap: it puts the analysis and the repo side by
side, triages one against the other, applies what survives, and re-runs the
evaluation against the same dataset so the two scores mean something next to
each other.

## The one idea worth keeping

**The report is not a to-do list.** Its evidence is first-hand and reliable. Its
recommendations are reconstructions, written by something that could see only
the output.

On the first real export this was built against, two of five recommendations
asked for things that already existed in the repo, and a third would have
lowered the score. That ratio has held up across every agent tested since.

The clearest case: an agent scored 1.86 out of 10 because the evaluation
harness sliced every answer to 400 characters before submitting it. The judge
saw seven replies cut off mid-word and recommended, reasonably, that the agent
be taught to finish its sentences. The agent was already producing complete
800-character answers. Applying that recommendation literally would have
changed nothing, and the real defect was one line in a file the report never
mentions.

## Install

Clone straight into your skills directory:

```bash
git clone git@github.com:AgentX-ai/AgentX-Eval-Skill.git \
  ~/.claude/skills/agentx-eval-fix
```

Or per-project, so it travels with the repo:

```bash
git clone git@github.com:AgentX-ai/AgentX-Eval-Skill.git \
  <your-repo>/.claude/skills/agentx-eval-fix
```

If you install it per-project, keep it out of the repo under test. The repo is
the thing being measured, and tooling in its `git status` ends up in the diff a
reviewer reads:

```bash
echo '.claude/skills/agentx-eval-fix/' >> .git/info/exclude
```

`.git/info/exclude` is per-clone and never committed, which is what you want.

## Use

Point it at a repo and an analysis. It routes itself:

```
Use the agentx-eval-fix skill. Follow references/triage-brief.md exactly,
in order, against this repo. The analysis is eval-analysis/exports/<file>.md
```

Two ways to get the analysis in:

| You have | Use |
|---|---|
| An evaluation id and API access | `scripts/fetch_analysis.py <id> --write-export eval-analysis/exports/` |
| A downloaded `.md` export | `scripts/parse_export.py <file>` |

Prefer fetching. The dashboard export is rendered in the browser and leaves out
`acceptance_criteria`, `rejection_criteria`, `evaluation_criteria` and the
per-case `judge_guideline`. Those four strings are the rubric the answers were
scored against, and they are the difference between a triage that catches a
harmful recommendation and one that applies it.

## What you get

A **mapping table** at `eval-analysis/mapping-<EVAL_ID>.md`, written before any
code is touched. It is the checkpoint: everything up to it is free, and the next
step is not.

- **Table 1**, one row per recommendation, each with a verdict: `apply`,
  `apply-modified`, `reject-wrong-premise`, `reject-already-done`, or
  `reject-harmful`. Every rejection cites the `file:line` or the rubric string
  that disproves it.
- **Table 2**, defects the report could not see. An answers-only judge cannot
  observe retrieval width, tool wiring, sampling parameters, or whether a tool's
  output ever reaches the model. There is almost always something here.

Then the work happens in a git worktree on its own branch, so your checkout is
untouched, and the re-run produces a v1-vs-v2 comparison.

## Reading the mapping table

The checkpoint exists so a human looks. Worth checking:

- Does every recommendation have a verdict? A skipped row is an unexamined claim.
- Are rejections evidenced with a file and line, or just asserted?
- Is Table 2 empty? That usually means the code was skimmed rather than read.
- Are there `RUBRIC-CONFORMING` rows? Those mark values changed on the rubric's
  authority alone, where nothing in the repo could confirm which figure is
  right. If the rubric has a typo, the change makes the agent confidently wrong
  and the score goes *up* anyway, so nothing downstream catches it. **These are
  the rows only you can validate.**

## Results

Nine agents, each with deliberately planted defects, run end to end. Scores are
the judge's stored per-result ratings.

| Agent | What was wrong | Before | After |
|---|---|---|---|
| Northwind | vague prompt, narrow retrieval | 6.56 | 9.66 |
| Harborline | no retrieval, wrong bands | 2.30 | 8.80 |
| Brightwell | wrong constant, self-discouraging docstring, truncating lookup | 4.10 | 10.00 |
| Thorne & Vale | cache on wrong key, unit mismatch, compound truncation | 4.50 | 9.60 |
| Pinegrove | boundary off-by-one, swallowed exception, tool ambiguity | 4.30 | 9.70 |
| Wrenfield | pence/pounds scale, loop dropping tool_use blocks, stale prompt | 4.00 | 8.30 |
| Ashgrove | every lever in a YAML config, not in code | 6.50 | 9.29 |
| Calderwood | a data file contradicting the authoritative policy beside it | 5.71 | 10.00 |
| Marlow & Finch | the decisive defect was in the eval harness | 1.86 | 10.00 |

The minimum matters more than the mean. Across the last three, minimums went
from 1, 2 and 1 to 7, 10 and 10.

Two caveats the skill insists on reporting, and which apply to this table:

- A single pass applies every change at once, so no individual fix is creditable
  from one run. Per-change attribution costs one run per change.
- Where a defect was in the harness rather than the agent, part of the movement
  is the repair rather than the agent improving. Marlow & Finch is mostly that.

## Requirements

- Python 3.9+, the `agentx` SDK, and an API key that reaches the dataset
- The repo under test checked out, with its dependencies installed
- Git, for the worktree and branch

`scripts/bootstrap.sh` builds a virtualenv from `requirements.txt`, a
`pyproject.toml`, or packages named on the command line, and skips its `apt`
branch wherever a working pip already exists.

## Layout

| Path | What it is |
|---|---|
| `SKILL.md` | Entry point. Routes by whether you are executing, orchestrating, or working locally |
| `references/triage-brief.md` | The core artifact. Six phases, executed in order |
| `references/eval-brief.md` | The re-run: guardrails, launch, polling, and the comparison |
| `references/orchestration.md` | Driving a remote project from outside over MCP |
| `references/server4agent-runtime.md` | Remote runtime constraints |
| `scripts/fetch_analysis.py` | Analysis by evaluation id, including the rubric |
| `scripts/parse_export.py` | Analysis from a downloaded export |
| `scripts/bootstrap.sh` | Virtualenv setup |
