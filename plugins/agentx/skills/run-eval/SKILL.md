---
name: run-eval
argument-hint: "[dataset-id] [evaluator-id] [extra instructions]"
description: >-
  Run an AgentX self-host evaluation of the local agent: pick or create the dataset and
  grading config (templates, an existing id, a CSV/file of Q&A, or cases curated from the
  agent's own live traces), write the harness that calls the agent per case with a linked
  trace, execute the run, and hand back the score, the browser report, and the /eval-fix
  command. Use whenever someone wants to evaluate their agent on a self-host engine
  (AgentX-trace-eval, normally http://localhost:4700), asks to "run an eval", "score my
  agent", "create a dataset", "test my agent against a dataset", or has traced runs and
  wants to know how good they are. The deliverable is a committed eval harness plus a
  finished, analyzed run - the exact thing /eval-fix picks up.
---

# Run an evaluation of the agent in this repo

Tracing tells you what the agent did. An evaluation tells you how good that was, case by
case, against criteria someone chose - and leaves behind an id that `/eval-fix` can turn
into a code change. This skill takes a repo (usually one the `instrument` skill already
set up) from "traced" to "scored":

1. **A dataset and a grading config** - given as ids, or picked, or created.
2. **A committed harness** - `eval/run_eval.py`, the reproducible definition of this
   evaluation. `/eval-fix`'s re-run executes this exact file, which is what makes a
   v1-vs-v2 comparison mean something.
3. **A finished run** - executed, finalized, analyzed; every result linked to its trace.
4. **The handoff** - the score, the report's browser URL, and `/eval-fix <run-id>`.

The place of this skill in the loop:

```
/instrument ──► traced runs ──► /run-eval ──► score + analysis ──► /eval-fix ──► v1 vs v2 ──► PR
```

**Self-host only.** Everything here targets the engine from AgentX-trace-eval. Its API
dialect differs from the hosted platform's, and ids never travel between engines.

**Python only, same as the sibling skills.** The harness runs on `agentx-python`; a repo
with no Python cannot be evaluated this way - say so rather than improvising.

---

## The workflow

Carry this out yourself, in order. Do not delegate it to a subagent. Everything after the
argument words is extra instruction. The detailed phases live in
`references/run-brief.md`; follow that brief exactly once the ids are settled. `<skill>`
is this skill's own directory - resolve it once.

**1. Engine, once.** Same question, same three options, and same skip rules as the
instrument skill: local `http://localhost:4700` / hosted is not supported here - name it
plainly if the user asks / other address. Skip the question when `.env.agentx` already
names an engine or the user named one. `.env.agentx` is also where the key comes from;
if it is absent, the instrument skill's `agentx_key.py` writes it - run that first.

**2. Resolve the two ids.** `$1` is a dataset id, `$2` an evaluation-settings id.

| Given | Do |
|---|---|
| both | Validate each (below), then go to the brief. No picker questions - the brief's Phase 3 still asks for the go-ahead. |
| dataset only | The dataset's own grading config grades it - that is the default, not a degraded mode. One line saying so, then the brief. |
| neither | The two pickers below - one AskUserQuestion each, never more. |
| an id that fails validation | Say which engine and project were checked, then fall into the picker with the failure stated. A wrong-engine or wrong-project id fails exactly like a typo, and this is where that surfaces. |

Validate with:

```bash
python3 <skill>/scripts/pick_eval.py --validate-dataset <id>
python3 <skill>/scripts/pick_eval.py --validate-settings <id>
```

**3. The dataset picker** (no `$1`). One call builds the options:

```bash
python3 <skill>/scripts/pick_eval.py --json
```

Ask with AskUserQuestion, options in this order:

- **A template** - `make_dataset.py --list-templates` names them: customer-support,
  rag-grounding, tool-use. Pick the one matching what the repo's agent actually is
  (Phase 0 of the brief tells you), and say in the option description that creation
  writes a **permanent row - the API has no dataset DELETE** - and that creation is
  idempotent by name, so re-picking a template never duplicates it.
- **An existing dataset** - the top few from the listing, name + case count; "Other"
  takes a typed id.
- **From the agent's live traces** - only offer when the project has traces (the repo
  was instrumented). The engine drafts each case from real traffic:
  `--preview-trace <id>` / `--preview-session <id>`, then `--suggest-expected` for the
  reference answer, then show the drafted case to the user and only `--add-case` what
  they approve. Real questions the agent actually received - the strongest dataset an
  agent can be graded on.
- **A dataset with follow-up questions still runs**, but the SDK's execute path asks main
  questions only - `make_dataset.py` says so at creation time when a payload carries
  follow-ups, and the run's rated count will reflect main questions, not the follow-ups.
- **From the user's material** - Q&A pairs typed in chat, or a CSV/XLSX/document. Convert
  whatever is given to `query`/`expected_results`, show the parsed cases for approval,
  then `--from-csv` or `--from-json`. You do the file conversion yourself; only CSV and
  builder-shaped JSON reach the script.

**4. The evaluator picker** (no `$2`). Options in this order:

- **The dataset's own config** *(default)* - every dataset carries its own criteria; the
  run simply omits the settings id. Recommended unless the user has a reason to grade
  differently.
- **An existing config** - from the same `--json` listing. Every project ships built-in
  judges (Session Baseline, the RAG family), so this list is never empty.
- **Create a simple one** - `make_dataset.py --create-settings <name> --acceptance "..."
  --rejection "..."`, idempotent by name, same permanence warning. For a no-decisions
  default, recreate the engine's starter "Example: Helpfulness Judge" criteria (seeding
  is default-project-only, so fresh projects lack it).

When the dataset answer was "create new", this question may be multiSelect: each selected
config becomes its own run over the same dataset - that is exactly what standalone
evaluation settings exist for.

**5. Execute `references/run-brief.md`.** Phases 0-6: orient in the repo, write the
harness, preflight, run, verify, hand off. The brief is the contract; the two traps that
ruin runs silently - a second tracer instance, and shelling out to the CLI - are
explained there, not here.

---

## The helpers

```bash
python3 <skill>/scripts/pick_eval.py --json            # datasets + settings, for the pickers
python3 <skill>/scripts/pick_eval.py --validate-dataset <id>
python3 <skill>/scripts/make_dataset.py --list-templates
python3 <skill>/scripts/make_dataset.py --template <name> [--dry-run]
python3 <skill>/scripts/make_dataset.py --from-csv <path> --name <name>
python3 <skill>/scripts/make_dataset.py --from-json <path>
python3 <skill>/scripts/make_dataset.py --preview-trace <traceId>
python3 <skill>/scripts/make_dataset.py --suggest-expected --query "..."
python3 <skill>/scripts/make_dataset.py --add-case <datasetId> --query "..." --trace-id <t>
python3 <skill>/scripts/make_dataset.py --create-settings <name> --acceptance "..." --rejection "..."
```

Both scripts are stdlib-only, read `.env.agentx` themselves (a real environment variable
wins), and never print anything derived from a key. Every creating call states on stderr
that the row is permanent; `--dry-run` prints the payload and writes nothing.

---

## What this skill does not do

- **Fix the agent.** That is `/eval-fix <run-id>` - it triages the analysis against the
  source, applies what survives, re-runs this same harness, and offers the PR. Run-eval
  ends at the handoff line.
- **Re-run for comparison.** Also `/eval-fix` - a re-run only means something next to a
  triaged change.
- **Score hosted-platform agents.** Different dialect, different ids.
- **Invent an evaluation the user did not ask for.** A run costs real judge and agent
  calls, so the brief's Phase 3 puts one AskUserQuestion - run it now, or later with the
  command that runs it - between the written harness and any spend.
