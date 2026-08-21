# agentx-eval-fix

A Claude Code skill that turns an AgentX evaluation into a triaged code fix, then
re-runs it on the same dataset so the before and after numbers mean something
next to each other.

```
evaluation ──► triage against the real source ──► mapping table ──► you approve
                                                                       │
               v1 vs v2 comparison ◄── re-run on same dataset ◄── fixes on a branch
```

Built for [AgentX self-host](https://github.com/AgentX-ai/AgentX-trace-eval) —
the Trace/Evaluate/Monitor engine you run yourself, `http://localhost:4700` by
default and anywhere you like via [`HOST`](#pointing-it-at-another-engine).

## The problem it solves

An AgentX analysis is written by a judge that saw your agent's **answers** and
nothing else. Not the source, not the tool definitions, not the retrieval config.
So it describes symptoms accurately and prescribes badly, and you are left with a
document that says what was wrong with the output but not what to change.

A real case. An agent scored **1.86 out of 10**, every answer cut off mid-word.
The judge's top recommendation:

> Introduce and enforce a completion check that discourages truncation and
> requires the agent to finish its explanations.

Reasonable, and useless. The agent was already producing complete 800-character
answers ending on a full stop. The evaluation harness had this line:

```python
"output": out["text"][:MAX_OUTPUT_CHARS],   # MAX_OUTPUT_CHARS = 400
```

One line, in a file the report never mentions, in a component the judge cannot
see. Four of that report's five recommendations were downstream of it. Deleting
the slice took the agent to 10.00.

**That is the gap this closes.** It treats the report's evidence as reliable and
its recommendations as hypotheses, then checks each one against the code.

## How it works

**1. Reads the evaluation off your engine.** Standard library only — no SDK, no
virtualenv, no `.env`. It stitches together the stored per-result ratings, the
dataset's expected results and judge guidelines, and the multi-judge agreement
data into one export, and works out which grading config actually graded the run.

```bash
python3 scripts/fetch_analysis.py --list
python3 scripts/fetch_analysis.py <evaluation_id> --write-export eval-analysis/exports/
```

**2. Triages every recommendation against the source.** Each gets one of five
verdicts, and every rejection must cite the `file:line` or the rubric string that
disproves it. An assertion without evidence is not a verdict.

| Verdict | Meaning |
|---|---|
| `apply` | Right, as written |
| `apply-modified` | Real problem, wrong remedy — keep the observation, change the fix |
| `reject-wrong-premise` | Assumes something untrue about the code |
| `reject-already-done` | Asks for something that already exists |
| `reject-harmful` | Following it would lower the score |

**3. Writes down what the report could not see.** A second table for defects
found by reading the code: retrieval width, tool wiring, sampling parameters,
whether a tool's output ever reaches the model, whether the model's answer
reaches the judge. There is almost always something here, and it is usually the
thing that actually mattered.

**4. Fixes on a branch, in a worktree.** Your checkout is never touched. Changes
land in `.worktrees/eval-fix-<id>` on `eval-fix/<id>`.

**5. Re-runs and compares.** Same dataset, same criteria, same model — anything
the comparison is keyed on stays frozen, or the second number means nothing.

## Install

As a plugin, which is one command and gets updates:

```bash
claude plugin marketplace add AgentX-ai/AgentX-Eval-Skill
claude plugin install agentx-eval-fix@agentx
```

The same two steps work from `/plugin` inside a Claude Code session. Later,
`claude plugin marketplace update agentx` pulls new versions.

**Restart Claude Code afterwards.** Slash commands are loaded at startup, so
`/eval-fix` does not exist in the session you installed from.

Or copy the skill straight into your skills directory, if you would rather read
the files than install anything:

```bash
git clone https://github.com/AgentX-ai/AgentX-Eval-Skill.git /tmp/agentx-eval-skill
cp -r /tmp/agentx-eval-skill/plugins/agentx-eval-fix/skills/agentx-eval-fix \
  ~/.claude/skills/
```

Per-project instead of per-user works too — copy it to
`<your-repo>/.claude/skills/` and keep it out of the repo under test, since the
repo is the thing being measured and tooling in its `git status` ends up in the
diff a reviewer reads:

```bash
echo '.claude/skills/agentx-eval-fix/' >> <your-repo>/.git/info/exclude
```

## Run it

From inside the repo that holds your agent — the triage reads that source:

```
/eval-fix oE1YMG5wqmu4j2bhTtw1X
```

The evaluation id is the whole input. Copy it from the run's card in the
dashboard's Evaluate tab; everything else is discoverable from it, since the run
carries its own dataset, grading config, subject metadata and per-result ratings,
and the script resolves the engine and key on its own. Anything after the id is
passed through as extra instruction:

```
/eval-fix oE1YMG5wqmu4j2bhTtw1X focus on the pricing question, it regressed
```

It fetches the evaluation, reads the source, writes the mapping table, applies
what survived on a branch, and stops. Review `eval-analysis/mapping-<id>.md`,
then approve the re-run. You get `eval-analysis/v2-report-<id>.md` with the
before and after side by side.

Without the plugin installed, the same thing in words still works — name the
skill and the brief, and give it the id.

### Pointing it at another engine

The engine is assumed local, so there is nothing to configure until it is not. When it
is somewhere else — a box on your network, a container, an engine the team shares — you
say so in the same sentence as the id:

```
/eval-fix oE1YMG5wqmu4j2bhTtw1X our engine is at http://10.0.0.5:4700
```

That is the whole mechanism. No file to edit, no variable to export, no restart. The
address is read out of what you typed and passed to every command that needs it, and
each run prints which engine answered so you can see it took.

**Plain `http://` is fine, and internal addresses are the expected case.** An address
with no scheme is completed from the local default's shape, so `10.0.0.5` means
`http://10.0.0.5:4700` and `agentx.internal:4700` means `http://agentx.internal:4700`.
A scheme you supply is left alone: `https://evals.example.com` stays on 443, as a
reverse proxy needs, and a path prefix survives for an engine mounted under one. (The
project key rides along in a header, so on an untrusted network you want the `https://`
form, same as any other API.)

**If you say nothing and localhost is not it, you get asked.** Anything that means "the
wrong box" — nothing listening, no key that works, a 404 on an id you know exists —
stops and asks where the engine is, quoting the address it tried. Answer with the
address; that is the whole interaction.

Two things worth knowing once the engine is remote:

- **Keys are per engine as well as per project.** A remote engine needs a key minted by
  *that* engine — the local `~/.agentx/config.json` is the wrong file, which the script
  verifies rather than failing later as a confusing 404. Paste yours from the engine's
  startup output or dashboard when asked.
- **v1 and v2 have to be scored by the same engine**, or the before-and-after is not a
  comparison. The address is written into the mapping table alongside the dataset id for
  exactly that reason, and the re-run is launched against it explicitly.

For a permanent default — a team where the engine is never local — the environment
still works, and is picked up with nothing typed at all:

```bash
export AGENTX_HOST=https://evals.example.com     # or 10.0.0.5:4700
export AGENTX_API_KEY=<that engine's project key>
```

First one set wins, most specific first: `--base-url` → `--host` → `$AGENTX_API_BASE_URL`
→ `$AGENTX_HOST` → `$HOST` → `http://localhost:4700`. A bare `HOST` counts only when it
carries a scheme, since `HOST=0.0.0.0` is what dev servers and container images set for
their own listener and it lands in the environment of everything beside them.

### The analysis is the input, and it is a spend

The recommendations this skill triages come from analysing the evaluation, so
`--analyze` is the normal path when a run has not been analysed yet. It is also
the one step that spends judge calls, which is why the skill asks first rather
than running it unprompted. If you decline, the triage still works on a reduced
input: the
per-result ratings, the rubric, each answer's judge justification, the similarity
metrics and any code-scorer output are all on the run the moment it finishes, and
they are the reliable half. The numbered recommendations are the code-blind half
this skill exists to be sceptical of. With no analysis present, Table 1 is empty
by fact rather than omission and Table 2 becomes the deliverable — which is where
the useful findings usually were anyway.

## Reviewing the mapping table

The stop is deliberate: everything before it is free, the re-run is not. Worth
thirty seconds:

- **Every recommendation has a verdict.** A skipped row is an unexamined claim.
- **Rejections cite a file and line**, rather than asserting.
- **The second table isn't empty.** That usually means the code was skimmed.
- **No tools were added, and no hedging language.** Both are common wrong turns,
  and adding tools breaks the comparison — runs record a tool count.
- **`RUBRIC-CONFORMING` rows.** These are the only ones you must settle
  personally. They mark a value changed purely because the expected results
  disagreed with the code, where nothing in the repo could say which is right. If
  the rubric has a typo, the change makes your agent confidently wrong to real
  users, the score goes *up* anyway, and nothing downstream will catch it.

## Requirements

A running self-host engine — local, or anywhere `HOST` points — Python 3.9+, and git. `scripts/fetch_analysis.py`
needs nothing beyond the standard library; `scripts/bootstrap.sh` builds a
virtualenv for the repo under test when it needs one.

The engine needs a provider key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or
`GEMINI_API_KEY`, or the dashboard's Platform Settings) to score anything.
Without one a run still reports that it finished, and every result is stored with
rating 0 and the reason in its justification — check the ratings, not the exit
code.

Project API keys are **copy-pasted, not fetched**: the engine deliberately
removed its old unauthenticated `/dev/bootstrap` handout. `fetch_analysis.py`
reads `$AGENTX_API_KEY`, then `~/.agentx/config.json` — which it verifies against
the engine before using, because that file records whichever engine last ran on
the machine and not necessarily the one you are talking to. When no key works it
says so, and says where to get one, instead of failing later as a 404 that looks
like a bad evaluation id.

## What's in here

Everything below is under `plugins/agentx-eval-fix/`.

| Path | What it is |
|---|---|
| `commands/eval-fix.md` | The `/eval-fix <id>` slash command — the normal entry point |
| `skills/agentx-eval-fix/SKILL.md` | Connecting to the engine, and where the analysis comes from |
| `skills/agentx-eval-fix/references/triage-brief.md` | The core artifact. Six phases, executed in order |
| `skills/agentx-eval-fix/references/eval-brief.md` | The re-run: guardrails, launch, analysis, comparison |
| `skills/agentx-eval-fix/scripts/fetch_analysis.py` | Evaluation, rubric and judge evidence by id, over plain HTTP |
| `skills/agentx-eval-fix/scripts/bootstrap.sh` | Virtualenv setup for the repo under test |

The nesting is what the plugin format expects: `.claude-plugin/marketplace.json`
at the repo root declares the marketplace, and each plugin keeps its skills under
`<plugin>/skills/<name>/`.

Validated end to end on nine agents with deliberately planted defects, spanning
levers in code, in a YAML config, in a data file, and in the evaluation harness
itself; then on three LangChain agents scoring 3.60, 5.50 and 4.80, triaged to
10.00, 9.60 and 9.80 on the same datasets.

Most recently against a LangChain/LangGraph support agent on a self-host engine:
**6.25 → 9.53** average, minimum **1 → 7**, rating variance **5.78 → 0.98**, on
the same dataset with criteria, judge and model frozen. Of seven
recommendations, three were applied, two applied with changed scope, one rejected
as already implemented and one as harmful against the dataset's own rejection
criteria. The single change that moved the score most — retrieval width — came
from reading the code and appeared in no recommendation at all.
