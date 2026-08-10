# Brief: triage an evaluation analysis against this code, then fix the code

You are working inside a clone of a repo that holds an AI agent and the harness
that evaluates it. An evaluation was run, a judge scored the answers, and the
resulting analysis export is in this workspace. Your job is to turn that export
into the right code changes.

You have no memory of any previous session, and everything you need is in this
file and in the repo. There is also no way to ask a question and get an answer:
this run has no interactive channel, so a question just ends the run with nothing
done. Where something is genuinely ambiguous, make the most defensible call,
apply it, and record the uncertainty in the mapping table.

Work through the phases in order. Do not start editing code before Phase 3.

## The thing to understand before you start

**The export is code-blind.** It was produced by a judge that saw the agent's
answers and nothing else. Not this source tree. Not the tool definitions. Not the
retrieval configuration. Not the model settings.

That asymmetry shapes everything the report says. Its observations about the
*answers* are first-hand and trustworthy: it really did see a wrong number, a
missing citation, a fabricated policy. Its recommendations about the *system* are
guesses, because it was reasoning backwards from outputs to a machine it could
not inspect. A judge that sees an ungrounded answer cannot tell the difference
between "this agent has no retrieval tool" and "this agent has a retrieval tool
and was not told to use it", so it will often recommend building something that
already exists.

So do not implement the recommendations. **Triage** them against the code, and
implement what survives. Some will be right. Some will describe things this repo
already does. Some will make the score worse if applied literally, because they
were written without sight of the grading criteria the answers are scored
against.

The most valuable rows in your output will be the defects you find in the code
that the report never mentions, because those are the ones nobody else could have
found.

## Phase 1: read, in this order, once each

Turns are limited and re-reading is expensive, so take notes as you go rather
than planning to come back.

1. **The analysis export**, under `eval-analysis/exports/`. Usually there is one
   `.md` file there and it is the one you want. Read it in full. Its filename
   contains the evaluation id in the form `..._analysis_<EVAL_ID>_<date>.md`;
   call that `<EVAL_ID>`, you will need it for output filenames. Pay particular
   attention to the numbered recommendations, the judge evidence section (which
   quotes real answers with the judge's reasoning, and is the most
   information-dense part of the document), and the statistics.

   **If there are several, do not read them all.** Read the first dozen lines of
   each to find its subject and dataset, pick the one describing the agent this
   repo actually implements, say which you chose and why in the mapping table,
   and leave the rest alone. Reading an unrelated 600 line export in full is
   turns you cannot spend twice, and a run hit its turn limit doing exactly that,
   having reached the right conclusion and then having nothing left to report it
   with.

   If none of them matches this repo, that is the finding. Say so at the top of
   the mapping table, treat every recommendation as `reject-wrong-premise`, and
   fix only what you can verify from the source.
2. **The agent's source.** Find where the agent is defined and read it: its
   instruction string, its tools, its knowledge or retrieval setup, its model
   configuration.
3. **The rubric: acceptance criteria, rejection criteria, evaluation criteria,
   and any per-case judge guidance.** These are what the answers were graded
   against, and a change that fights them lowers the score no matter how sensible
   the recommendation that motivated it sounded. The judge that wrote the report
   could see them; the report itself does not quote them.

   If the export has a **Grading criteria** section, that is the rubric
   first-hand, taken from the dataset. Use it.

   If it does not, the export was rendered without those fields and you have to
   recover them from the repo: find the script that runs the evaluation and read
   the criteria it publishes. That works when the dataset is defined in code.

   **Two places, one look each, then stop.** If neither the export nor the
   harness has the criteria, they are not recoverable from here and no amount of
   further searching will produce them. Record that in the mapping table, treat
   every recommendation about answer style, length or hedging as unverifiable,
   and carry on with what you can check: expected results, tool behaviour and
   arithmetic are all still verifiable without a rubric.

   This matters for your turn budget. A triage that keeps hunting for criteria
   that do not exist is how a run reaches its turn limit with the analysis done
   and nothing reported.
4. **The README**, if there is one.

## Phase 2: the mapping table, before you touch any code

Write `eval-analysis/mapping-<EVAL_ID>.md`. A human reads this to decide whether
to authorize an expensive re-run, so write it for that reader.

### Table 1: every numbered recommendation in the export

One row per numbered recommendation, none skipped, in order. Columns:

| # | Recommendation (short) | Verdict | Code location | Change | Derived from | Rationale |

**Verdict** is one of exactly these five. The set is deliberately shaped so that
"the report is wrong" has somewhere to go, because a taxonomy of only
apply/defer quietly pressures you into applying things that should not be:

- `apply`: correct as written, and the code is missing it.
- `apply-modified`: the underlying observation is real, but the proposed remedy
  has to change shape to fit this rubric or this codebase. Say what you kept and
  what you dropped.
- `reject-wrong-premise`: the recommendation asserts something about the system
  that is false. Name the file and line that disproves it.
- `reject-already-done`: the code already does this. Name the file and line.
- `reject-harmful`: applying it would lower the score. Quote the criteria string
  it would violate.

**Code location** is `file:LINE`, or `n/a` for a genuine no-op. A vague reference
here usually means the claim was not actually checked.

**Derived from** is one of `report`, `code`, `report+code`, `README`. Be honest
about this. If most rows say `README`, you transcribed an existing list of known
issues instead of doing the analysis, and the reader needs to be able to see that
rather than discover it later.

**Rationale** is one or two sentences. For every `reject-*`, it has to carry the
specific evidence, not a general argument. "The tools already exist" is not
usable; "three tools are registered at agent.py:240 and the instruction string at
:246 makes their use optional" is.

### Table 2: defects the report could not see

Same columns, minus Verdict. These come from reading the code, not the report.

**There is almost always at least one.** A judge working from answers alone
structurally cannot observe how the agent is configured, only what it said. Those
are exactly the places where one constant produces a large and confusing variance
in scores. If Table 2 is empty, you have probably not finished Phase 1.

Walk this list explicitly rather than relying on what stood out while reading,
because these are easy to skim past. Each has been the real cause at least once:

- **Sampling parameters.** Is `temperature` set, or left at the provider default?
  An unpinned temperature makes identical questions take different paths, and it
  is the first thing to suspect when the same question scores very differently on
  different runs. No prompt rule can fix it.

  Record it, but **do not reflexively pin it to 0 in the same pass as a prompt
  rewrite.** Pinning does not remove a failure, it removes the chance of avoiding
  one: a question that used to fail intermittently now fails every single run, and
  the floor drops even as the variance improves. This is not hypothetical. On the
  run this brief was written from, pinning to 0 made the agent stop calling its
  retrieval tool on two of seven questions and answer "could you share your account
  ID?" instead, where at the provider default it had called the tool and answered
  correctly.

  If you pin it, spot-check the lowest-scoring questions by hand before committing:
  run one of them and confirm the agent still makes the tool calls it is supposed
  to. That is a single cheap invocation and it catches exactly this.
- **Retrieval width.** How many documents come back per query, against how many
  exist? If questions routinely span two topics and the search returns two
  results, the second topic gets answered from priors.
- **Tool wiring.** Which tools exist, and does anything actually require their
  use, or merely permit it?
- **Whether the tool's answer reaches the model.** A tool can be entirely correct
  and still deliver nothing. An async function called without `await` serialises
  to `{}`. A truncated return drops rows. A swallowed exception substitutes a
  default. None of these raise, and from the answers they are indistinguishable
  from an agent that simply does not know: it says it lacks the detail, which
  reads as a knowledge gap in the prompt.

  So check the path, not just the function. Call the tool directly, then look at
  what the call site actually hands back to the model, and confirm they match.

  This is not hypothetical. A triage missed exactly this, saw answers full of
  "I don't have those details", and responded by tightening the prompt to forbid
  unsourced figures. That was correct in itself and changed nothing, because the
  figures were never arriving. A prompt rule cannot fix a value that is not
  there.
- **Whether the model's answer reaches the judge.** The same failure one hop
  later, and it lives in the harness rather than the agent. A submit path that
  slices the answer to a character budget, sends a summary field instead of the
  full text, or submits an empty string when a case raises, produces evidence
  that looks exactly like an agent which rambles, omits its conclusion, or stops
  mid-thought. The judge then recommends teaching the agent to finish its
  sentences, and that recommendation is unactionable because the agent already
  does.

  Also not hypothetical. A harness capped submitted answers at 400 characters.
  All seven answers arrived cut off mid-word and missing the closing disclosure
  the rubric required, every score landed between 1 and 3, and the report's
  top recommendation was to add a completion check to the agent. The agent was
  already returning complete answers, roughly 800 characters, ending on a full
  stop.

  So read the submit path, not just the agent. Call the agent on one failing
  question, then compare what it returned against what the harness actually
  sends.
- **The instruction string versus the rubric.** Does it ask for behaviour the
  rejection criteria punish? Verbosity and eagerness are the usual offenders.
- **What the harness reports.** Does its output contain the numbers a
  before-and-after comparison needs, and does it echo local variables where it
  should echo what the server returned?

### A short prose section: what the report could and could not see

A paragraph. The useful content is the mechanism: which of the report's
conclusions follow from what it observed, and which follow from what it was
missing. This is the part a reader will use to calibrate how much to trust the
next report.

### Reconciliation, last

If the repo documents its own known issues (often a README table), **open it only
after Tables 1 and 2 are written.** Then add a "Reconciliation" section recording
both directions: things it lists that your tables did not reach, and rows in your
tables it does not list.

If your independent analysis matched, say so plainly. If it did not, say that
plainly too. Do not go back and edit Tables 1 and 2 to agree with it. The
divergence is the interesting part, and a reader who wanted the README could have
read the README.

### Calibration

Reports of this kind usually reduce to a handful of real changes, concentrated in
one or two places, most often the agent's instruction string plus one or two
configuration values. If your tables produce dozens of changes, you are
gold-plating and the re-run will not tell anyone anything. If they produce fewer
than two, go back to the judge evidence for the lowest-scoring questions.

## Phase 3: apply what survived

### First, move into a worktree

Do not edit the checkout you are standing in. Create a worktree and make every
change there:

```bash
mkdir -p .git/info
grep -qxF '.worktrees/' .git/info/exclude 2>/dev/null || echo '.worktrees/' >> .git/info/exclude
git worktree add .worktrees/eval-fix-<EVAL_ID> -b eval-fix/<EVAL_ID>
cd .worktrees/eval-fix-<EVAL_ID>
```

Three reasons, in order of how much they cost when ignored:

- **The checkout may be a running service.** On a hosted box the project
  directory is the deployed app. Editing an agent's prompt in place changes
  live behaviour the moment you save, before anyone has reviewed the diff.
- **It may be someone's working tree.** Locally, that directory is where a
  person has their own work in progress and their own branch checked out.
  Switching branches under them is rude and occasionally destructive.
- **Cleanup is one command.** If the triage goes wrong, `git worktree remove`
  erases it completely. An in-place branch has to be unpicked.

`.git/info/exclude` rather than `.gitignore` because it is per-clone and never
committed, so the worktree stays invisible to `git status` without you having to
modify a tracked file to hide your own scaffolding.

Some environments refuse writes anywhere inside `.git/`. If that step is blocked,
carry on: the worktree itself still works, and the only consequence is that
`.worktrees/` shows up as untracked in the parent. That is harmless as long as
you stage explicit paths and never `git add -A`, which this brief requires
anyway. Note it in the final block so the next reader knows why there is an
untracked directory.

If the worktree cannot be created (not a git repo, an ancient git, a filesystem
that will not take one), fall back to `git checkout -b eval-fix/<EVAL_ID>` in
place, and say so in the final block so nobody assumes the original tree was
left alone.

**Anything the next run needs must live in the worktree**, because the
evaluation runs there too. A virtualenv in the parent is reachable with a
symlink and needs no reinstall:

```bash
[ -d ../../.venv ] && [ ! -e .venv ] && ln -s ../../.venv .venv
```

### Freeze anything the comparison is keyed on

The point of this work is a v1-vs-v2 comparison on the same dataset. Anything
that defines the grading, or that the run records as a dimension of the subject
under test, has to be identical across the two runs, or the two numbers are not
comparable and the whole exercise produces nothing.

In practice, leave alone:

- **The grading surface.** The test questions, the expected results, the
  acceptance / rejection / evaluation criteria, any per-case judge guidance, and
  the number of runs per question. These define what "good" means. Changing them
  and then reporting a higher score is measuring a different thing.
- **The tool inventory.** Do not add or remove tools. Runs commonly record a tool
  count in their metadata, so a changed inventory confounds the comparison
  directly. If your triage genuinely concluded a tool is missing, record it in
  the mapping table as a follow-up for a *separate* run and say why you did not
  do it here.
- **The knowledge base contents.** Same reasoning. Adding or removing documents
  changes what the agent is able to know. This is not a licence to leave a wrong
  value in place: if a file in the knowledge base contradicts another file in the
  same repo that the README or the file itself calls authoritative, that is an
  internal inconsistency with a right answer, and correcting it is a fix like any
  other. Cite the authority in the mapping table. What you must not do is edit
  the authoritative source to match the wrong copy.
- **The model.** A different model is a different experiment.

Change freely: the agent's instruction string, the version tag, retrieval and
other configuration constants, and the reporting the harness prints at the end.

The freeze covers what the grading measures, not the plumbing that carries an
answer to it. If the harness mangles the answer on its way out, that is a bug
like any other and repairing it is in scope. Say so explicitly in the mapping
table, and add one line noting that the v1 number was produced by the broken
path, so part of the movement between the runs is the repair rather than the
agent. That is a real caveat on the comparison and the reader should not have to
work it out.

### Two specifics worth getting right

**Bump the version tag in code.** Most harnesses carry a prompt or config version
into the run metadata so the two runs are distinguishable in the dashboard. Find
it and increment it. If there is no such tag, add one.

**Change configuration in code, not by environment variable.** An environment-only
change is invisible in the diff, does not survive a fresh clone, and leaves the
next person unable to see why the score moved. Edit the default.

### When the code and the rubric disagree about a value

Sometimes the only way to satisfy the rubric is to change a number that a
deterministic tool returns or that the instructions state.

That is sometimes exactly right: the code's figure was stale and the rubric
records the real policy. It is sometimes exactly wrong: the code was right, the
rubric has a typo, and conforming to it makes the agent give a false answer to
real people while the score goes up.

**You usually cannot tell which, from inside the repo.** Both look identical: a
figure in the code, a different figure in the expected results, and judge
evidence marking the code's figure as an error. There is no third source to
break the tie, and the rubric is not evidence that the rubric is correct.

So apply the change if the rubric is the better bet, but do not present it as
fixing a bug, because you do not know that it is one. Mark the row
`RUBRIC-CONFORMING` in the mapping table, say in the rationale that you changed
the value **on the rubric's authority alone and could not verify it**, and repeat
it in the final block.

The person reading the checkpoint is the only one who can know whether the fee
really is 25 and not 15, and they will only go and check if you tell them there
is something to check. A row that reads "fixed the bug in the tool" gets waved
through; a row that reads "changed the tool to match the rubric, unverified"
gets looked at. The whole value of the checkpoint depends on that distinction.

This applies to values, not to behaviour. Making the agent call a tool it was
ignoring, or cite a doc id, needs no such flag: the rubric is authoritative about
what a good answer looks like. It is only authoritative about what is *true* if
someone has checked it.

### Rewriting the instruction string

Re-read the rubric from Phase 1 before you write, and write against it rather
than against the report.

The failure mode to watch for: reports of ungrounded answers very often recommend
that the agent hedge, express uncertainty, or defer to official documentation.
That is good advice when the agent genuinely cannot know the answer. It is
actively harmful when the agent has a tool that returns the answer and simply was
not told to call it, and it is worse than harmful when the rubric rejects
answers that dodge the question, which such rubrics commonly do. Check which
situation you are in before you write a single hedging instruction. Grounding and
hedging are not the same thing: "do not state a fact you did not retrieve" is
grounding, "say you might be wrong" is hedging, and the first is almost always
what was wanted.

Write the new instructions as direct, numbered operating rules. Every rule should
trace to a row in Table 1 or Table 2; a rule that traces to nothing is a rule you
invented, and it will be indistinguishable from the ones that were justified when
someone reads the diff. Keep it tight. An instruction string that is itself padded
tends to produce padded answers, which is often one of the things being fixed.

### Add a reporting tail to the harness

The harness probably prints little at the end. A comparison needs numbers. Add
prints for: the dataset id the run actually used, number of runs, average score,
min and max, spread, consistency, overall rating, instruction adherence, the run
id, and each of the new report's own recommendations.

Print the dataset id because it makes the log prove, by itself, that the run
scored against the intended dataset. That is the single most expensive thing to
get wrong here, and one line removes the doubt.

If the report object exposes no variance field, use max minus min as the spread
and label it as a substitute rather than presenting it as the same statistic.

Print the per-result ratings too, not only the report's summary statistics. On
this platform the two disagree, sometimes by several points, and the per-result
values are the ones that match what the answers actually scored.

The exact attribute names are in `server4agent-runtime.md`, alongside this file
in the skill's `references/` directory, under "Reading the evaluation SDK's
report object". Use those names rather than guessing: a wrong attribute name is
an exception thirty minutes into the next run, after the money has been spent.

**The method you want for the stored ratings is not on the object you first
reach for.** `client.evaluations` is a runner, and it exposes only the methods
for starting a run. `get_run` and `get_report` live on the client it wraps.
Reach for them like this, so the tail keeps working if the SDK later promotes
them:

```python
runner = client.evaluations
reports = runner if hasattr(runner, "get_run") else runner._client
run = reports.get_run(run_id)
# get_run returns a plain dict, not a model object, so read it by key:
stats = run["liveStatistics"]   # averageRating, minRating, maxRating, ratedCount
```

Two separate triages wrote `client.evaluations.get_run(...)` and both were
wrong. Neither crashed, because each had wrapped the lookup in a bare `except`
that fell back to the report's summary statistics. That is the worst possible
outcome: a full run's worth of spend, a report that looks complete, and the
numbers silently taken from the source this whole workflow exists to avoid.

So do not wrap the ratings lookup in a bare `except`. If it cannot read them,
the tail should say so loudly in the log. And verify the accessor before you
commit, which costs nothing and needs no run:

```bash
python3 -c "
from dotenv import load_dotenv; load_dotenv()
from agentx import AgentX
r = AgentX.from_env().evaluations
print('runner:', hasattr(r, 'get_run'), ' wrapped:', hasattr(r._client, 'get_run'))
"
```

`load_dotenv()` is not optional there. `AgentX.from_env()` raises
`AgentXAuthError` without the key, and a one-liner that omits it fails for a
reason that has nothing to do with the thing you were checking.

### Update the README

Add a short note recording that these gaps were closed and pointing at
`eval-analysis/mapping-<EVAL_ID>.md`. Leave any existing known-issues table in
place as the record of what the previous version was.

## Phase 4: verify, without running the evaluation

Do not run the evaluation in this run. That is the next brief's job, it costs
real money, and the dependencies are not installed yet.

Check instead that what you changed is syntactically valid and that the frozen
surfaces really are unchanged. For Python, parse rather than import, since
importing pulls in dependencies that are not present:

```bash
python3 -c "import ast; [ast.parse(open(f).read()) for f in ['<agent file>', '<harness file>']]; print('syntax ok')"
```

Then confirm the version tag moved, the configuration constants are what you
intended, and the tool count is what it was before you started. Print the
before-and-after of anything you claim to have changed, so the next reader does
not have to take your word for it.

## Phase 5: commit and push

You are already on `eval-fix/<EVAL_ID>` inside the worktree, so there is no
branch to create here.

```bash
git add <each path you changed> eval-analysis/
git status --porcelain
```

Read that `git status --porcelain` output before committing. If it shows anything
under a virtualenv directory, `__pycache__/`, `.env`, or `.server4agent/`,
unstage it. Use explicit paths rather than `git add -A`: a bootstrap step in the
next run creates a virtualenv in this directory, and an `-A` habit formed here is
how a few hundred megabytes end up in the history.

Commit with a message that says what was applied and what was rejected, since
that is the part a reviewer most wants and least wants to reconstruct:

```
v2: <n> changes from triaging evaluation <EVAL_ID> against the code

Applied: <one line per applied change>
Rejected: <one line per rejected recommendation, with the reason>

Mapping table: eval-analysis/mapping-<EVAL_ID>.md
```

Push with the run's git credential, taking owner and repo from
`git remote get-url origin`:

```bash
git push "https://x-access-token:$S4A_GIT_TOKEN@github.com/<owner>/<repo>.git" HEAD:eval-fix/<EVAL_ID>
```

The `x-access-token` username is required; the token alone as the username does
not authenticate. Never write the token into a file, a commit, a remote, or a
command you echo.

If the push is refused because the credential is read-only, that is a reportable
outcome and not a failure. The commit exists locally and is fully inspectable.
Say so in the final block and carry on, and skip the pull request below.

### Open a pull request

A pushed branch is easy to lose track of. A pull request puts the mapping table
in front of a reviewer with the diff attached, which is where this work wants to
end up.

Use whichever of these is available. `gh` is usually present on a developer
machine and usually absent on a hosted box, so try it and fall back:

```bash
gh pr create --base "$(git remote show origin | sed -n 's/.*HEAD branch: //p')" \
  --head "eval-fix/<EVAL_ID>" --title "..." --body-file <file>
```

Without `gh`, the same thing over the REST API, which needs only `curl` and the
token you just pushed with:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $S4A_GIT_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/<owner>/<repo>/pulls" \
  -d "$(python3 -c 'import json,sys; print(json.dumps({
        "title": sys.argv[1], "head": sys.argv[2], "base": sys.argv[3],
        "body": open(sys.argv[4]).read()}))' "<title>" "eval-fix/<EVAL_ID>" "<base>" <body file>)"
```

Build the JSON with a real serializer rather than string interpolation: the body
contains a markdown table full of quotes and backticks, and hand-built JSON will
break on it.

Title it after what was actually done, not after the eval id. The body should
lead with the applied and rejected counts, link the mapping table by path, and
carry any `RUBRIC-CONFORMING` rows near the top, since those are the only thing
the reviewer must check personally.

The response JSON has an `html_url`. Report it. If the call fails, report the
status and the branch name, and do not retry blindly: a 403 usually means the
credential has no pull-request scope, and a 422 usually means the branch has no
commits the base does not already have.

## Phase 6: the report

**Write the block below to `eval-analysis/triage-report-<EVAL_ID>.md` first, then
repeat it as the last thing in your final message.**

Both, not either. The final message is how a caller normally reads a run, but it
is also the single thing a run loses when it is cut short, and these runs are
cut short: the turn limit is 60 and a wall-clock ceiling sits behind it. A run
that analysed the repo, wrote the mapping table and committed a working fix, then
died one turn before reporting, looks from the outside exactly like a run that
did nothing. Writing the report to disk first means the work can always be found,
because the file survives whatever happens to the message.

If you can feel the budget running short, stop analysing and write the report
now, with whatever `status` is true. A partial report that names what was done is
worth more than a complete analysis nobody can see.

Fill it in exactly:

```
TRIAGE COMPLETE
status:         complete | partial (<what is missing and why>)
eval_id:        <EVAL_ID>
mapping_file:   eval-analysis/mapping-<EVAL_ID>.md
branch:         eval-fix/<EVAL_ID>
worktree:       .worktrees/eval-fix-<EVAL_ID> | none (edited in place, <reason>)
commit:         <short sha>
pushed:         yes | no (<reason>)
pr_url:         <html_url> | none (<reason>)
applied:        <n> recommendations
rejected:       <n> recommendations
code_only:      <n> defects the report could not see
version_tag:    <old> -> <new>
config_changed: <name>: <old> -> <new>, ... | none
tool_count:     <n> (unchanged)
rubric_conforming: <values changed on the rubric's authority alone, unverified> | none
files_changed:  <comma-separated list>
uncertainties:  <anything you had to decide without enough information, or none>
next:           run the evaluation brief
report_file:    eval-analysis/triage-report-<EVAL_ID>.md
```
