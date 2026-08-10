# Brief: re-run the evaluation and report the before-and-after

You are working inside a clone of a repo that holds an AI agent and the harness
that evaluates it. A previous run changed the agent's code based on a triaged
analysis of an earlier evaluation. Your job is to re-run that evaluation against
**the same dataset** and report what moved.

You have no memory of that previous run. Everything you need is in this file, in
the repo, and in git history. There is no interactive channel, so never pause to
ask a question.

**This run spends real money**: one model invocation per test case per repeat,
plus a judge pass over the results. The guardrails in Phase 1 exist because the
expensive failures here are silent ones. Work through the phases in order.

## Phase 0: orient

**The fix may be in a worktree, not here.** The triage works in
`.worktrees/eval-fix-<EVAL_ID>` so it never edits a running service or someone's
working tree. Evaluate the fix, which means evaluating what is in there:

```bash
git worktree list
```

If a worktree on an `eval-fix/` branch exists, `cd` into it and stay there for
the whole of this brief. Everything below assumes that is your working
directory. If there is none, the triage edited in place and you are already in
the right spot.

```bash
git --no-pager log --oneline -3
git --no-pager diff --stat "$(git rev-list --max-parents=0 HEAD)"..HEAD
ls eval-analysis/ eval-analysis/exports/
```

Read `eval-analysis/mapping-<EVAL_ID>.md`, the mapping table the previous run
wrote. There will be exactly one; its filename gives you `<EVAL_ID>`. This tells
you what changed and why, which is what you will be attributing results to.

Then open the export it came from, `eval-analysis/exports/*.md`, but only two
sections: **Identifiers** (for the dataset id) and **Statistics** (for the v1
baseline numbers: average, min, max, runs, consistency, and the score variance).
Skip the prose; you are not re-doing the triage.

## Phase 1: four guardrails, all of them, before anything runs

Ordered by how expensive they are to miss.

### 1. The dataset id has to be the one the export names

```bash
echo "dataset from env: ${AGENTX_DATASET_ID:-UNSET}"
```

Compare it against the Dataset ID row in the export's Identifiers table. **If it
is unset or different, stop and report that. Do not run the evaluation.**

This is the check that matters most, because its failure is silent and complete.
Handed a dataset id, the harness reuses that dataset. Handed nothing, it publishes
a brand new one and scores the agent against freshly created questions. Nothing
errors. You get a clean, plausible number that answers a different question than
the one anyone asked, and it costs the same as the right one.

### 2. Any `.env` points somewhere live

Most harnesses call `load_dotenv()` at import, so a `.env` silently overrides what
you checked in the previous step. Look at the API host it sets, if any:

```bash
[ -f .env ] && grep -E 'API_BASE_URL|_URL=' .env || echo "no .env"
```

The danger is a **stale host**, not the file's existence. Committed
`.env.example` files are exactly where dead URLs survive, because nothing ever
fails when an example is wrong, and anything that copies the example into place
inherits the rot. A dead host kills the first API call in a way that reads like
an outage rather than a config error.

What to do depends on where you are, and getting this backwards is destructive:

- **On a fresh clone** (a box, CI, a container), a `.env` was not written by a
  person. It came from an automated setup step copying the example. If its host
  is dead or unreachable from here, delete it (`rm -f .env`) and note that in
  your report. It is gitignored, so this touches no history.
- **On someone's own machine**, `.env` is their real configuration and holds
  their keys. **Do not delete it.** If the host is wrong for this run, override
  it for the one command instead: `AGENTX_API_BASE_URL=<correct> ...`. Deleting
  it destroys credentials you cannot restore.

If you cannot tell which situation you are in, you are on someone's machine.
Treat the file as theirs.

### 3. The API base URL is not pointing somewhere dead

```bash
echo "base url: ${AGENTX_API_BASE_URL:-<unset, SDK default applies>}"
```

Unset is correct and preferred: the SDK defaults to production. If it is set, it
has to be a host that currently resolves.

### 4. The provider keys are present, under their prefixed names

The repo's provider credentials are attached under prefixed names rather than
their conventional ones. That is deliberate: the conventional names are reserved
by the platform for the agent runtime that is executing this very session, and a
secret using one of them would override the platform's own credential rather than
only reaching the evaluation. The prefix keeps the two apart.

Check that each one the harness needs is set, plus the evaluation API key. Report
and stop if any is missing, rather than improvising a fallback: a missing key
fails partway through, after some of the spend.

You will map the prefixed names back to their real names on the command line in
Phase 3, so the real names exist inside that one process and nowhere else.

## Phase 2: bootstrap

```bash
bash .claude/skills/agentx-eval-fix/scripts/bootstrap.sh
```

(That is where the script lives when this skill is installed into the project. If
the skill sits elsewhere, use its own `scripts/bootstrap.sh`.)

Expect two to four minutes on a cold box, since it installs a package manager and
a virtualenv module first, and a few seconds on a warm one. It ends with a line
confirming the imports that matter. If it fails, report the error verbatim and
stop. Do not fall back to a system-wide install: this box refuses that by design,
and forcing past it breaks the system Python rather than fixing anything.

**If it stops because the repo has no dependency manifest**, that is common: plenty
of small agent repos document their install as a pip line in the README and never
pin anything. Read the install instructions, pass the packages explicitly, and pin
the evaluation SDK to an exact version:

```bash
bash .claude/skills/agentx-eval-fix/scripts/bootstrap.sh <pkg> <pkg> "<eval-sdk>==<version>"
```

Pin the SDK because a before-and-after comparison whose scoring client changed
underneath is not a controlled comparison. Note the missing manifest in your final
report as a reproducibility gap: the next run will have to guess the same way you
just did, and it may guess differently.

Then make sure the virtualenv cannot be committed:

```bash
grep -q '^\.venv/$' .gitignore && echo ".venv ignored, good" || echo "WARNING: .venv not ignored"
```

If it is not ignored, add the line now, before going any further.

## Phase 3: launch detached

**Do not run the evaluation in the foreground.** It invokes a model once per case
and then polls a server-side analysis job whose client-side wait can run to half
an hour. A foreground command that long either hits a command timeout or consumes
this run's entire wall-clock budget, in both cases after the money is already
spent.

```bash
mkdir -p .server4agent
<REAL_KEY_NAME>="$<PREFIXED_KEY_NAME>" \
<REAL_KEY_NAME_2>="$<PREFIXED_KEY_NAME_2>" \
nohup setsid .venv/bin/python -u <evaluation script> > .server4agent/eval-v2.log 2>&1 &
echo "launched pid $!"
```

`-u` is not optional. Without unbuffered output the log stays empty until the
process exits, and you cannot tell "still running" from "hung", which is the one
distinction you will need for the next twenty minutes.

### The abort check, 30 seconds in

```bash
sleep 30; cat .server4agent/eval-v2.log
```

The first line has to say that it is **reusing** the dataset, and name the id from
the export. If it says it published a new one, kill the process immediately:

```bash
pkill -f <evaluation script>
```

then report that the dataset id was not honored and stop. Every second past that
point is spend on a result nothing can be compared to.

While you are here, capture the run id from the startup banner. If everything
later goes wrong, that id is how the result gets recovered from the dashboard,
and it is only cheap to grab now.

## Phase 4: poll, with a cap on your own patience

```bash
sleep 60; tail -20 .server4agent/eval-v2.log
```

Repeat. **Poll at most 14 times.** This run has a wall-clock ceiling enforced from
outside, and a run killed at that ceiling loses everything not already written to
a file.

Expect roughly: a banner, per-case progress, a scored-results line, a finalize
line, an analysis phase with a rising percentage, then the statistics block.

**If you hit the poll cap with no statistics block, the work is not lost.**
Results are uploaded in batches as they complete, and the analysis job runs
server-side independently of this process. Write the report file with what you
have, marked `status: analysis_incomplete`, including the run id and the log's
last lines. Commit it. Say clearly in your final message that the full report can
be retrieved later from the dashboard using that run id. Do not keep waiting.

## Phase 5: write the comparison

Write `eval-analysis/v2-report-<EVAL_ID>.md`:

```markdown
# v1 vs v2: <dataset name>

- Dataset id: <id>   (identical in both runs)
- v1 evaluation id: <EVAL_ID>
- v2 run id: <from the log>
- v2 dashboard: <url from the log>
- Status: complete | analysis_incomplete
- Changes under test: eval-analysis/mapping-<EVAL_ID>.md

## Scores

| Metric | v1 | v2 | Delta |
|---|---|---|---|
| Average score | | | |
| Min | | | |
| Max | | | |
| Spread (max - min) | | | |
| Runs | | | |
| Consistency | | | |
| Instruction adherence | | | |
| Jaccard | | | |
| ROUGE-L | | | |

## What moved

## The new report's recommendations

## Verdict
```

### Where to read the scores from

**Do not fill the table from the report's own statistics.** They are not an
aggregate of the judge's scores. They are an independent estimate, and they are
wrong in both directions. Read the stored ratings instead:

```bash
GET {api}/custom-agent-evaluations/runs/{run_id}
# run.liveStatistics -> averageRating, minRating, maxRating, ratedCount
```

`liveStatistics` is computed server-side from the stored per-result ratings and
carries the mean, minimum, maximum and count in one object, which is exactly what
the table needs. If a run predates the field, average `run.results[].rating` by
hand instead. The two agree.

Measured on three fresh runs, `report.statistics` diverged from the stored
ratings by -0.79, +0.72 and +3.14, so it is not a constant offset and not a
consistent bias. On the worst of the three, an agent whose answers were all being
truncated to fragments scored 1.86 and reported as 5.00, with a maximum of 8 where
no stored rating exceeded 3. A reported minimum of `0.00` turns up on runs whose
true minimum was 1 or 2.

The direction matters more than the size. A report that understates makes you
work on an agent that is fine. A report that overstates by three points makes you
ship one that is broken.

Since a before-and-after comparison is the entire deliverable here, reading a
number that can be three points out in either direction defeats the exercise.
Take the v1 side the same way, from the earlier run's stored results, so both
sides come from the same source. If you can only get one side from the stored
results, say so in the report rather than comparing two numbers that were
computed differently.

### One log line that looks alarming and is not

The submission log prints a line like:

```
Batch 16a74773: accepted=2 duplicates=5 failed=0
```

`accepted` counts rows newly inserted, not answers scored. Results are submitted
in batches as they finish, and the finalize step submits them again; the second
submission collides on the idempotency key, and each collision resolves to the
row already stored. `accepted=0 duplicates=7` is the ordinary shape of a healthy
seven-question run. It means all seven landed.

Do not read it as "only two answers were scored" and declare the baseline
invalid. A triage did exactly that and reported a real 5.71 over seven answers as
a fake 2.50 over two, on a run whose stored results were all present and rated.
`ratedCount` from `liveStatistics`, or the length of `run.results[]`, is the
answer to how many were scored. The batch line is not.

If the export reports a score variance and the new report exposes no such field,
use spread as the substitute and say so in the table rather than presenting one
number as the other.

**Under "What moved"**, one short paragraph per change from the mapping table that
the data can say something about, keyed to the metric that moved. Be careful
about attribution: this was a single pass that applied every change at once, so
no individual change can be isolated from this run. Say that plainly rather than
implying a causal story the data does not support. If someone wants per-change
attribution, that is one run per change, and it is worth naming as the option
rather than faking it.

**Under "The new report's recommendations"**, list them verbatim from the log,
each with a one-line note on whether it is a genuine new finding or the same
code-blind inference pattern as last time. That note is what stops the next
iteration from chasing the same rejected advice again.

**Under "Verdict"**, one of **worked**, **partial** or **regressed**, plus one
sentence on what to try next.

Weigh the minimum score at least as heavily as the average. A low minimum means
some questions fail badly and inconsistently, which is a different and usually
worse problem than a uniformly mediocre mean, and it is the one that moves when
grounding and tool discipline improve.

State the noise floor explicitly. If the v1 judge scored structurally similar
answers several points apart, then a change in the average smaller than that
spread is not a result, and reporting it as one invites the next person to
optimize against noise.

## Phase 6: commit and push

```bash
git add eval-analysis/
git status --porcelain
```

Read that output. Nothing under a virtualenv directory, `__pycache__/`, `.env` or
`.server4agent/` may be staged. Use explicit paths, never `git add -A`: the
bootstrap step created a virtualenv in this directory a few minutes ago.

```bash
git commit -m "eval v2 for <EVAL_ID>: <v1 average> -> <v2 average>"
git push "https://x-access-token:$S4A_GIT_TOKEN@github.com/<owner>/<repo>.git" HEAD
```

Take owner and repo from `git remote get-url origin`. The `x-access-token`
username is required. Never write the token into a file, a commit, a remote, or a
command you echo. A refused push is a reportable outcome, not a failure: say so
and continue.

## Phase 7: the report

Write this block to `eval-analysis/eval-report-<EVAL_ID>.md` first, then repeat
it as the last thing in your final message. The message is how a caller normally
reads a run and is also the one thing a truncated run loses, and this brief spends
real money before it reports. Putting the numbers on disk first means a run that
is cut off after the evaluation completed still has its results.

```
EVAL V2 COMPLETE
status:            complete | analysis_incomplete | aborted (<reason>)
evaluated_in:      .worktrees/eval-fix-<EVAL_ID> | the main checkout
dataset_id:        <id>   (same as v1: yes | no)
v2_run_id:         <id>
dashboard:         <url>
average:           <v1> -> <v2>   (delta <+/-x.xx>)
min:               <v1> -> <v2>
max:               <v1> -> <v2>
spread:            <v1> -> <v2>
consistency:       <v1> -> <v2>
noise_floor:       <the v1 spread on similar answers, for reading the delta>
verdict:           worked | partial | regressed
report_file:       eval-analysis/v2-report-<EVAL_ID>.md
log_file:          .server4agent/eval-v2.log
commit:            <short sha>
pushed:            yes | no (<reason>)
stray_env_deleted: yes | no
```
