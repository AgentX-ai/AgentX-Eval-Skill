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

### If the run has no analysis, stop and ask

The export's header says `Analysis status: not_started` when nobody has pressed Analyze.
That is a spend decision and it is the user's, so **ask with AskUserQuestion and wait for
the answer**. Do not announce which way you are leaning and carry on regardless: either
the question is worth asking and you wait for it, or it is not and you should not have
raised it.

Put the real trade-off in the options:

- **Run it** costs one judge pass over the whole run, billed to whichever provider key the
  engine holds, and produces the numbered recommendations. It takes a minute or two and
  the call is synchronous.
- **Skip it** loses nothing you need to do this work. The per-result ratings, the rubric,
  each answer's own judge justification and the similarity metrics are all on the run the
  moment it finishes, and those are the reliable half. The recommendations are the
  code-blind half this workflow exists to be sceptical of, and on the run this command was
  built from, three of eight were rejected outright while none of the five that survived
  said anything the per-result evidence had not already shown.

Then act on the answer. If they say run it, re-fetch with `--analyze` and wait for it to
finish before going on. If they say skip, say in one line that Table 1 will be empty by
fact rather than omission, and go straight to the results.

**If the run already has an analysis, do not ask.** It costs nothing to read what is
already there.

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

## 4. Re-run only when asked

On approval, follow `${CLAUDE_PLUGIN_ROOT}/skills/agentx-eval-fix/references/eval-brief.md`.
Re-run against the **same dataset id** and keep every frozen surface frozen: questions,
all three criteria strings, judge prompt and model, similarity metrics, code scorers, tool
count, knowledge base and agent model. Then write the before/after, reporting the minimum
and the variance alongside the average, and naming any question that regressed.
