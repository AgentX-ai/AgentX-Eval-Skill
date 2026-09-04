---
name: auto-improve
argument-hint: "[report-id] [extra instructions]"
description: >-
  Turn an AgentX improvement report - production failures a human CONFIRMED during signal
  review, clustered into issues with recommendations - into triaged fixes applied to the
  agent's source code. Use whenever someone has an improvement report id from a self-host
  engine (AgentX-trace-eval, normally http://localhost:4700), asks to "apply the improvement
  report", "fix what the reviewers confirmed", or mentions the dashboard's Insights >
  Auto-improve surface. This is the ONLINE counterpart of /eval-fix: the evidence is live
  traffic a human vouched for, not an offline dataset run, and the core move is the same -
  triage code-blind recommendations against the real source instead of applying them
  literally.
version: 2.9.0
metadata:
  author: AgentX <marcin@agentx.so>
  tags:
    - agentx
    - auto-improve
    - production-failures
    - triage
    - self-host
---

# Apply an improvement report to the agent's source

An improvement report is the output of AgentX's close-the-loop flow: online scorers and
failure patterns flag production traffic, a human reviewer **confirms** which flags are real
(Review > Review signals - every Confirm accumulates automatically), and the confirmed set is
spent on one analysis pass that clusters the failures into issues, each with evidence and a
recommendation. The report is id-addressable; the id is your input.

Two properties make this evidence unusually trustworthy, and one property makes it
untrustworthy in a specific way:

- Every item was flagged by a machine **and** confirmed by a person - this is the
  high-precision slice of production traffic, not a sample.
- The evidence (input, output, judge rationale) was snapshotted at confirm time - it cannot
  have drifted since.
- The recommendations were written **without seeing the code**. They are hypotheses about the
  agent, not instructions. On evaluation reports, sibling skill /eval-fix routinely finds
  recommendations that ask for things the repo already does, or that would make things worse.
  Expect the same here. Triage, never transcribe.

**Self-host only** - the engine from AgentX-trace-eval, `http://localhost:4700` unless `HOST`
says otherwise.

## Starting from a report id

The normal entry point. The dashboard's **Insights > Auto-improve** view generates reports and
shows each id with a copy button. The invocation is just:

```
Use the agentx auto-improve skill on report Xq3f9kLm2....
```

Fetch it (stdlib only, no SDK needed):

```bash
python3 scripts/fetch_report.py <report-id>          # full report as JSON
python3 scripts/fetch_report.py --list               # every report on the engine, newest first
```

The script resolves the engine from `--base-url` / `--host` / `$AGENTX_API_BASE_URL` /
`$AGENTX_HOST` / `$HOST` (default localhost:4700) and the key from `$AGENTX_API_KEY`, then
`~/.agentx/config.json`, then the engine's own `/auth/config` handout - verifying each with a
real read. Keys are per project: a report generated in one project is invisible to another
project's key, so "report not found" usually means "wrong key", and the script says which.

## The brief

Work the report issue by issue, in order (the generator lists them by weight of evidence):

1. **Read the evidence before the recommendation.** Each issue carries the confirmed failures
   behind it: user input, agent output, the judge's rationale, the score. The evidence is what
   actually happened; the recommendation is one model's guess at why.

2. **Find the implicated code.** Locate the prompt, tool, retrieval step, or control flow the
   issue actually passes through. If the repo has no plausible site for the issue, say so -
   "this failure originates outside this repo" is a finding, not a miss.

3. **Triage the recommendation against the source.** Three verdicts, same as /eval-fix:
   - **Apply** - the recommendation names a real gap, visible in the code. Make the smallest
     change that closes it.
   - **Already handled** - the code already does what is asked; the failure has another cause.
     Look for that cause in the evidence before moving on.
   - **Reject** - applying it would contradict the evidence, fight another requirement, or
     degrade behavior the report cannot see. Write one line saying why.

4. **Prefer the change class the evidence points at.** Judge rationales complaining about
   *content* (invented policy, missing empathy, no next step) usually mean prompt or grounding
   changes. Failures about *actions* (wrong tool, malformed arguments, missing lookup) usually
   mean tool schemas or flow. Do not rewrite the whole prompt to fix one clause.

5. **Deliver a triage table.** Issue by issue: verdict, what changed (file:line), or why not.
   The person reading it confirmed these failures by hand in review - they know the evidence;
   what they need from you is what it means in *their* code.

## Verifying the fix

This report came from live traffic, so the honest verification is live traffic: the same
scorers keep running, and a fixed failure mode stops re-raising its signals (a re-fire after a
"Fixed" resolve reopens the signal as a regression - that is the engine telling you the fix
did not hold). For a pre-deploy check, curate the confirmed failures into a dataset (the
dashboard's curation flow) and run /run-eval against it - then /eval-fix's before/after
machinery applies.

## When there is no report id

`--list` shows every report; if there are none, the script says whether confirmed failures are
waiting in a group (generate the report in Insights > Auto-improve - it is one explicit,
billed LLM call) or whether nothing has been confirmed yet (that happens in Review > Review
signals; Confirm is the accumulation gesture).
