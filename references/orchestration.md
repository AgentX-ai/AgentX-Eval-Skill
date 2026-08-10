# Driving this from outside, over Server4Agent MCP

For when the code lives in a Server4Agent project rather than on this machine.
Read `server4agent-runtime.md` first: it covers the run ceiling, why prompts carry
no memory, why `exec` cannot do real work, and the secret naming rule that stops
an evaluation key from breaking the box's own agent.

This assumes the project **already exists** and bring-up has finished. That is the
normal lifecycle: someone creates a project from git, the agent clones the repo
and gets the codebase running, and only then is there something worth evaluating.
This is not a provisioning flow. If the project does not exist yet, create it the
usual way and let bring-up finish first, because a triage against a repo whose
dependencies were never installed reports things that are really just an
incomplete setup.

## 1. Equip the project, once

Install this skill into the project with `write_file`. Paths are relative to the
project workspace, which is the only place `write_file` can reach: it strips
leading slashes, rejects `..`, and prefixes the workspace root.

```
.claude/skills/agentx-eval-fix/SKILL.md
.claude/skills/agentx-eval-fix/references/triage-brief.md
.claude/skills/agentx-eval-fix/references/eval-brief.md
.claude/skills/agentx-eval-fix/references/server4agent-runtime.md
.claude/skills/agentx-eval-fix/scripts/bootstrap.sh
```

`orchestration.md` and the two input scripts stay on the orchestrating side. The
box never runs them, and every kilobyte it does not read is a turn it keeps.

The project's agent runs with the workspace as its working directory, so it picks
up `<workspace>/.claude/skills/` the way a local session picks up a repo's skills.
This is verified behaviour in headless mode, not an assumption.

Verify each write by checksum rather than trusting it. A truncated brief fails in
ways that look like a bad triage:

```bash
sha256sum .claude/skills/agentx-eval-fix/references/triage-brief.md
```

Then keep it out of the repo, with one `exec`:

```bash
mkdir -p .git/info && grep -qxF '.claude/skills/agentx-eval-fix/' .git/info/exclude 2>/dev/null \
  || echo '.claude/skills/agentx-eval-fix/' >> .git/info/exclude
```

`.git/info/exclude` is per-clone and never committed, which is what you want here.
The repo under test is the thing being measured, and tooling that shows up in its
`git status` ends up in the diff a reviewer reads.

This step is once per project. Later analyses reuse it.

## 2. Check the secrets

The evaluation needs an API key that reaches the dataset, the dataset id, and the
repo's own provider credentials. Secrets resolve **at run dispatch**, so attach
anything missing before prompting, not after.

**Prefix the repo's provider credentials.** Attaching one under its conventional
name overrides the platform's own credential for the agent that runs these
prompts, on this and every later run, and it presents as a platform outage rather
than a bad secret. Do not attach a version tag or a configuration constant as a
secret either: those become code changes in the triage, and a secret shadowing the
code would make the committed diff a lie about what was tested.

`scripts/fetch_analysis.py <evaluation_id>` is a cheap way to prove the key works
before anything expensive runs.

One thing worth checking before you plan a re-run: **the box has to be able to
reach the evaluation API.** A key pointed at a development API on localhost is
unreachable from a pod, and the failure arrives late.

## 3. Stage the analysis

`write_file` it to `eval-analysis/exports/<filename>.md`. That path is in the repo
proper, not hidden, because the mapping table will be read against it and it
should version alongside the fix.

## 4. Prompt the triage

Because the skill ships its own briefs, the prompt is one line:

```jsonc
prompt({ server_id, project_id, prompt:
  "Use the agentx-eval-fix skill. Follow its references/triage-brief.md exactly,
   in order, against this repo. The analysis to triage is
   eval-analysis/exports/<filename>.md" })
```

Poll `get_run` until terminal. Five to twelve minutes. The brief ends with a
`TRIAGE COMPLETE` block in `result`, which is the only structured channel back:
`change` comes back null on this platform. It also writes the same block to
`eval-analysis/triage-report-<EVAL_ID>.md`. Read that file whenever `result` is
missing or truncated: runs do hit the 60 turn limit, and a run that finished the
work then died before reporting looks identical to one that did nothing unless
you go and look.

## 5. Checkpoint, then the evaluation

`read_file` the mapping table, show the user, and stop by default.

Confirm the diff independently through `exec`:

```bash
git -c safe.directory="$PWD" --no-pager diff --stat <first commit>..HEAD
git -c safe.directory="$PWD" --no-pager diff <first commit>..HEAD -- <agent file> | head -120
```

`safe.directory` is not optional. `exec` runs as root while the workspace belongs
to `node`, so git refuses the repo as "dubious ownership" and every command fails
identically whether or not the triage worked. The inner agent does not hit this,
because it runs as `node`. Every command also needs `--no-pager` and a `| head`,
since `exec` is capped near 20 seconds.

Then the same one-line prompt shape pointing at `references/eval-brief.md`. Ten to
twenty minutes, and `read_file` the v2 report.
