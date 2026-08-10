# Running someone else's code on a Server4Agent box

Everything here is a property of the platform, not of the repo under test. Read
it before the first `prompt` call. Each item is here because it changes what you
should do, not for completeness.

## Runs

**A run is a fresh session every time.** MCP `prompt` passes no resume id, so each
call starts an agent with no memory of the last one, in the same folder. This is
why the briefs in `references/` are long and self-contained rather than a
conversation. Do not write a prompt that says "continue where you left off".

**A run is force-failed at 30 minutes.** `TASK_MAX_RUN_MS` in the control plane
flips a run to `failed` and cancels it, while the worker itself allows 60. The
shorter one wins, so budget against 30. Turns are capped separately at 60
(`MAX_AGENT_TURNS`).

That ceiling is the reason the work is split into two prompts and the reason the
evaluation is launched detached. An evaluation that invokes a model once per
case, then polls a server-side analysis job, can exceed 30 minutes on its own;
the SDK's own analysis wait defaults to 1800 seconds. A run killed at the ceiling
loses everything the agent has not already written to a file, which is why both
briefs write their results to disk before reporting them.

**`change` and `question` come back null.** `get_run` has fields for the commit
and files-changed summary, and for an agent question, but only the legacy Docker
worker ever populates them. On the live path they are always null. Two
consequences:

- To find out what the agent changed, use `exec` with git commands or
  `read_file`. Never infer it from `change`.
- A run cannot ask you anything. There is no channel for an answer, so a brief
  that invites a question just burns wall clock. Both briefs tell the agent to
  decide and record the uncertainty instead.

What you do get: `status`, `log` (latest progress note), `result` (the agent's
final message) and `usage`. `result` is the only text channel out, which is why
each brief ends with a mandatory fixed-format block. Nothing important may live
outside that block.

## `exec`

Capped near 20 seconds, and it does **not** source the workspace env file: it
runs `cd <project dir> && <command>` and nothing more.

**It also runs as root, while the workspace belongs to `node`.** Git therefore
refuses every command in that repo as "dubious ownership", which looks the same
as a broken repository. Prefix git commands with `-c safe.directory="$PWD"`:

```bash
git -c safe.directory="$PWD" --no-pager log --oneline -3
```

The inner agent never hits this, since the worker runs it as `node`. It is
specific to inspecting a repo through `exec`.

So: no `pip install`, no `git fetch`, no running the evaluation, nothing
network-bound. Every git command needs `--no-pager` and a `| head`, because a
pager waiting for input burns the whole timeout. If a command needs the project's
secrets, prefix it with `. .server4agent/env.sh &&`.

Use `exec` for inspection. Use `prompt` for work.

## Secrets

Bound per account, then attached to a server or a project. Project scope
overrides server scope for the same key.

**They are resolved at run dispatch.** A secret attached after a run has started
does not reach it. Attach everything before the first `prompt`, or pass them in
the `create_project` call, which binds them project-scoped before bring-up
dispatches.

Delivery is two files in the workspace, both mode 600: `.server4agent/env.sh`
(sourced by the shell that launches the agent, and therefore inherited by
anything the agent starts) and `.server4agent/env` (plain dotenv, for libraries
that read one). `.server4agent/` is in the global git excludes, so run secrets
cannot be committed.

### Name your provider keys with a prefix

The runtime image's sudoers block preserves a specific provider credential env
var for each agent runtime adapter across the switch to the `node` user:
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and the equivalent for each other adapter.
The launch command sources `.server4agent/env.sh` **before** exec'ing the agent
binary.

Put a secret under one of those exact names and it overrides the platform's own
credential for the coding agent, on that run and every later run on the project.
The failure looks like a platform outage, not like a misconfigured secret, which
is what makes it worth avoiding by construction.

So attach the repo's provider keys under prefixed names, and map them back on the
command line of the one process that needs them:

```bash
PROVIDER_A_KEY="$EVAL_PROVIDER_A_KEY" .venv/bin/python run_eval.py
```

The real names then exist inside that process and nowhere else.

## The container

Base image is Node. It has `python3`, `git`, `curl`, `sudo` (passwordless), and
`cron`. It does **not** have `pip` or the `venv` module, and the system Python is
PEP-668 externally managed, so installing into site-packages is refused outright.

`scripts/bootstrap.sh` handles this: install `python3-pip` and `python3-venv`,
then build a virtualenv. Use a venv rather than `--break-system-packages`. It
sidesteps PEP-668 honestly, it persists in the workspace so the second run is
nearly free, and it keeps the repo's dependency tree away from anything else on
the box.

Long-running processes follow the platform convention:

```bash
nohup setsid <command> > .server4agent/<name>.log 2>&1 &
```

Outbound network is open. The egress NetworkPolicy denies the metadata endpoint
and private ranges, and a VPC rule blocks SMTP, but every public API the repo
needs is reachable. There is no host allowlist to add anything to.

Resources are tier-scoped, and exceeding the memory limit kills the whole
sandbox. The run then reports something about not being able to exec in a stopped
state. If that happens on a small tier, the fix is a larger tier, not a retry.

## Git

Projects created with a git source clone into `/home/node/workspace/<slug>`,
which is the working directory for every run on that project.

For a private repo, the platform mints a short-lived, repo-scoped credential per
run and delivers it as `$S4A_GIT_TOKEN`, kept out of the workspace and deleted
when the run ends. It is usually good for `contents: write` and pull requests,
and falls back to read-only if the installation grants less. Push with:

```bash
git push "https://x-access-token:$S4A_GIT_TOKEN@github.com/<owner>/<repo>.git" HEAD:<branch>
```

The `x-access-token` username is required. Never write the token into a file, a
commit, a remote, or a command that gets echoed.

The token exists only if a GitHub App installation covers the repo. Without one,
the clone fails as an anonymous 404 that looks like a wrong URL. The alternative
is a personal access token stored as a secret and named in `source.auth_secret`.

A read-only installation means the push is refused at the end of an otherwise
successful run. Both briefs treat that as a reportable outcome, not a failure:
the commit exists locally and `exec` can still show the diff.

## Tools not to call

`start_build`, `get_build` and `deploy` are stubs. `get_build` advances a
simulated progress counter and returns hardcoded artifact names; `deploy` writes
a bookkeeping record. Nothing builds and nothing ships. Real serving comes from a
project's `visibility`, and none of that is relevant here anyway, since an
evaluation harness is not a web service.

## Reading the evaluation SDK's report object

Verified attribute names, so a run is not wasted guessing:

| What the export calls it | Attribute |
|---|---|
| Average score | `report.statistics.average_rating` |
| Min / Max score | `report.statistics.min_rating` / `.max_rating` |
| Runs | `report.statistics.number_of_runs` |
| Consistency score | `report.consistency_score` |
| Instruction adherence | `report.instruction_adherence.score` |
| Overall assessment | `report.overall_rating`, `report.summary` |
| Recommendations | `report.recommendations[].{category, priority, recommendation, reasoning}` |
| (not in the export) | `report.run_id`, `report.dataset_id`, `report.dashboard_url`, `report.jaccard_similarity`, `report.rouge_score` |

Two things to know about that table:

**There is no variance field.** The export reports a score variance;
`ReportStatistics` does not expose one. Use max minus min as the spread and label
it as a substitute rather than presenting it as the same number.

**Print `report.dataset_id`.** It costs one line and it makes the run's own log
prove that it scored against the dataset it was supposed to, which is the failure
this whole workflow is most exposed to.
