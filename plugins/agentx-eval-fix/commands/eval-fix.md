---
description: Triage an AgentX self-host evaluation against this repo's source and fix the agent
argument-hint: <evaluation-id> [extra instructions]
---

Run the `agentx-eval-fix` workflow against evaluation **$1** on the local AgentX self-host
engine, from the current repo.

Additional instructions from the user (may be empty): $ARGUMENTS

Carry this out yourself, in order. Do not delegate it to a subagent.

## 1. Pull the evaluation

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/agentx-eval-fix/scripts/fetch_analysis.py $1 \
  --write-export eval-analysis/exports/
```

The script resolves the engine URL and the project key on its own and verifies the key
before using it. If it reports no usable key, relay its message — it names where to get
one — and stop rather than guessing.

Show the user the baseline numbers (average, **minimum**, variance, rated count) and how
many recommendations came back, before going further.

### If the evaluation has no analysis, offer to run one

The export's header says `Analysis status: not_started` when nobody has analysed
this evaluation yet. **The analysis is what this workflow triages** - the numbered
recommendations are the input to Table 1, and without them there is nothing to
check against the code except what you find by reading it.

So the normal answer is to run it. It still costs a judge pass over the
evaluation, billed to whichever provider key the engine holds, so it is the
user's call: **ask with AskUserQuestion and wait for the answer.** Do not
announce which way you are leaning and carry on regardless.

Frame it as what it is:

- **Run it** (the expected path) produces the recommendations this triage exists
  to test. One judge pass over the evaluation, synchronous, a minute or two.
- **Skip it** still works, but it is the reduced version: Table 1 is empty by
  fact rather than omission, and the triage runs on the stored per-result
  ratings, the rubric, each answer's judge justification and the similarity
  metrics alone. Say so in one line if they choose it.

Then act on the answer. If they say run it, re-fetch with `--analyze` and wait
for it to finish before going on.

**If the evaluation already has an analysis, do not ask.** It costs nothing to
read what is already there.

## 2. Triage

Follow `${CLAUDE_PLUGIN_ROOT}/skills/agentx-eval-fix/references/triage-brief.md` exactly
and in order, against this repo.

Start from the lowest-rated rows and read outward. The single most valuable output is
Table 2 — defects found by reading the source that a judge working from answer text alone
could not have seen. If Table 2 is empty, Phase 1 is not finished.

## 3. Stop at the mapping table

Write `eval-analysis/mapping-$1.md`, apply what survived on a branch in a worktree, and
**stop**. Everything to this point is free; the re-run is not. Summarise for the user:
verdict counts, what Table 2 found, and anything marked `RUBRIC-CONFORMING`, which only
they can settle.

Spot-check the worst question with one direct agent invocation before you claim the fix
works. A prompt rule that looks right often is not — and one invocation is far cheaper
than discovering it after a full run.

### Then ask, with buttons

Having summarised, **ask with AskUserQuestion whether to re-run now** — do not end the turn
on an open-ended "let me know". The user has just read the verdict counts and the spot-check;
that is the moment they can actually decide, and a button is a decision where a paragraph is
a chore.

Ask exactly one question, and put the cost in the option descriptions rather than in a
warning above them:

- **Re-run now** — the same dataset, every frozen surface frozen. Say what it spends in
  concrete terms: N questions × M runs agent invocations plus the judge pass, and roughly
  how long. Read N and M off the dataset instead of guessing.
- **Not yet** — the branch and the mapping table stay as they are; nothing is spent. Say in
  one line how to come back to it (re-run this command, or ask for the eval brief directly).

Add a third option only when the triage actually produced one — for example an unresolved
`RUBRIC-CONFORMING` row, where "check that figure first, then re-run" is a genuinely
different choice and not a hedge.

Then act on the answer. On "re-run now", go straight to step 4 without asking again. On
"not yet", stop cleanly: the branch is committed and the report is on disk, so say where
both are and finish.

## 4. Re-run only when asked

On approval, follow `${CLAUDE_PLUGIN_ROOT}/skills/agentx-eval-fix/references/eval-brief.md`.

Do not open a pull request before this point. Until the re-run exists there is no
before-and-after to put in it, which is the only thing that makes it worth
reviewing. Push the branch in step 3, and offer the PR once the comparison is in
hand - asking first, and saying plainly if the numbers went the wrong way.
Re-run against the **same dataset id** and keep every frozen surface frozen: questions,
all three criteria strings, judge prompt and model, similarity metrics, code scorers, tool
count, knowledge base and agent model. Then write the before/after, reporting the minimum
and the variance alongside the average, and naming any question that regressed.
