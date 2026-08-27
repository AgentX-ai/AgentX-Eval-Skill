# Which engine, and what to do when you cannot reach it

`SKILL.md` carries the part that changes what you do: ask once, before the first read,
and hold the answer. This file is the mechanics behind that question - the precedence
order an address is resolved through, how a scheme-less one is completed, and the three
failures that mean "possibly the wrong box". Read it when the default does not answer,
when the user names an address you have to interpret, or when a read fails.

## Four things that cost a run each when assumed wrong

- **Keys are per project, and so is the data.** The key *is* the project
  selector; an evaluation belongs to exactly one project and is a 404 under
  every other key. `curl -s <engine>/api/v1/projects` lists them all with their keys,
  given any one project's key to authenticate with.
- **`~/.agentx/config.json` can be a different engine's key.** It records
  whichever engine last ran on this machine. A Docker instance keeps its database
  in its own volume and mints its own keys, so the file and the port disagree the
  moment anyone runs the container. `fetch_analysis.py` now verifies that key with a
  real read before using it and says so plainly when it fails, rather than letting it
  surface as a 401 against the evaluation id.
- **The engine hands out a key on one row of the matrix, and only one.**
  `GET /api/v1/auth/config` is unauthenticated and exists in both auth modes — it is how
  the dashboard chooses between login, owner setup and no-auth — and under the default
  `AGENTX_AUTH=disabled` it returns the default project's key outright. So a cold start
  needs no exported variable and nothing pasted. Under `AGENTX_AUTH=enabled` no key is
  ever returned, and the hosted platform serves no such route, where the key has to come
  from the engine's startup output or the dashboard. (`GET /dev/bootstrap`, its
  predecessor, was removed with a test asserting it 404s; this route replaced it.)
  `fetch_analysis.py` tries it **last** — it is always the *default* project, which is
  right for a fresh install and wrong for anyone who has already chosen where their
  evaluations live.
- **Ids are nanoids**, e.g. `oE1YMG5wqmu4j2bhTtw1X`, not the 24-character hex ids
  the hosted platform uses. There is no filename to read one out of, so `--list`
  is how you find one.

## Ask which engine, once, before the first read

`http://localhost:4700` is right for most people, but it is not right for anyone whose
engine runs on another box, and which engine a number came from is invisible until
something fails. So **open with one AskUserQuestion** — three choices, two of them
predefined and answering to a word:

| Option | Address | Pass as |
|---|---|---|
| **local** (default) | `http://localhost:4700` | `--host local` |
| **agentx** | `https://api.agentx.so` | `--host agentx` |
| **other** | whatever the user types | `--host <address>` |

Add a fourth only when `$AGENTX_HOST` or `$AGENTX_API_BASE_URL` names something that is
neither predefined one — read them before asking and quote the value, since someone who
configured it once should not retype it.

**`agentx` is a different dialect, not a second self-host engine.** Its evaluation ids
are 24-character hex where self-host's are nanoids, and the analysis this skill reads
lives on self-host's dashboard router. The script tracks which kind of address it is
on: hex ids are accepted only against `agentx`, a nanoid used there is flagged, and a
missing route answers "this is the hosted platform, which serves a different router"
rather than a bare 404. Offer it when that is where the evaluation lives — just never
as equivalent to self-host.

Ask it once, at the start, before spending anything, and hold the answer for the whole
workflow. The alternative is finding out at the 404 — or worse, not finding out, and
triaging a run the user cannot see from an engine they are not using. Skip the question
only when they already named an address alongside the id, since an answer just given is
not a question:

```
Use the agentx-eval-fix skill on evaluation p6sLDw9CPv0XF0eiUA_zF.
Our engine is at http://10.0.0.5:4700.
```

```bash
python3 <skill>/scripts/fetch_analysis.py p6sLDw9CPv0XF0eiUA_zF --host http://10.0.0.5:4700
```

**The answer is not a lock.** Say once that a different address can be named at any
time; if one is, every later command switches to it — and if the switch comes after the
export was written, re-fetch from the new engine before triaging, because an export from
one engine and a re-run against another is precisely the comparison this guards.

**Plain `http://` is fine**, and on an internal network it is the normal answer. A
scheme-less address is completed from the local default's shape, so `10.0.0.5` means
`http://10.0.0.5:4700` and `agentx.internal:4700` means `http://agentx.internal:4700`.
A scheme the user supplies is left alone, so `https://evals.example.com` stays on 443
as a reverse proxy needs, and a path prefix survives for an engine mounted under one.

**Nobody should have to edit a file or export a variable to tell you where their engine
is.** If an address arrives in a sentence, read it out of the sentence. If none arrives
and the default fails, ask — see below. Environment variables are for people who
already set them, not a step to instruct anyone through.

The address can also be set before the session starts, and the first one set decides:

| Source | What it says |
|---|---|
| `--base-url` | the full API base, when it is not `<host>/api/v1` |
| `--host` | the address, for one command — **this is the one you pass** |
| `$AGENTX_API_BASE_URL` | the full API base — also what the SDK and the harness read |
| `$AGENTX_HOST` | the address, for the session |
| `$HOST` | the same, but only when it carries a scheme |
| nothing | `http://localhost:4700` |

A bare `HOST` is only honoured with a scheme, because `HOST=0.0.0.0` is what a dev
server or a container image sets for *its own* listener and it lands in the environment
of everything started beside it. A hostname-shaped `HOST` is announced and ignored
rather than followed into a port nothing serves.

## When you cannot reach it, ask — do not report it down

Three failures mean "possibly the wrong box", and the script names the address it tried
in each: `cannot reach the engine at ...`, `no usable API key for ...`, and a `404` on
an id the user believes exists. On any of them, **ask with AskUserQuestion**, quoting
that address so the question is answerable at a glance. The user either starts a local
engine and you retry unchanged, or gives you an address you pass as `--host`.

Two things follow once the engine is not local:

- **The key travels with the engine.** Keys are per project *and* per engine, so a
  remote engine needs a key minted by that engine in `$AGENTX_API_KEY`. The local
  `~/.agentx/config.json` is then exactly the wrong file — the same trap as above, one
  step worse — and the script catches it by verifying the key before use. If none
  works, ask the user for theirs; it is one paste from their engine's startup output.
- **Every command needs telling again.** Each Bash call is a fresh shell, so an address
  exported in one is gone by the next, and the repo under test reads
  `$AGENTX_API_BASE_URL` through the SDK regardless. Carry the literal address on each
  command that needs it — `--host <address>` here, `AGENTX_API_BASE_URL=<address>/api/v1`
  on the harness launch line — and record it in the mapping table so the re-run can
  reproduce it. Lose it there and v2 scores against a different engine than v1, with
  nothing erroring.
