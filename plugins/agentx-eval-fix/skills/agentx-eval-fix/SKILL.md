---
name: agentx-eval-fix
description: >-
  Turn an AgentX self-host evaluation into a triaged code fix and a re-run against
  the same dataset, so a before-and-after comparison means something. Use whenever
  someone has an evaluation on a local AgentX self-host engine (AgentX-trace-eval,
  normally http://localhost:4700) and wants to know what to actually change in the
  code, or wants to re-run an evaluation to compare scores. Also use when a request
  mentions a self-host evaluation id or dataset id, an agent that scored badly, an
  AI Analysis or judge findings from the Evaluate tab, or the question "the report
  tells me what is wrong with the answers but not what to fix in my code". The core
  move is triaging code-blind judge recommendations against the real source instead
  of applying them literally.
---

# Fix an agent from its evaluation, then re-score it

An AgentX analysis tells you what was wrong with an agent's *answers*. It cannot
tell you what to change in the code, because the judge that wrote it never saw
the code. This skill closes that gap: it puts the analysis and the repo side by
side, triages one against the other, applies what survives, and re-runs the
evaluation against the same dataset so the two scores mean something next to each
other.

The report is not a to-do list. On a real export, two of five recommendations
asked for things that already existed in the repo, and a third would have lowered
the score. Treat its evidence as reliable and its recommendations as hypotheses.

**This targets AgentX self-host only** — the local engine from AgentX-trace-eval,
listening on `http://localhost:4700` by default. Its API is a different dialect
from the hosted platform's, and several of the endpoints the hosted flow depends
on do not exist here. Everything below is written against the local one.

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

**Otherwise**, the rest of this page is for you.

## Starting from an evaluation id

The normal entry point. Copy the id from the run's card in the dashboard's Evaluate
tab — the `ID p6sLDw9CPv0XF0eiUA_zF` chip has a copy button next to it — and hand it
over with nothing else:

```
Use the agentx-eval-fix skill on evaluation p6sLDw9CPv0XF0eiUA_zF.
```

That is the whole invocation. From the id alone, everything else is discoverable:
`fetch_analysis.py <id>` resolves the engine and the key, and the run carries its own
dataset id, grading config, subject metadata and per-result ratings. Run it from inside
the repo that holds the agent, because the triage reads that source.

**An id is enough; an analysis is not required.** The per-result rows — rating, expected
versus actual, the judge's per-answer justification, the similarity metrics and any code
scorer output — are on the run from the moment it finishes, and they are the reliable
half. The numbered recommendations only exist once someone runs Analyze, and they are the
half this skill exists to be sceptical of. Start from the lowest-rated rows.

## Connect to the engine

Two values, and both have a default worth knowing.

```bash
python3 <skill>/scripts/fetch_analysis.py --list
```

That is the whole connection test. It resolves the base URL from
`$AGENTX_API_BASE_URL`, falling back to `http://localhost:4700/api/v1`, and the
key from `$AGENTX_API_KEY`, then from `~/.agentx/config.json` — which it verifies
against the engine before using. If it prints evaluations, you are connected.

Three things about this that cost a run each when assumed wrong:

- **Keys are per project, and so is the data.** The key *is* the project
  selector; an evaluation belongs to exactly one project and is a 404 under
  every other key. `curl -s http://localhost:4700/api/v1/projects` lists them all
  with their keys, given any one project's key to authenticate with.
- **`~/.agentx/config.json` can be a different engine's key.** It records
  whichever engine last ran on this machine. A Docker instance keeps its database
  in its own volume and mints its own keys, so the file and the port disagree the
  moment anyone runs the container. `fetch_analysis.py` now verifies that key with a
  real read before using it and says so plainly when it fails, rather than letting it
  surface as a 401 against the evaluation id. **`GET /dev/bootstrap` no longer exists** —
  the engine removed the unauthenticated handout on purpose, with a test asserting it
  404s, so keys are copy-pasted from the engine's startup output or the dashboard.
- **Ids are nanoids**, e.g. `oE1YMG5wqmu4j2bhTtw1X`, not the 24-character hex ids
  the hosted platform uses. There is no filename to read one out of, so `--list`
  is how you find one.

## Getting the analysis

```bash
python3 <skill>/scripts/fetch_analysis.py <evaluation_id> \
  --write-export <repo>/eval-analysis/exports/
```

This writes the markdown the triage brief reads, and prints the same data as JSON
on stdout. Show the user the baseline numbers and the count of recommendations
before going further. That is the shape of the job.

### An evaluation has no analysis until someone asks for one

This is the one structural difference from the hosted platform. There, a finished
run has a report waiting. Here, `analysis` is absent until the Analyze button is
pressed or something calls the endpoint, and a fresh run reports
`analysis_status: not_started`.

```bash
python3 <skill>/scripts/fetch_analysis.py <evaluation_id> --analyze \
  --write-export <repo>/eval-analysis/exports/
```

**Ask before you pass `--analyze`, and wait for the answer.** Asking and then proceeding
anyway is worse than not asking: it spends the reader's attention without giving them the
choice. It is a real judge pass — every sampled item
re-rated by up to three judges, then one more call to write the narrative — billed
to whichever provider key the engine holds. It also needs that key to exist:
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` in the engine's
environment, or set from the dashboard's Platform Settings. Without one it fails
with a 422 naming the missing key.

**Scoring fails differently, and silently.** The same missing key does not fail the
run that produced the results: each result is stored with `rating 0` and the reason
in its own `justification` (`Judge model "..." needs a ... API key`), while the
harness prints that it scored and finalised normally. A run whose average is absent
and whose `ratedCount` is 0 has not been judged at all - check the ratings before
triaging anything, or you will triage a report about an unscored run.

Analyze is **synchronous** here — no job queue, no polling. The HTTP call holds
open for the whole pass and comes back already finished.

**The analysis is the input this skill triages**, so running it is the normal
path, not an upsell. What follows is the fallback when someone declines the
spend.

**A triage without an analysis is still worth doing.** The stored per-result
ratings, the rubric, the judge's per-answer justifications, the similarity metrics
and the code-scorer results are all on the run regardless. What you lose is the
numbered recommendations, which is to say the part of the report this skill exists
to be sceptical of. Table 2 of the mapping table — defects the report could not
see — does not depend on it at all.

### How the run was produced changes what you can conclude

`runSource` on the run says which of two paths created it, and they are not
equivalent evidence.

- **`sdk`** — your own harness computed the answers and pushed them. It honours
  the dataset's `number_of_requests`, so each question appears more than once and
  a single unlucky sample is visible as such. It is also the path that can attach
  a `traceId` to each result.
- **`connector`** — the engine drove the dataset through a registered URL itself.
  One pass per question, no repetitions and no smoke-test variants, so a
  seven-question dataset yields seven results and every score is a single sample.
  Read single-question movements here with much more caution than an SDK run's.

**Check whether results carry a `traceId` before trusting anything the report
says about tool use.** The engine renders the agent's real execution path into
the judge prompt only for results that link a trace. Without one the judge sees
answer text alone, cannot tell a correct retrieval-backed citation from a
fabricated one, and reliably concludes the agent has no working retrieval and
"may be fabricating tool results". Recommendations of that shape are an artefact
of the wiring, not a finding about the agent — verify against the source before
spending a row on them.

### Which rubric actually graded the run

Self-host splits the grading surface across two objects, and reading the wrong one
is how a triage rejects a good recommendation.

- **The criteria** come from the run's `evaluationSettings`. If the run named a
  standalone grading config, that config's criteria apply and the dataset's own
  criteria were never consulted.
- **`expectedResults` and `judgeGuideline`** always come from the dataset's
  questions, whichever config supplied the criteria.
- **The judge prompt and judge model** live only on the grading config.

`fetch_analysis.py` resolves this for you and labels the export's Grading criteria
section with where each half came from, printing the dataset's overridden criteria
separately when they differ. If you read the criteria off the dataset by hand,
check that first.

## Local workflow

### 1. Get the analysis into the repo

```bash
python3 <skill>/scripts/fetch_analysis.py <evaluation_id> \
  --write-export <repo>/eval-analysis/exports/
```

### 2. Do the triage

Read `references/triage-brief.md` and carry it out yourself, against the repo,
with the repo as your working directory.

The parts that matter most: write the mapping table before touching any code,
keep the frozen surfaces frozen, and do not run the evaluation during the triage.

### 3. Stop and show the mapping table

`eval-analysis/mapping-<EVAL_ID>.md` is the deliverable of the triage and the
checkpoint of the whole workflow. Everything up to here was free; the next step
is not.

Having summarised it, **ask with AskUserQuestion whether to re-run now** rather than
ending on "let me know". Two options — re-run now, or not yet — with the cost stated
concretely in the re-run option (questions × runs invocations plus the judge pass, read
off the dataset rather than guessed) and, in the other, one line on how to come back to
it. Add a third option only if the triage produced a real one, such as an unresolved
`RUBRIC-CONFORMING` row to check first. The user has just read the verdict counts; that
is the moment they can decide, and a button beats a paragraph.

### 4. Re-run the evaluation

Read `references/eval-brief.md` and carry it out. Two things there are worth
repeating because they are the expensive failures:

- **Check the dataset id and the base URL before launching.** A harness handed no
  dataset id publishes a brand new one and scores against freshly created
  questions; a harness with `AGENTX_API_BASE_URL` unset talks to the hosted
  platform instead of your engine. Neither errors.
- **`.analyze()` against self-host depends on the versions in play.** It posts to
  `/custom-agent-evaluations/runs/{id}/analyze`, which older engines do not implement
  and which older SDKs do not fall back from — and the SDK swallows the failure, so the
  symptom is an empty report that reads like a run which scored nothing, not an error.
  Current engines serve that route, and current SDKs fall back to the dashboard route on
  a 404, so the pairing works in three of four combinations and fails silently in the
  fourth. If a report comes back empty, check the engine's analyze route before believing
  it. `fetch_analysis.py --analyze` calls the dashboard route directly and works either way.

`scripts/bootstrap.sh` builds a virtualenv from `requirements.txt`, a
`pyproject.toml`, or packages you name on the command line.

## Reading the mapping table

The checkpoint. In order:

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

- **The minimum matters at least as much as the average.** A low minimum means
  some questions fail badly and unpredictably, which is a different problem from a
  uniformly mediocre mean and usually the one that grounding fixes.
- **No individual change can be credited.** A single pass applies everything at
  once. Per-change attribution costs one run per change, and it is worth naming as
  an option rather than implying the data supports a story it does not.
- **Read the scores from `liveStatistics`** on `GET /api/v1/evaluate/<run_id>`.
  It is recomputed from the stored per-result ratings on every read. The
  analysis's own `statistics` block is computed the same way, so the two agree —
  unless results landed after Analyze ran, in which case the analysis is stale and
  `fetch_analysis.py` says so in the export.
- **`ratingVariance` is a real field here**, on the analysis statistics. Report it
  rather than substituting max-minus-min.
- **State the noise floor.** If the first run's judge scored structurally similar
  answers several points apart, a smaller movement in the average is not a result.
- **The multi-judge `finalScore` is not the score.** Analyze re-rates a sample of
  answers with fresh judges purely to measure agreement. Those numbers sit beside
  the stored ratings and are not what the run was scored on; a `split`
  disagreement band marks an ambiguous rubric, not a bad answer.

## Adapting the briefs

The briefs are written against roles, not names: "the agent's instruction string",
"the tool registry", "the retrieval configuration", "the grading surface". That is
what lets them work on a repo nobody has seen before, and it is worth preserving
when editing them.

The one principle they turn on, and the one to keep if anything else is cut:
**freeze anything the comparison is keyed on.** The test questions, the grading
criteria, the judge prompt and model, the code scorers, the tool inventory, the
knowledge base and the agent's model all have to be identical across the two runs.
Change any of them and the second number is not comparable to the first, which is
the only reason the second run exists.
