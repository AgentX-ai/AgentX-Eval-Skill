---
name: agentx-eval-fix
description: >-
  Turn an AgentX evaluation analysis into a triaged code fix and a re-run against
  the same dataset, either locally against a checked-out repo or on a Server4Agent
  box. Use this whenever someone has an AgentX analysis export, evaluation report,
  judge findings or a scored eval run and wants to know what to actually change in
  the code, or wants to re-run an evaluation to compare before-and-after scores.
  Also use when a request mentions AgentX dataset ids or evaluation ids, an agent
  that scored badly, hallucination or grounding findings from an LLM judge, or the
  question "the report tells me what is wrong with the answers but not what to fix
  in my code". The core move is triaging code-blind judge recommendations against
  the real source instead of applying them literally.
---

# Fix an agent from its evaluation report, then re-score it

An AgentX analysis tells you what was wrong with an agent's *answers*. It cannot
tell you what to change in the code, because the judge that wrote it never saw
the code. This skill closes that gap: it puts the analysis and the repo side by
side, triages one against the other, applies what survives, and re-runs the
evaluation against the same dataset so the two scores mean something next to each
other.

The report is not a to-do list. On a real export, two of five recommendations
asked for things that already existed in the repo, and a third would have lowered
the score. Treat its evidence as reliable and its recommendations as hypotheses.

## Start here

**If you were asked to triage an analysis, or to re-run an evaluation, in a repo
you are already working inside**, you are the one doing the work. Go straight to
the brief:

| Asked to | Follow |
|---|---|
| Triage an analysis and fix the code | `references/triage-brief.md` |
| Re-run the evaluation and compare | `references/eval-brief.md` |

Each brief is self-contained and written to be executed in order. Nothing else on
this page applies to you.

**If you are driving a Server4Agent project from outside**, read
`references/orchestration.md`. It covers equipping the project, secrets, staging,
and the two prompts.

**Otherwise the repo is on this machine**, and the rest of this page is for you.

## Where the analysis comes from

| You have | Use | Notes |
|---|---|---|
| An evaluation id and API access | `scripts/fetch_analysis.py <id> --write-export <dir>` | **Preferred.** Includes the grading criteria |
| A downloaded `.md` export | `scripts/parse_export.py <file>` | Works with no API access |

Both emit the same JSON, so everything after this point is identical.

Prefer fetching when you can. The dashboard export is rendered in the browser and
leaves out `acceptance_criteria`, `rejection_criteria`, `evaluation_criteria` and
the per-case `judge_guideline`. Those four strings are the rubric the answers were
scored against, and they are the difference between a triage that catches a
harmful recommendation and one that applies it. The dataset API has all four.

If you only have the export, the triage can usually recover the rubric by reading
the evaluation harness in the repo, which is why the brief tells it to. That works
when the harness defines the dataset in code. It fails silently when the dataset
was published from somewhere else.

## Local mode

### 1. Get the analysis into the repo

```bash
# From an evaluation id (preferred)
python3 <skill>/scripts/fetch_analysis.py <evaluation_id> \
  --write-export <repo>/eval-analysis/exports/

# Or from a file you downloaded
mkdir -p <repo>/eval-analysis/exports
cp <export.md> <repo>/eval-analysis/exports/
python3 <skill>/scripts/parse_export.py <export.md>
```

Show the user the baseline numbers and the count of recommendations before going
further. That is the shape of the job.

### 2. Do the triage

Read `references/triage-brief.md` and carry it out yourself, against the repo,
with the repo as your working directory. One substitution, since that brief is
also used on a box: where it says to push with `$S4A_GIT_TOKEN`, locally that is
just `git push -u origin <branch>`.

Everything else applies unchanged, including the parts that matter most: write the
mapping table before touching any code, keep the frozen surfaces frozen, and do
not run the evaluation during the triage.

### 3. Stop and show the mapping table

`eval-analysis/mapping-<EVAL_ID>.md` is the deliverable of the triage and the
checkpoint of the whole workflow. Everything up to here was free; the next step
is not.

If a run reports nothing at all, look for the work rather than assuming there is
none. The summary is also written to `eval-analysis/triage-report-<EVAL_ID>.md`,
and the mapping table itself is written before any code is touched, so a run that
died partway through still leaves both. A run that hit its turn limit and a run
that did nothing look identical from the outside until you go and check.

### 4. Re-run the evaluation

Read `references/eval-brief.md` and carry it out. Locally you can run in the
foreground, since there is no 30 minute run ceiling, but keep these because they
are not about the box:

- **Check the dataset id before launching.** This is the one failure that costs
  full price and produces a plausible number for a different question.
- Delete any stray `.env` that came from a committed example file.
- Report the minimum score alongside the average, and state the noise floor.

`scripts/bootstrap.sh` works locally too: it skips its `apt` branch wherever a
working pip already exists, and builds a virtualenv from `requirements.txt`, a
`pyproject.toml`, or packages you name on the command line.

### Worked example

For a Python agent at `~/code/my-agent` with an export in `~/Downloads`:

```bash
mkdir -p ~/code/my-agent/eval-analysis/exports
cp ~/Downloads/*_analysis_*.md ~/code/my-agent/eval-analysis/exports/
python3 ~/.claude/skills/agentx-eval-fix/scripts/parse_export.py \
  ~/code/my-agent/eval-analysis/exports/*_analysis_*.md
```

Then work in `~/code/my-agent`, follow the triage brief, and stop at the mapping
table.

## Reading the mapping table

The checkpoint, in every mode. In order:

- Does every recommendation have a verdict? A skipped row is an unexamined claim.
- Are the rejections evidenced with a file and line, or just asserted?
- Is Table 2 empty? A judge working from answers alone cannot see retrieval
  configuration, tool wiring or sampling parameters, so an empty Table 2 usually
  means the code was skimmed rather than read.
- Does "Derived from" say `README` on most rows? Then the agent transcribed an
  existing list of known issues instead of doing the analysis.
- Did it add tools, or add hedging language? Both are common wrong turns. The
  brief warns against both, but check.
- **Are there any `RUBRIC-CONFORMING` rows?** Those are the ones only you can
  validate. They mark a value the triage changed in the code purely because the
  expected results said something different, with no way to tell from inside the
  repo which figure is the true one. If the rubric is right, the change is a fix.
  If the rubric has a typo, the change makes the agent confidently wrong and the
  next score goes *up* anyway, so nothing downstream will catch it. Go and check
  the real policy before approving these.

Table 2 varies more between runs than Table 1 does. The verdicts are stable; the
code-only sweep is a search, and a single pass does not exhaust it. If the finding
matters, a second pass is cheap.

## Reporting results

Three things worth saying out loud regardless of what the numbers did:

- **The minimum matters at least as much as the average.** A low minimum means
  some questions fail badly and unpredictably, which is a different problem from a
  uniformly mediocre mean and usually the one that grounding fixes.
- **No individual change can be credited.** A single pass applies everything at
  once. Per-change attribution costs one run per change, and it is worth naming as
  an option rather than implying the data supports a story it does not.
- **Read the scores from the stored per-result ratings**, not the report's
  summary statistics. The two disagree on this platform, and the summary has
  understated every run measured so far.
- **State the noise floor.** If the first run's judge scored structurally similar
  answers several points apart, a smaller movement in the average is not a result.

## Adapting the briefs

The briefs are written against roles, not names: "the agent's instruction string",
"the tool registry", "the retrieval configuration", "the grading surface". That is
what lets them work on a repo nobody has seen before, and it is worth preserving
when editing them.

The one principle they turn on, and the one to keep if anything else is cut:
**freeze anything the comparison is keyed on.** The test questions, the grading
criteria, the tool inventory, the knowledge base and the model all have to be
identical across the two runs. Change any of them and the second number is not
comparable to the first, which is the only reason the second run exists.
