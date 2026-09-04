# AgentX for coding agents

One plugin, four commands, one loop: get an agent's real runs into
[AgentX](https://github.com/AgentX-ai/AgentX-trace-eval) — the Trace/Evaluate/Monitor engine you
run yourself — then turn what they measure into a code fix.

| Skill | What it does |
|---|---|
| `/instrument` | Sets a Python agent up on AgentX: key, SDK, one span where the run begins, then traces sent and read back to prove it |
| `/run-eval` | Evaluates the agent against a dataset it can also create — templates, a CSV/document, or cases curated from live traces — and leaves the committed harness the re-run needs |
| `/eval-fix <id>` | Triages an evaluation against the real source, applies what survives, re-runs it on the same dataset |
| `/auto-improve <id>` | Applies an improvement report — production failures a human confirmed in signal review, clustered into issues — as triaged fixes to the source. The online counterpart of `/eval-fix` |

```
/instrument ──► traced runs ──► /run-eval ──► score + analysis ──► /eval-fix ──► v1 vs v2
                     │
                     └──► live traffic ──► signals confirmed in review ──► /auto-improve ──► fix
```

They are one plugin because they are one story. An evaluation result carrying a `traceId` is
judged against the agent's **real execution path**; one without it is judged on answer text
alone — which is how a judge concludes an agent has no retrieval when it plainly does.

## Why each exists

**Tracing looks like a five-minute job and fails silently.** Delivery is fire-and-forget by
design — tracing must never break the agent it watches — so an unset `AGENTX_API_BASE_URL` sends
a self-host user's traces to the hosted platform and a key from the wrong project makes them
invisible. Both look identical from outside: an agent that runs fine, a dashboard that stays
empty. The opposite failure is worse — `AgentX.from_env()` *raises* when the key is missing, at
import time, so a missing secret in CI becomes a crash in your application.

**An evaluation report is written by a judge that never saw your code.** It describes symptoms
accurately and prescribes badly. A real case: an agent scored **1.86/10**, every answer cut off
mid-word, and the top recommendation was to "enforce a completion check". The agent was already
producing complete answers. The harness had this line:

```python
"output": out["text"][:MAX_OUTPUT_CHARS],   # MAX_OUTPUT_CHARS = 400
```

One line, in a file the report never mentions, in a component the judge cannot see. Four of its
five recommendations were downstream of it. Deleting the slice took the agent to 10.00.

So `/eval-fix` treats the report's **evidence as reliable and its recommendations as
hypotheses**, and checks each against the source. Every rejection cites a `file:line`.

## Install

```bash
claude plugin marketplace add AgentX-ai/AgentX-Eval-Skill
claude plugin install agentx@agentx
```

**Restart Claude Code afterwards** — slash commands load at startup.

> **Coming from the two-plugin layout?** `agentx-eval-fix` and `agentx-tracing` are now one
> plugin. Uninstall both, then `claude plugin marketplace update agentx && claude plugin install
> agentx@agentx`.

### Cursor

**Dashboard → Plugins → Team Marketplaces → Add Marketplace → Import from Repo**, and paste this
repo's URL. Enable **Auto Refresh** to keep it current. The plugin's skills are discovered from
`skills/` automatically.

### Codex / ChatGPT

```bash
codex plugin marketplace add AgentX-ai/AgentX-Eval-Skill
```

Then restart the app and install **AgentX** from the Plugins Directory's marketplace picker.

### Anything else

The skills are plain `SKILL.md` folders, so they install into any agent that reads the
[Agent Skills standard](https://agentskills.io) — Codex, Cursor, Copilot, Gemini CLI, Windsurf,
Zed and the rest:

```bash
npx skills add AgentX-ai/AgentX-Eval-Skill
```

One repo serves all four paths. Each ecosystem reads its own manifest directory —
`.claude-plugin/`, `.cursor-plugin/`, `.codex-plugin/` — over **one copy** of the skills,
their references and their scripts.

## Run it

From inside the repo that holds your agent — every command reads that source.

```
/instrument
```

Asks at most three questions: which engine, which entry point (only if several are plausible),
and which project the traces go under (only if the engine can list them). Then it surveys the
repo, writes `.env.agentx`, instruments the entry point, and proves it by sending a trace and
fetching it back by id. It does **not** decorate every function — nesting is automatic, so one
span at the entry point plus one line of framework auto-instrumentation is the whole job.

```
/run-eval
/run-eval <dataset-id> [evaluator-id]
```

With no ids it asks two questions, one for the dataset and one for the grading config, and no
more. A dataset can be one of the shipped templates, an existing id, a CSV or document of Q&A
pairs, or cases the engine drafts from the agent's own live traces. It then writes
`eval/run_eval.py` — the committed harness that calls the agent once per case with a linked
trace — and asks before spending anything, because a run costs real judge and agent calls. It
ends with the score, the report's browser URL, and the `/eval-fix` command to paste next.

```
/eval-fix oE1YMG5wqmu4j2bhTtw1X
```

The evaluation id is the whole input; copy it from the run's card in the Evaluate tab. Anything
after it is passed through as extra instruction. It fetches the run, triages every
recommendation against the code, applies what survived on a branch in a worktree, and **stops**
at `eval-analysis/mapping-<id>.md`. Everything to that point is free; the re-run is not, so it
asks first.

```
/auto-improve Xq3f9kLm2
```

The report id is the whole input; copy it from **Insights → Auto-improve** in the dashboard. An
improvement report is the online version of an evaluation analysis: failures that online
scorers flagged on production traffic and a reviewer **confirmed** by hand, clustered into
issues with recommendations. The evidence is the strongest the engine has, and the
recommendations were still written without seeing the code, so the skill works the report
issue by issue with the same three verdicts `/eval-fix` uses — apply, already handled, reject —
and delivers a triage table citing `file:line`. Verification is live: the same scorers keep
running, and a fixed failure stops re-raising its signal. For a pre-deploy check, curate the
confirmed failures into a dataset and hand it to `/run-eval`.

All four open with one question about which engine to use — `http://localhost:4700` by default —
because that address decides which database every number comes from, and it is invisible until
something fails. They skip the question when `.env.agentx` already names one.

### Before you approve the re-run

- **Every recommendation has a verdict.** A skipped row is an unexamined claim.
- **Rejections cite a file and line**, rather than asserting.
- **The second table isn't empty** — that table is for defects found by reading code the judge
  could not see, and an empty one usually means the code was skimmed.
- **`RUBRIC-CONFORMING` rows are yours to settle.** They mark a value changed purely because the
  expected results disagreed with the code. If the rubric has the typo, the change makes your
  agent confidently wrong, the score goes *up* anyway, and nothing downstream catches it.

## Requirements

Python 3.9+, git, and a reachable AgentX engine — self-host on `http://localhost:4700` by
default, or the hosted platform. The scripts need nothing beyond the standard library; the repo
being traced needs `agentx-python` from PyPI, which the skill installs with whichever extra its
framework calls for.

Keys are per project, and the key **is** the project selector — a trace or an evaluation is
visible to its own project's key and to no other. On self-host in its default auth mode the
engine hands out its default project's key at `GET /api/v1/auth/config`, so a cold start needs
nothing exported and nothing pasted; otherwise it comes from the engine's startup log or the
dashboard. Every candidate is verified against the engine before use, and no key is ever
printed.

To score anything, the engine needs a provider key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or
`GEMINI_API_KEY`, or the dashboard's Platform Settings). Without one a run still reports that it
finished and every result is stored with rating 0 — check the ratings, not the exit code.

## What's in here

The plugin is `plugins/agentx/`, and every skill under it has the same shape: a `SKILL.md`
that is the command, `references/` briefs it executes in order, and stdlib-only `scripts/`
that talk to the engine. Paths below are relative to `plugins/agentx/skills/`.

| Path | What it is |
|---|---|
| `instrument/SKILL.md` | The `/instrument` command: what to trace, what not to, and the three silent failures |
| `instrument/references/instrumentation-brief.md` | The core artifact — eight phases, from survey to a trace fetched back |
| `instrument/references/preflight-brief.md` | The two checks before any SDK code is written: git state, and which project the key selects |
| `instrument/scripts/detect_stack.py` | Framework, entry points and existing instrumentation, found with `ast` — never imports the repo |
| `instrument/scripts/agentx_key.py` | Verified key resolution and project selection, written to `.env.agentx` at mode 0600 |
| `instrument/scripts/verify_trace.py` | Proves the wiring without writing anything; `--check` grades the agent's own runs, `--capabilities` reports what the installed SDK supports |
| `run-eval/SKILL.md` | The `/run-eval` command: id resolution, the dataset and evaluator pickers, and what it refuses to do |
| `run-eval/references/run-brief.md` | Seven phases: orient, validate, harness, preflight, run, verify, hand off |
| `run-eval/scripts/pick_eval.py` | Datasets and grading configs listed, and ids validated against this engine and project |
| `run-eval/scripts/make_dataset.py` | Datasets created four ways — templates, JSON, CSV, or curated from live traces — idempotent by name |
| `run-eval/templates/` | Three starter datasets: customer-support, rag-grounding, tool-use. Shape-checked in CI |
| `eval-fix/SKILL.md` | The `/eval-fix <id>` command: from an id to a connected engine, and which brief to follow |
| `eval-fix/references/engine-brief.md` | Which engine, how an address resolves, and the three failures that mean "wrong box" |
| `eval-fix/references/triage-brief.md` | Recommendations checked against the source, verdict by verdict, ending at the mapping file |
| `eval-fix/references/eval-brief.md` | The re-run on the same dataset, and the v1-vs-v2 comparison |
| `eval-fix/scripts/fetch_analysis.py` | An evaluation and its AI Analysis pulled off the engine; `--list` doubles as the connection test |
| `eval-fix/scripts/bootstrap.sh` | Makes the repo under test runnable from whichever manifest it finds — not only Python |
| `auto-improve/SKILL.md` | The `/auto-improve <id>` command: the brief for working a report issue by issue, and how to verify against live traffic |
| `auto-improve/scripts/fetch_report.py` | An improvement report pulled off the engine; `--list` shows every report, and says why there are none |

Every `scripts/url_guard.py` is the same file: the http/https allowlist a request passes through
before a key rides along with it. Where a skill has an `evals/evals.json`, it is the case set the
[skill-creator](https://github.com/anthropics/skills) evals run against.

Around the plugin:

| Path | What it is |
|---|---|
| `.claude-plugin/`, `.cursor-plugin/`, `.agents/plugins/` | One marketplace manifest per ecosystem, all pointing at the same plugin directory |
| `plugins/agentx/.claude-plugin/`, `.codex-plugin/`, `.cursor-plugin/` | The plugin's own manifest, once per ecosystem, over one copy of the skills |
| `tests/audit_skills.py` | Holds the docs against the code they tell an agent to run: every script, flag and SDK symbol a brief names has to exist |
| `tests/test_audit_fires.py` | Breaks a copy of the repo in twenty-odd ways and requires the audit to catch each one by name |
| `tests/test_runeval_scripts.py`, `test_live_link.py`, `test_live_grader_surface.py` | The run-eval scripts and the engine endpoints they depend on, live where an engine is reachable |
| `skillevaluator-policy.yaml`, `tests/test_policy_keys.py` | The overlay for running NVIDIA SkillEvaluator locally, and the test that keeps it from quietly covering more than it says. Not a CI gate: the scanner is unpinned upstream and went red on a commit it had passed |
| `.github/workflows/audit.yml` | Runs the tests above on push, on PRs, and weekly, so an SDK rename turns the repo red without waiting for a commit |

Full detail on the tracing and evaluation half is in
[`plugins/agentx/README.md`](plugins/agentx/README.md). Each skill's `SKILL.md` is the
reference for its own workflow.

## Results

Validated end to end on nine agents with deliberately planted defects — levers in code, in a
YAML config, in a data file, and in the evaluation harness itself — then on three LangChain
agents scoring 3.60, 5.50 and 4.80, triaged to 10.00, 9.60 and 9.80 on the same datasets.

Most recently, a LangChain/LangGraph support agent on a self-host engine: **6.25 → 9.53**
average, minimum **1 → 7**, rating variance **5.78 → 0.98**, with criteria, judge and model
frozen. Of seven recommendations, three were applied, two applied with changed scope, one
rejected as already implemented and one as harmful against the dataset's own rejection criteria.
The single change that moved the score most — retrieval width — came from reading the code and
appeared in no recommendation at all.
