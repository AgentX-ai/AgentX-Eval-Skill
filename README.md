# AgentX for coding agents

One plugin, two commands, one loop: get an agent's real runs into
[AgentX](https://github.com/AgentX-ai/AgentX-trace-eval) — the Trace/Evaluate/Monitor engine you
run yourself — then turn what they measure into a code fix.

| Skill | What it does |
|---|---|
| `/agentx-init` | Sets a Python agent up on AgentX: key, SDK, one span where the run begins, then traces sent and read back to prove it |
| `/eval-fix <id>` | Triages an evaluation against the real source, applies what survives, re-runs it on the same dataset |

```
/agentx-init ──► traced runs ──► evaluation ──► /eval-fix ──► v1 vs v2
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

From inside the repo that holds your agent — both commands read that source.

```
/agentx-init
```

Asks at most three questions: which engine, which entry point (only if several are plausible),
and which project the traces go under (only if the engine can list them). Then it surveys the
repo, writes `.env.agentx`, instruments the entry point, and proves it by sending a trace and
fetching it back by id. It does **not** decorate every function — nesting is automatic, so one
span at the entry point plus one line of framework auto-instrumentation is the whole job.

```
/eval-fix oE1YMG5wqmu4j2bhTtw1X
```

The evaluation id is the whole input; copy it from the run's card in the Evaluate tab. Anything
after it is passed through as extra instruction. It fetches the run, triages every
recommendation against the code, applies what survived on a branch in a worktree, and **stops**
at `eval-analysis/mapping-<id>.md`. Everything to that point is free; the re-run is not, so it
asks first.

Both open with one question about which engine to use — `http://localhost:4700` by default —
because that address decides which database every number comes from, and it is invisible until
something fails.

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

Everything is under `plugins/agentx/`.

| Path | What it is |
|---|---|
| `skills/agentx-init/` | The `/agentx-init` slash command: where to trace and where not to, plus three scripts |
| `skills/eval-fix/` | The `/eval-fix <id>` slash command: connecting to the engine, the triage brief, and the re-run brief |

Full detail on the tracing half is in [`plugins/agentx/README.md`](plugins/agentx/README.md).
Each skill's `SKILL.md` is the reference for its own workflow.

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
