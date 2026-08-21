---
name: eval-fix
description: Triage an AgentX self-host evaluation against this repo's source and fix the agent
argument-hint: <evaluation-id> [extra instructions]
---

Run the `agentx-eval-fix` workflow against evaluation **$1** on the AgentX self-host
engine, from the current repo. The engine is local unless `HOST` says otherwise.

Additional instructions from the user (may be empty): $ARGUMENTS

Carry this out yourself, in order. Do not delegate it to a subagent.

## 1. Ask where the engine is, once

**Before anything else, ask with AskUserQuestion which engine to read from.** One
question, at the start, and then not again: the answer holds for the whole workflow.

It goes first because the address decides which database every number in this run comes
from, it is invisible in the output until something fails, and an evaluation read from
the wrong engine wastes the entire workflow — including the paid re-run at the end. One
button press up front is cheaper than any of the ways that goes wrong. It is also the
only moment the user is not yet holding results they want to act on.

Three choices, so it is a menu and not a prompt:

| Option | Address | Pass as |
|---|---|---|
| **local** (default) | `http://localhost:4700` | `--host local` |
| **agentx** | `https://api.agentx.so` | `--host agentx` |
| **other** | whatever they type | `--host <what they typed>` |

Put **local** first and mark it the default: it is right for most people and is what the
script assumes with no flag at all. **other** is AskUserQuestion's free-text answer, and
the address can be anything reachable — `http://10.0.0.5:4700`, `agentx.internal:4700`,
`https://evals.example.com`. A scheme-less address is completed as `http://` on port
4700, and **plain `http://` on an internal network is a normal answer, not a mistake to
correct**.

Add a fourth option only when `$AGENTX_HOST` or `$AGENTX_API_BASE_URL` names something
that is neither of the two — read them *before* asking, quote the value literally, and
offer it. Someone who configured it once should not retype it.

**About the agentx option.** This skill is written against self-host's API, and the two
are different dialects: the analysis it reads lives on self-host's dashboard router, and
hosted evaluation ids are 24-character hex where self-host's are nanoids. The script
knows which kind of address it is on — it accepts hex ids only against `agentx`, warns
on the reverse, and answers a missing route there with "this is the hosted platform,
which serves a different router" rather than a bare 404. So offer it when that is where
the user's evaluation lives; just never as equivalent to self-host, and if a read fails
there with a routing message, say that plainly rather than retrying variations.

Ask it exactly once. Skip it only when the user already named an address in the same
breath as the id — `/eval-fix <id> our engine is at http://10.0.0.5:4700` — because an
answer they just gave is an answer, not a question to repeat. Confirm which engine you
are using and move on.

**Say it can be changed at any time**, in one clause, when you first report the numbers:
naming a different address later switches every subsequent command to it. If they do
switch after the fetch, re-fetch from the new engine before triaging anything — an
export from one engine and a re-run against another is the exact failure this question
exists to prevent.

Then pass the answer as `--host <address>` on every call to the script, and record it in
the mapping table beside the dataset id. **Each Bash call is a fresh shell**, so there
is no exporting it once: the address goes on each command that needs it, including the
harness launch line in step 5 as `AGENTX_API_BASE_URL=<address>/api/v1`.

## 2. Pull the evaluation

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/agentx-eval-fix/scripts/fetch_analysis.py $1 \
  --host <the address from step 1> --write-export eval-analysis/exports/
```

The script verifies the project key before using it and prints the engine it reached on
its first line of stderr — **say which engine the numbers came from** when you report
them, in the same breath as the scores. If it reports no usable key, relay its message;
it names where to get one.

**If it fails, the address is the first suspect, not the last.** Three failures mean
"possibly the wrong box", and each names the address it tried: `cannot reach the engine
at ...`, `no usable API key for ...`, and a `404` on an id the user believes exists. Ask
again with AskUserQuestion rather than declaring the engine down — quote the address, and
offer both real causes: it is somewhere else, or it is the right engine and the key
selects a different project, since a run is invisible to every key but its own. Keys are
per engine as well as per project, so a remote engine needs one minted by that engine.

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

## 3. Triage

Follow `${CLAUDE_PLUGIN_ROOT}/skills/agentx-eval-fix/references/triage-brief.md` exactly
and in order, against this repo.

Start from the lowest-rated rows and read outward. The single most valuable output is
Table 2 — defects found by reading the source that a judge working from answer text alone
could not have seen. If Table 2 is empty, Phase 1 is not finished.

## 4. Stop at the mapping table

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

Then act on the answer. On "re-run now", go straight to step 5 without asking again. On
"not yet", stop cleanly: the branch is committed and the report is on disk, so say where
both are and finish.

## 5. Re-run only when asked

On approval, follow `${CLAUDE_PLUGIN_ROOT}/skills/agentx-eval-fix/references/eval-brief.md`.

Do not open a pull request before this point. Until the re-run exists there is no
before-and-after to put in it, which is the only thing that makes it worth
reviewing. Push the branch in step 4, and offer the PR once the comparison is in
hand - asking first, and saying plainly if the numbers went the wrong way.
Re-run against the **same dataset id** and keep every frozen surface frozen: questions,
all three criteria strings, judge prompt and model, similarity metrics, code scorers, tool
count, knowledge base and agent model. Then write the before/after, reporting the minimum
and the variance alongside the average, and naming any question that regressed.
