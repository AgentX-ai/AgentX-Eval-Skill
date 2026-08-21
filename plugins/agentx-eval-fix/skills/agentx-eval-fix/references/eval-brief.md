# Brief: re-run the evaluation and report the before-and-after

You are working inside a repo that holds an AI agent and the harness that
evaluates it, scored by an AgentX self-host engine (`http://localhost:4700`
unless `HOST` says otherwise). A previous run changed the agent's code based on a
triaged analysis of an earlier evaluation. Your job is to re-run that evaluation
against **the same dataset** and report what moved.

Everything you need is in this file, in the repo, and in git history.

**This run spends real money**: one model invocation per test case per repeat,
plus a judge pass over the results, all billed to whichever provider keys are in
play. The guardrails in Phase 1 exist because the expensive failures here are
silent ones. Work through the phases in order.

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
sections: **Identifiers** (for the dataset id and the base URL) and
**Statistics** (for the v1 baseline: average, min, max, rated results, variance,
consistency). Skip the prose; you are not re-doing the triage.

## Phase 1: four guardrails, all of them, before anything runs

Ordered by how expensive they are to miss.

### 1. The base URL has to point at the engine that scored v1

```bash
ENGINE="${AGENTX_HOST:-http://localhost:4700}"
echo "base url: ${AGENTX_API_BASE_URL:-<unset>}   engine: $ENGINE"
curl -s -o /dev/null -w '%{http_code}\n' "$ENGINE/health"
```

**Unset is wrong here.** The AgentX SDK defaults to the hosted platform, so an
unset base URL sends the whole run to production: a different database, a
different dataset id space, a different bill, and a v2 number that cannot be
compared to a v1 scored on the self-host engine.

**The right value is the Base URL row of the export's Identifiers table**, not
localhost by reflex. The engine is local by default, but `HOST` can point it at a
LAN box, a container or a shared engine behind TLS, and v1 was scored wherever that
said at the time. Same engine as v1 or the two numbers are not a pair — a v2 run
against a different engine is a different database, a dataset id that resolves to
something else or to nothing, and no error either way.

`/health` returning `200` is the engine answering. Anything else and it is not
running, or `HOST` names an address that is not it — `agentx-server --dev`, or the
container, or the right address, before going further.

This inverts the hosted advice, so check it even if you think you know.

### 2. The dataset id has to be the one the export names

```bash
echo "dataset from env: ${AGENTX_DATASET_ID:-UNSET}"
```

Compare it against the Dataset ID row in the export's Identifiers table. **If it
is unset or different, stop and report that. Do not run the evaluation.**

This failure is silent and complete. Handed a dataset id, the harness reuses that
dataset. Handed nothing, it publishes a brand new one and scores the agent
against freshly created questions. Nothing errors. You get a clean, plausible
number that answers a different question than the one anyone asked, and it costs
the same as the right one.

Self-host ids are nanoids, e.g. `ig_b183o1Pm7I3kd05q8c`. A 24-character hex id in
the harness is a leftover from the hosted platform and will 404 here.

### 3. Any `.env` points somewhere live

Most harnesses call `load_dotenv()` at import, so a `.env` silently overrides both
checks above:

```bash
[ -f .env ] && grep -E 'AGENTX_|API_BASE_URL|_URL=|DATASET' .env || echo "no .env"
```

The danger is a **stale host**, not the file's existence. Committed `.env.example`
files are exactly where dead URLs survive, because nothing ever fails when an
example is wrong, and anything that copies the example into place inherits the
rot. A `.env` still pointing at the hosted API is the specific rot to look for
here, because it does not fail — it succeeds against the wrong server.

What to do depends on where you are, and getting this backwards is destructive:

- **On a fresh clone** (CI, a container), a `.env` was not written by a person. It
  came from an automated step copying the example. If its host is wrong for this
  run, delete it (`rm -f .env`) and note that in your report. It is gitignored, so
  this touches no history.
- **On someone's own machine**, `.env` is their real configuration and holds their
  keys. **Do not delete it.** Override it for the one command instead:
  `AGENTX_API_BASE_URL="${AGENTX_HOST:-http://localhost:4700}/api/v1" ...`.
  Deleting it destroys credentials you cannot restore.

If you cannot tell which situation you are in, you are on someone's machine.
Treat the file as theirs.

### 4. Two different keys have to be present, in two different places

Self-host splits them, and each is missed in its own way:

- **The agent's own provider key**, in this process's environment, so the agent
  under test can answer. A missing one fails partway through, after some of the
  spend.
- **The engine's judge key** — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or
  `GEMINI_API_KEY` in the *engine's* environment, or set from the dashboard's
  Platform Settings, which takes precedence. This is what scores the answers. If
  it is missing, results still upload and still store, they just come back
  unrated: the run looks like it worked and produces no scores to compare.
- **The AgentX API key** (`AGENTX_API_KEY`) selects the project. An evaluation
  belongs to one project and is invisible under any other project's key. If a
  lookup 404s on an id you know exists, this is why:
  `curl -s "${AGENTX_HOST:-http://localhost:4700}/api/v1/projects"` lists them.
  Keys are per engine as well as per project, so an engine reached over `HOST`
  needs *that* engine's key, not the one in the local `~/.agentx/config.json`.

Report and stop if any is missing, rather than improvising a fallback.

## Phase 2: bootstrap

```bash
bash <skill>/scripts/bootstrap.sh
```

It dispatches on whichever manifest the repo has - `requirements.txt` or
`pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, `Gemfile`, `pom.xml` -
and ends with a line saying which toolchain it readied. If it fails, report the
error verbatim and stop.

**The repo under test can be in any language.** The agent is reached through the
repo's own harness, and neither the harness nor this brief cares what it is
written in. Do not assume Python because this skill's own scripts are Python;
those are unrelated and run under whatever `python3` is already on the machine.

**If it stops because there is no manifest it recognises**, that is common: plenty
of small agent repos document their install as one line in the README and never
pin anything. Do whatever the README says yourself, then carry on. For a Python
repo you can also pass the packages explicitly, and pin the evaluation client:

```bash
bash <skill>/scripts/bootstrap.sh <pkg> <pkg> "agentx-python==<version>"
```

Pin the client because a before-and-after comparison whose scoring library changed
underneath is not a controlled comparison. Note the missing manifest in your final
report as a reproducibility gap: the next run will have to guess the same way you
just did, and it may guess differently.

If the bootstrap created a virtualenv, make sure it cannot be committed:

```bash
grep -q '^\.venv/$' .gitignore && echo ".venv ignored, good" || echo "WARNING: .venv not ignored"
```

If it is not ignored, add the line now, before going any further.

## Phase 3: launch

Locally there is no wall-clock ceiling, so the foreground is fine for a small
dataset. For anything that will run more than a few minutes, detach it so a
command timeout cannot take the run down after the money is spent:

**Use the repo's own run command**, whatever that is - the line its README gives,
its `package.json` script, its Makefile target. Write that command into the
mapping table before you launch, because it is one of the surfaces the comparison
is keyed on: v1 and v2 have to be produced the same way, and "how the harness was
started" is easy to change by accident between two runs.

```bash
mkdir -p .eval-logs
AGENTX_API_BASE_URL="${AGENTX_HOST:-http://localhost:4700}/api/v1" \
nohup <the repo's run command> > .eval-logs/eval-v2.log 2>&1 &
echo "launched pid $!"
```

**Pass the base URL explicitly, even when it is the default.** The harness reads
`AGENTX_API_BASE_URL` through the SDK and knows nothing about `HOST`, so an engine
you reached with `AGENTX_HOST` is an engine the harness will miss — it would send
v2 to the hosted platform, or to whatever a stale `.env` still names, and score
cleanly against the wrong database. Deriving one from the other on the launch line
keeps the two halves of this workflow pointed at the same engine.

For a Python harness that command is usually `.venv/bin/python -u <script>`, and
**the `-u` is not optional**: without unbuffered output the log stays empty until
the process exits and you cannot tell "still running" from "hung". Other
toolchains have their own equivalent - `node` is unbuffered already, Go's
`log` package writes straight through - so check rather than assume the log will
fill as it goes.

### The abort check, 30 seconds in

```bash
sleep 30; cat .eval-logs/eval-v2.log
```

The first lines have to show it **reusing** the dataset from the export, against
the local base URL. If it published a new dataset, or if the URL is not the local
engine, kill it immediately:

```bash
pkill -f <evaluation script>
```

then report that and stop. Every second past that point is spend on a result
nothing can be compared to.

While you are here, capture the run id from the startup banner.

Then poll:

```bash
sleep 60; tail -20 .eval-logs/eval-v2.log
```

Expect roughly: a banner, per-case progress, a scored-results line, a finalize
line, then whatever tail the harness prints.

### Three log lines that look alarming

**`✓ Scored 0 results`, with `accepted=0 duplicates=10` on a brand new run.** The
run is fine and all ten are stored and rated. This is a timeout race, and it is
specific to self-host: the engine scores every result in the batch with a judge
call *inside* the `append_results` request, while the SDK gives that request a
30 second timeout and then retries it up to three times. A batch that takes
longer than 30 seconds to score — which on a 10-result batch it does — is
abandoned client-side while the engine is still working, retried, and eventually
answered by an engine that has by now inserted every row. The retry therefore
reports them all as duplicates, and `accepted` is what the SDK prints.

Measured on two runs of the same size against the same engine: one scored in 25
seconds and printed `accepted=10 duplicates=0`; the other took 63 seconds and
printed `accepted=0 duplicates=10`. Same code, same batch size, same outcome in
the database. The only difference was how long the judge took.

So the line tells you nothing about whether the evaluation worked. Read
`ratedCount` from `liveStatistics` instead. If you want the line to stop lying,
raise the SDK's timeout or submit smaller batches — but neither changes the
result, so do not do it in the middle of a comparison.

**`Batch 16a74773: accepted=2 duplicates=5 failed=0`.** The ordinary version of
the same thing. `accepted` counts rows newly inserted, not answers scored.
Results are submitted in batches as they finish, and the finalize step submits
them again; the second submission collides on the idempotency key and each
collision resolves to the row already stored.

Do not read it as "only two answers were scored" and declare the run invalid. A
triage did exactly that and reported a real 5.71 over seven answers as a fake 2.50
over two, on a run whose stored results were all present and rated. `ratedCount`
from `liveStatistics`, or the length of `results[]`, is the answer to how many
were scored. The batch line is not.

**`Analyze failed: HTTP 404`, followed by an empty report.** An older engine
paired with an older SDK. It does not crash and it does not mean the evaluation
failed: the SDK swallows the 404, then swallows the follow-up `get_report()`
failure, then prints an empty report with no statistics and no recommendations,
which looks exactly like a run that scored nothing. The results are stored and
rated regardless; ignore the printed report and read the run over HTTP. Current
engines serve the SDK's analyze routes and current SDKs fall back to the
dashboard route on a 404, so seeing this at all means one side is behind.

## Phase 4: run the analysis

Self-host's Analyze is synchronous — one call, no polling, already finished when
it returns. Older engines served it only from the dashboard router and 404'd the
SDK's `analyze_run()` / `get_analysis_status()` / `get_report()`; current ones
serve both, and current SDKs fall back to the dashboard route on a 404. Calling
the dashboard route yourself works against every combination, so it is what this
brief uses.

```bash
python3 <skill>/scripts/fetch_analysis.py <v2_run_id> --analyze \
  --write-export eval-analysis/exports/
```

Or directly, if you would rather see the raw call:

```bash
curl -s -X POST "${AGENTX_HOST:-http://localhost:4700}/api/v1/evaluate/analyze/<v2_run_id>" \
  -H "x-api-key: $AGENTX_API_KEY" -H "Content-Type: application/json" \
  -d '{"qualityMode":"balanced"}'
```

It samples the worst and best results, re-rates each with up to three judges, and
writes one narrative. Give it a few minutes on a large run. A 422 means the engine
has no provider key for the judge model — report that rather than retrying.

**If Analyze fails, the comparison still stands.** Every number in the table below
comes from the stored ratings, not from the analysis. What you lose is the new
report's recommendations section. Say so and carry on.

## Phase 5: write the comparison

Write `eval-analysis/v2-report-<EVAL_ID>.md`:

```markdown
# v1 vs v2: <dataset name>

- Dataset id: <id>   (identical in both runs)
- v1 evaluation id: <EVAL_ID>
- v2 run id: <from the log>
- Engine: <the Base URL both runs used>
- Status: complete | analysis_failed
- Changes under test: eval-analysis/mapping-<EVAL_ID>.md

## Scores

| Metric | v1 | v2 | Delta |
|---|---|---|---|
| Average rating | | | |
| Min | | | |
| Max | | | |
| Rated results | | | |
| Rating variance | | | |
| Consistency | | | |
| Instruction adherence | | | |
| Vector similarity | | | |
| Jaccard | | | |
| BLEU | | | |
| ROUGE-L | | | |
| Code scorers | | | |

## What moved

## The new report's recommendations

## Verdict
```

Drop any row the dataset's grading config did not enable — the similarity metrics
and code scorers are each opt-in per config, and an empty row invites the reader
to think something regressed to nothing.

### Where to read the scores from

```bash
curl -s "${AGENTX_HOST:-http://localhost:4700}/api/v1/evaluate/<run_id>" -H "x-api-key: $AGENTX_API_KEY"
# .liveStatistics -> averageRating, minRating, maxRating, ratedCount
# .results[]      -> per-question rating, justification, similarity, codeScorerResults
```

**Note the router.** `liveStatistics` is on `/api/v1/evaluate/{id}`, the dashboard
route. The SDK-facing `/api/v1/custom-agent-evaluations/runs/{id}` is a different,
smaller object — `{runId, datasetId, status, resultCount, averageRating}` — with
no `liveStatistics`, no minimum, no maximum and no per-result array. Asking it for
the fields this table needs returns nothing, which reads as a run that stored
nothing.

`liveStatistics` is recomputed from the stored per-result ratings on every read,
which makes it the number to trust. The analysis carries its own `statistics`
block computed the same way from the same rows, so the two agree — unless results
landed after Analyze ran, in which case the analysis is describing a subset and is
stale rather than wrong. `fetch_analysis.py` compares them and says so in the
export when they diverge.

`ratingVariance` is a real field on the analysis statistics here, so report it
directly rather than substituting max-minus-min.

Take the v1 side from the earlier run the same way, so both sides come from the
same source.

**One number in the analysis is not a score.** Its judge-evidence table carries a
`finalScore` per sampled answer: the average of two or three fresh judges re-rating
that answer to measure how much they agree. It is not the stored rating and not
what the run was scored on. A `split` disagreement band marks a question the rubric
leaves ambiguous, which is a finding about the dataset, not about the agent. Do not
put it in the scores table.

**Under "What moved"**, one short paragraph per change from the mapping table that
the data can say something about, keyed to the metric that moved. Be careful about
attribution: this was a single pass that applied every change at once, so no
individual change can be isolated from this run. Say that plainly rather than
implying a causal story the data does not support. If someone wants per-change
attribution, that is one run per change, and it is worth naming as the option
rather than faking it.

**Under "The new report's recommendations"**, list them verbatim, each with a
one-line note on whether it is a genuine new finding or the same code-blind
inference pattern as last time. That note is what stops the next iteration from
chasing the same rejected advice again.

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

## Phase 6: commit

```bash
git add eval-analysis/
git status --porcelain
```

Read that output. Nothing under a virtualenv directory, `__pycache__/`, `.env` or
`.eval-logs/` may be staged. Use explicit paths, never `git add -A`: the bootstrap
step created a virtualenv in this directory a few minutes ago.

```bash
git commit -m "eval v2 for <EVAL_ID>: <v1 average> -> <v2 average>"
git push -u origin HEAD
```

A refused push is a reportable outcome, not a failure: say so and continue.

## Phase 7: the report

Write this block to `eval-analysis/eval-report-<EVAL_ID>.md` first, then repeat
it as the last thing in your final message. This brief spends real money before it
reports, so putting the numbers on disk first means a run that is cut off after
the evaluation completed still has its results.

```
EVAL V2 COMPLETE
status:            complete | analysis_failed | aborted (<reason>)
evaluated_in:      .worktrees/eval-fix-<EVAL_ID> | the main checkout
engine:            <base url>   (health check: ok | failed)
dataset_id:        <id>   (same as v1: yes | no)
v2_run_id:         <id>
average:           <v1> -> <v2>   (delta <+/-x.xx>)
min:               <v1> -> <v2>
max:               <v1> -> <v2>
rated_results:     <v1> -> <v2>
variance:          <v1> -> <v2>
consistency:       <v1> -> <v2>
noise_floor:       <the v1 spread on similar answers, for reading the delta>
verdict:           worked | partial | regressed
report_file:       eval-analysis/v2-report-<EVAL_ID>.md
log_file:          .eval-logs/eval-v2.log
commit:            <short sha>
pushed:            yes | no (<reason>)
stray_env_deleted: yes | no
```

## Last: offer the pull request

Now, and not before, there is something worth putting in front of a reviewer: the
change, the reasoning, and whether it worked.

**Ask with AskUserQuestion and wait.** Opening a pull request on someone's
repository is an outward-facing act, and the verdict above is exactly the thing
that should decide it. Offer the three real options:

- **Open it** with the before-and-after in the body.
- **Push only**, leaving the branch for them to open themselves.
- **Neither**, if the numbers went the wrong way and the branch should just be
  deleted.

A regression is a perfectly good outcome to report and a bad one to raise a pull
request for. If the verdict is `regressed`, say so plainly in the question rather
than asking a neutral question over a bad result.

If they say open it:

```bash
gh pr create --base "$(git remote show origin | sed -n 's/.*HEAD branch: //p')" \
  --head "eval-fix/<EVAL_ID>" --title "..." --body-file <file>
```

Pass the body as a file rather than a string. It contains markdown tables full of
quotes and backticks, and shell quoting will mangle them.

Title it after what was actually done, not after the eval id. The body leads with
the score movement - average, and the **minimum**, which is usually the more
honest number - then the applied and rejected counts, then a link to the mapping
table by path. Name any question that regressed. A reviewer who finds a
regression you did not mention stops trusting the rest of the summary.
