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

The skill is a plain `SKILL.md` folder, so it runs in any agent that loads the
Agent Skills standard. Only the directory each one scans is different.

### Claude Code

As a plugin, which is one command, carries the `/eval-fix` command, and gets updates:

```bash
claude plugin marketplace add AgentX-ai/AgentX-Eval-Skill
claude plugin install agentx-eval-fix@agentx
```

The same two steps work from `/plugin` inside a Claude Code session. Later,
`claude plugin marketplace update agentx` pulls new versions.

**Restart Claude Code afterwards.** Slash commands are loaded at startup, so
`/eval-fix` does not exist in the session you installed from.

### Cursor

This repo is a Cursor marketplace as well. On a team plan, open **Dashboard →
Plugins → Team Marketplaces → Add Marketplace → Import from Repo** and paste this
repo's URL; **Enable Auto Refresh** keeps it current as the skill changes.
Without a team marketplace, use the directory install below — Cursor reads
`.agents/skills/` too.

### Codex, Antigravity, VS Code

`.agents/skills/` is the one path Codex, Cursor, Antigravity and Copilot all
read, so a single copy serves every one of them:

```bash
mkdir -p .agents/skills && curl -fsSL https://github.com/AgentX-ai/AgentX-Eval-Skill/archive/main.tar.gz \
  | tar -xz --strip-components=4 -C .agents/skills \
    AgentX-Eval-Skill-main/plugins/agentx-eval-fix/skills/agentx-eval-fix
```

Swap `.agents/skills` for `~/.agents/skills` to install once for every repo
instead of one — except Antigravity, whose user-level directory is
`~/.gemini/config/skills/`. Claude Code reads `~/.claude/skills/` and
`<repo>/.claude/skills/`.

Either way, keep it out of the repo under test: that repo is the thing being
measured, and tooling in its `git status` ends up in the diff a reviewer reads.

```bash
echo '.agents/skills/agentx-eval-fix/' >> <your-repo>/.git/info/exclude
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

**The first thing it asks is where your engine is** — one question, once: local, or
an address you type. Take the default and it reads `http://localhost:4700`. It is
asked before anything is spent because that address decides which database every
number comes from, and a run read from the wrong engine wastes the whole workflow.
Naming a different address later switches everything after it.

Then it fetches the evaluation, reads the source, writes the mapping table, applies
what survived on a branch, and stops. Review `eval-analysis/mapping-<id>.md`,
then approve the re-run. You get `eval-analysis/v2-report-<id>.md` with the
before and after side by side.

Without the plugin installed, the same thing in words still works — name the
skill and the brief, and give it the id.

### Pointing it at another engine

You are asked, so there is nothing to look up. Three choices, two predefined:

```
Where is your AgentX engine?
  ▸ local    http://localhost:4700     (default)
  ▸ agentx   https://api.agentx.so
  ▸ other    → http://10.0.0.5:4700
```

**local** is the self-host engine on this machine. **agentx** is the hosted platform —
worth knowing that it speaks a different dialect than self-host, so see the note below.
**other** is anything else reachable, typed in.

You can also answer it before it is asked, in the same sentence as the id, and the
question is skipped:

```
/eval-fix oE1YMG5wqmu4j2bhTtw1X our engine is at http://10.0.0.5:4700
```

Either way there is no file to edit, no variable to export and no restart. The
address is passed to every command that needs it, each run prints which engine
answered, and it is written into the mapping table so the re-run reproduces it.
Naming a different address at any point switches everything after it.

**Plain `http://` is fine, and internal addresses are the expected case.** An address
with no scheme is completed from the local default's shape, so `10.0.0.5` means
`http://10.0.0.5:4700` and `agentx.internal:4700` means `http://agentx.internal:4700`.
A scheme you supply is left alone: `https://evals.example.com` stays on 443, as a
reverse proxy needs, and a path prefix survives for an engine mounted under one. (The
project key rides along in a header, so on an untrusted network you want the `https://`
form, same as any other API.)

**If the answer turns out to be wrong, you get asked again.** Anything meaning "wrong
box" — nothing listening, no key that works, a 404 on an id you know exists — comes
back to you with the address it tried, rather than reporting your engine as down.
The 404 case names the other possibility too: right engine, wrong project key, since
a run is invisible to every key but its own.

Two things worth knowing once the engine is remote:

- **Keys are per engine as well as per project.** A remote engine needs a key minted by
  *that* engine — the local `~/.agentx/config.json` is the wrong file, which the script
  verifies rather than failing later as a confusing 404. Paste yours from the engine's
  startup output or dashboard when asked.
- **v1 and v2 have to be scored by the same engine**, or the before-and-after is not a
  comparison. The address is written into the mapping table alongside the dataset id for
  exactly that reason, and the re-run is launched against it explicitly.

**About `agentx`.** This skill is built against self-host's API, and the hosted platform
is a different dialect: its evaluation ids are 24-character hex where self-host's are
nanoids, and the analysis read here lives on self-host's dashboard router. It is
selectable because that is where some evaluations live, and the tooling knows which kind
of address it is on — hex ids are accepted only there, a nanoid used there is flagged
before anything is spent, and a missing route says so rather than returning a bare 404.
What it is not is a drop-in equivalent of a self-host engine.

For a permanent default — a team where the engine is never local — the environment
still works, and is picked up with nothing typed at all:

```bash
export AGENTX_HOST=https://evals.example.com     # or 10.0.0.5:4700, local, agentx
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

Project API keys resolve in order of how specific the intent behind them is:
`--api-key`, then `$AGENTX_API_KEY`, then `~/.agentx/config.json` — verified
against the engine before use, because that file records whichever engine last
ran on the machine and not necessarily the one you are talking to — and finally
the engine's own handout.

That handout is `GET /api/v1/auth/config`: unauthenticated, present in both auth
modes, and under the default `AGENTX_AUTH=disabled` it returns the default
project's key outright, so a cold start needs nothing exported and nothing
pasted. Under `AGENTX_AUTH=enabled` it returns no key, and the hosted platform
has no such route — there the key comes from the engine's startup output or the
dashboard. It is tried last on purpose: it is always the **default** project.
(`GET /dev/bootstrap` was its predecessor and was removed, with a test asserting
it 404s.)

When no key works, the script says so and says where to get one for *that*
engine, instead of failing later as a 404 that looks like a bad evaluation id.

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

The nesting is what both plugin formats expect: a marketplace manifest at the repo
root declares the marketplace, and each plugin keeps its skills under
`<plugin>/skills/<name>/`. Claude Code and Cursor use the same layout and differ
only in the manifest directory — `.claude-plugin/` and `.cursor-plugin/` — so the
skill, its references and its scripts are one copy serving both.

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
