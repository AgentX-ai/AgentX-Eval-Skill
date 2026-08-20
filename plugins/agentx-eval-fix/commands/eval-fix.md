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

**Do not pass `--analyze` without asking.** It spends judge calls. It is also optional:
the per-result ratings, the rubric, each answer's judge justification and the similarity
metrics are all present the moment a run finishes, and they are the reliable half. The
numbered recommendations are the code-blind half this workflow exists to be sceptical of.
If there is no analysis, say so in one line and work from the results.

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
