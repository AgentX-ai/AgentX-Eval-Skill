#!/usr/bin/env python3
"""Resolve an AgentX project API key from a running engine and write .env.agentx.

Three things this script exists to get right, each of which costs a debugging session
when assumed instead:

1. **The key is the project selector.** Every trace lands in exactly one project - the
   one whose key sent it - and is invisible to every other key. If the traces are meant
   to sit next to an evaluation, both have to run under the same project's key.

2. **`~/.agentx/config.json` records whichever engine last ran on this machine**, which is
   not necessarily the engine you are pointing at now. A Docker instance keeps its own
   database in its own volume and mints its own keys. So the file is a candidate, never
   an answer: every key here is verified with a real authenticated read before it is used,
   and a stale one is reported as stale rather than written into the project's .env.

3. **The key must not pass through the conversation.** Nothing here prints a key in full.
   `--list-projects` masks them, and `--write-env` writes the selected one straight to
   disk, so the secret goes from the engine to the file without a transcript in between.

`GET /api/v1/auth/config` is how this script identifies an engine, and on self-host it is also
the cold-start key source. It is unauthenticated, and in the default `AGENTX_AUTH=disabled`
mode it returns the default project's key outright - the same handout the dashboard uses to
land on a working screen with no paste-the-key step. (Its predecessor `GET /dev/bootstrap` was
removed, with a test asserting it 404s; this route replaced it.) Under `AGENTX_AUTH=enabled` no
key is ever handed out, and the hosted platform serves no such route at all - so the same probe
answers three questions at once: which engine this is, whether it can enumerate projects, and
whether a key can be had without asking.

Project *creation* is unauthenticated too in disabled mode - `--create-project` below - and
mints a fresh key. That writes a row nothing can delete afterwards, so it is opt-in and never
a fallback this script reaches for on its own.

Usage:
  agentx_key.py                                   # resolve a key and say where it came from
  agentx_key.py --list-projects                   # which projects exist, keys masked
  agentx_key.py --write-env .env.agentx           # write the resolved key + base URL
  agentx_key.py --write-env .env.agentx --project Dev
  agentx_key.py --create-project "My agent"       # self-host only, creates a row, asks nothing
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

LOCAL = "http://localhost:4700"
HOSTED = "https://api.agentx.so"
DEFAULT_PORT = 4700
TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------
def resolve_base_url(host: str | None, base_url: str | None) -> str:
    """Turn whatever the user said into a full `<engine>/api/v1`.

    A scheme-less address is completed from the local default's shape, because on an
    internal network `10.0.0.5` and `agentx.internal:4700` are how people actually name
    their engine. A scheme the user supplied is left exactly alone, so a reverse-proxied
    `https://evals.example.com` stays on 443 and keeps any path prefix it was mounted under.
    """
    if base_url:
        return base_url.rstrip("/")

    raw = (host or os.getenv("AGENTX_HOST") or "").strip()
    if not raw:
        env_base = (os.getenv("AGENTX_API_BASE_URL") or "").strip()
        if env_base:
            return env_base.rstrip("/")
        # A bare HOST is only honoured with a scheme: HOST=0.0.0.0 is what a dev server or
        # container image sets for its OWN listener, and it lands in the environment of
        # everything started beside it.
        loose = (os.getenv("HOST") or "").strip()
        raw = loose if loose.startswith(("http://", "https://")) else ""
    if not raw or raw.lower() == "local":
        return f"{LOCAL}/api/v1"
    if raw.lower() == "agentx":
        return f"{HOSTED}/api/v1"
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}" if ":" in raw else f"http://{raw}:{DEFAULT_PORT}"
    raw = raw.rstrip("/")
    return raw if raw.endswith("/api/v1") else f"{raw}/api/v1"


def engine_root(base_url: str) -> str:
    return base_url[: -len("/api/v1")] if base_url.endswith("/api/v1") else base_url


def is_hosted(base_url: str) -> bool:
    return "api.agentx.so" in base_url


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def request(base_url: str, path: str, key: str | None = None, method: str = "GET",
            body: dict | None = None, _retries: int = 1) -> tuple[int, object]:
    """One request. Returns (status, parsed-or-text); never raises on an HTTP error status.

    The credential routes this script uses (`/auth/config`, `/projects`) sit behind the
    engine's stricter rate limiter, and running the skill twice in quick succession is enough
    to trip it. A 429 is a healthy engine saying "slow down", so it gets one backed-off retry
    rather than being handed to the caller as a failure.
    """
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base_url}{path}", data=payload, method=method)
    if key:
        req.add_header("x-api-key", key)
    if payload is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            text = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(text)
            except json.JSONDecodeError:
                return resp.status, text
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and _retries > 0:
            time.sleep(2.0)
            return request(base_url, path, key, method, body, _retries - 1)
        text = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(text)
        except json.JSONDecodeError:
            return exc.code, text
    except urllib.error.URLError as exc:
        raise ConnectionError(f"cannot reach the engine at {engine_root(base_url)} ({exc.reason})") from exc


def key_works(base_url: str, key: str) -> bool:
    """Verify a key with a real authenticated read against THIS engine.

    `/ingest/traces` is the router traces themselves go to, so a key that passes here is
    a key that can actually deliver - which is the only property this script cares about.
    `/monitor/patterns` is the fallback for an engine that predates the trace list route.
    """
    for path in ("/ingest/traces?limit=1", "/monitor/patterns"):
        try:
            status, _ = request(base_url, path, key)
        except ConnectionError:
            return False
        if status == 200:
            return True
        if status in (401, 403):
            return False
    return False


# ---------------------------------------------------------------------------
# Engine identification
# ---------------------------------------------------------------------------
def describe_engine(base_url: str) -> dict:
    """Who is answering, and can a key enumerate projects here?

    One unauthenticated call to `/auth/config` settles all of it. Self-host answers with a
    `mode`; the hosted platform has no such route. That distinction is what decides whether
    the user gets to *choose* a project at all:

    | engine                          | /projects with a key            | choose a project? |
    |---------------------------------|---------------------------------|-------------------|
    | self-host, AGENTX_AUTH=disabled | lists every project, with keys  | yes               |
    | self-host, AGENTX_AUTH=enabled  | 401, needs a signed-in session  | no - dashboard    |
    | hosted (api.agentx.so)          | no such route                   | no - key selects  |
    """
    info = {"kind": "unknown", "auth_mode": None, "default_key": None,
            "can_list_projects": False, "reason": ""}

    if is_hosted(base_url):
        info.update(kind="hosted", can_list_projects=False,
                    reason="the hosted platform serves no /projects route - the API key "
                           "selects the workspace on its own")
        return info

    try:
        status, payload = request(base_url, "/auth/config")
    except ConnectionError:
        raise

    if status == 429:
        info.update(kind="rate-limited", can_list_projects=False,
                    reason="the engine rate-limited this check - it is up, just asked to slow "
                           "down. Wait a few seconds and run the same command again")
        return info

    if status != 200 or not isinstance(payload, dict) or "mode" not in payload:
        info.update(kind="unknown", can_list_projects=False,
                    reason=f"this engine did not answer /auth/config (HTTP {status}), so it is "
                           f"not a self-host engine this script recognises")
        return info

    mode = payload.get("mode")
    info["kind"] = "self-host"
    info["auth_mode"] = mode
    # Only disabled mode hands out a key, and only the default project's.
    info["default_key"] = payload.get("apiKey")
    if mode == "disabled":
        info.update(can_list_projects=True, reason="")
    else:
        info.update(can_list_projects=False,
                    reason="this engine runs with AGENTX_AUTH=enabled, where listing projects "
                           "needs a signed-in session rather than a key. Pick the project in "
                           "the dashboard and use its key")
    return info


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------
def config_path() -> Path:
    return Path(os.getenv("AGENTX_HOME", str(Path.home() / ".agentx"))) / "config.json"


def resolve_api_key(explicit: str | None, base_url: str,
                    engine: dict | None = None) -> tuple[str | None, str, list[str]]:
    """(key, where-it-came-from, what-was-tried-and-failed). Every candidate is verified.

    Ordered by how specific the intent behind it is. Something the user typed beats something
    their shell remembers, which beats a file recording whichever engine last ran here, which
    beats the engine's own default. The engine's handout is last precisely because it is always
    the *default* project - correct for a fresh install, wrong for anyone who has already
    chosen where their data goes.
    """
    tried: list[str] = []

    candidates: list[tuple[str, str]] = []
    if explicit:
        candidates.append((explicit, "--api-key"))
    from_env = os.getenv("AGENTX_API_KEY")
    if from_env:
        candidates.append((from_env, "$AGENTX_API_KEY"))
    cfg = config_path()
    if cfg.is_file():
        try:
            from_file = json.loads(cfg.read_text()).get("apiKey")
        except (json.JSONDecodeError, OSError):
            from_file = None
            tried.append(f"{cfg} is not readable JSON")
        if from_file:
            candidates.append((from_file, str(cfg)))
    if engine and engine.get("default_key"):
        candidates.append((engine["default_key"], "GET /auth/config (this engine's default project)"))

    for key, source in candidates:
        if key_works(base_url, key):
            return key, source, tried
        tried.append(f"{source} holds a key this engine rejects")

    if not candidates:
        tried.append("$AGENTX_API_KEY is unset, there is no ~/.agentx/config.json, and this "
                     "engine hands out no key")
    return None, "", tried


# Nothing here prints anything derived from a key, not even a masked or hashed form.
#
# Two earlier attempts got this wrong in the same way. A slice ("agtx_local_1…f394") still
# leaked four real characters and confirmed the prefix; a truncated SHA-256 leaked nothing but
# was a hash of a credential, which is its own thing to have to explain. Both existed to answer
# "which key is this?" - and every line that asked already carried the project id, which answers
# "which project is this?" better and is not a secret at all.
#
# So the key appears in exactly one place: the file the SDK reads it back from.


def assert_no_secret(payload: object, secret: str) -> None:
    """Refuse to emit a structure that contains a whole key.

    Every field in the JSON summary is meant to be a label or a masked form, and the review
    that establishes that is a review of code someone will later edit. This makes the property
    hold at runtime instead: a future field that carries the raw key stops the program rather
    than printing it into a transcript, a CI log, or an issue someone pastes it into.
    """
    if not secret or len(secret) < 12:
        return
    if secret in json.dumps(payload):
        raise SystemExit(
            "internal error: refusing to print a payload containing the API key in full. "
            "Whatever field was just added should carry the project id or the source "
            "label, not the key itself."
        )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
def list_projects(base_url: str, key: str) -> list[dict]:
    status, payload = request(base_url, "/projects", key)
    if status != 200 or not isinstance(payload, dict):
        return []
    return payload.get("projects") or []


def create_project(base_url: str, name: str) -> dict:
    """Mint a project - and with it a key - on a self-host engine in its default auth mode.

    This is the one route that hands out a key without already holding one. It is also
    irreversible: the engine serves no project delete, so a project created by mistake
    stays in the list forever. Never call it to work around a key that merely failed to
    verify; that key belongs to a project whose data someone wants.
    """
    status, payload = request(base_url, "/projects", None, method="POST", body={"name": name})
    if status == 201 and isinstance(payload, dict):
        return payload.get("project") or {}
    detail = payload.get("error") if isinstance(payload, dict) else payload
    if status == 401:
        raise SystemExit(
            f"the engine at {engine_root(base_url)} requires a signed-in session to create a "
            f"project (it runs with AGENTX_AUTH=enabled). Create it from the dashboard instead, "
            f"then copy the key from its project settings."
        )
    raise SystemExit(f"could not create the project (HTTP {status}): {detail}")


def pick(projects: list[dict], wanted: str | None) -> dict | None:
    if not projects:
        return None
    if not wanted:
        return next((p for p in projects if p.get("isDefault")), projects[0])
    for p in projects:
        if wanted in (p.get("_id"), p.get("name")):
            return p
    return None


# ---------------------------------------------------------------------------
# .env.agentx
# ---------------------------------------------------------------------------
ENV_HEADER = "# AgentX tracing - written by the instrument skill.\n" \
             "# Local credentials. Not for version control.\n"


def write_env(path: Path, key: str, base_url: str, force: bool) -> str:
    """Write the key and the base URL, and refuse to silently overwrite a different key.

    AGENTX_API_BASE_URL is written alongside the key on purpose. Left unset, the SDK
    defaults to the hosted platform, so a self-host user's traces leave for api.agentx.so
    and nothing errors - they simply never appear in the dashboard they are watching.
    """
    existing = path.read_text() if path.is_file() else ""
    if existing and not force:
        current = re.search(r"^AGENTX_API_KEY=(.*)$", existing, re.M)
        if current and current.group(1).strip().strip('"') != key:
            raise SystemExit(
                f"{path} already sets AGENTX_API_KEY to a different key. It is the project "
                f"selector, so replacing it moves every future trace to another project. "
                f"Re-run with --force once that is what you want."
            )

    # Writing the credential in clear text is this function's entire purpose - the SDK reads
    # it back from the environment, so there is no encrypted form it could use. What limits the
    # exposure is the mode and the ignore rule below, not the encoding.
    lines = [ENV_HEADER, f"AGENTX_API_KEY={key}\n", f"AGENTX_API_BASE_URL={base_url}\n"]
    path.write_text("".join(lines))  # codeql[py/clear-text-storage-sensitive-data]
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return "overwrote" if existing else "wrote"


def ensure_gitignored(repo: Path, filename: str) -> str | None:
    """A key file that is not ignored is one `git add .` from being published.

    Written whether or not this is a repository yet. It used to return early without one, on the
    reasoning that a non-repo cannot leak through git - true today, and wrong the moment someone
    runs `git init`, which is a thing the tracing skill itself now offers to do. A one-line
    .gitignore in a directory that never becomes a repo costs nothing; a credential in the first
    commit of one that does is not something you can take back.
    """
    gitignore = repo / ".gitignore"
    body = gitignore.read_text() if gitignore.is_file() else ""
    if any(line.strip().lstrip("/") == filename for line in body.splitlines()):
        return None
    with gitignore.open("a") as fh:
        if body and not body.endswith("\n"):
            fh.write("\n")
        fh.write(f"{filename}\n")
    return str(gitignore)


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="local | agentx | an address (default: local, i.e. http://localhost:4700)")
    ap.add_argument("--base-url", help="the full API base, when it is not <host>/api/v1")
    ap.add_argument("--api-key", help="a key to use and verify (default: $AGENTX_API_KEY, then ~/.agentx/config.json)")
    ap.add_argument("--project", help="select a project by id or name (default: the engine's default project)")
    ap.add_argument("--list-projects", action="store_true", help="list projects, keys masked")
    ap.add_argument("--limit", type=int, default=20, help="how many projects to show (default 20)")
    ap.add_argument("--create-project", metavar="NAME", help="self-host only: mint a project and a key. Irreversible.")
    ap.add_argument("--write-env", metavar="PATH", help="write the resolved key and base URL to this file")
    ap.add_argument("--no-gitignore", action="store_true", help="do not add the env file to .gitignore")
    ap.add_argument("--force", action="store_true", help="overwrite an env file that names a different key")
    ap.add_argument("--json", action="store_true", help="machine-readable summary on stdout")
    args = ap.parse_args()

    base_url = resolve_base_url(args.host, args.base_url)

    # Identify the engine before anything else. It decides where a key can come from and
    # whether the user gets to choose a project at all.
    try:
        engine = describe_engine(base_url)
    except ConnectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("Start the engine, or name the right address with --host.", file=sys.stderr)
        return 2

    kind = engine["kind"] + (f" (auth {engine['auth_mode']})" if engine["auth_mode"] else "")
    print(f"engine: {engine_root(base_url)} - {kind}", file=sys.stderr)
    if not engine["can_list_projects"]:
        print(f"  no project choice here: {engine['reason']}", file=sys.stderr)

    out: dict = {"base_url": base_url, "engine": engine_root(base_url),
                 "engine_kind": engine["kind"], "auth_mode": engine["auth_mode"],
                 "can_list_projects": engine["can_list_projects"],
                 "reason": engine["reason"]}

    if args.create_project:
        if engine["kind"] != "self-host" or engine["auth_mode"] != "disabled":
            raise SystemExit(
                f"this engine cannot create a project without authentication "
                f"({engine['reason'] or 'not a self-host engine in its default auth mode'}). "
                f"Create it from the dashboard instead, then copy its key.")
        project = create_project(base_url, args.create_project)
        key = project.get("apiKey", "")
        print(f"created project {project.get('name')!r} ({project.get('_id')})", file=sys.stderr)
        out.update(project_id=project.get("_id"), project_name=project.get("name"),
                   key_source="POST /projects")
        if args.write_env:
            path = Path(args.write_env)
            verb = write_env(path, key, base_url, force=True)
            print(f"{verb} {path}", file=sys.stderr)
            out["env_file"] = str(path)
            if not args.no_gitignore:
                changed = ensure_gitignored(Path.cwd(), path.name)
                if changed:
                    print(f"added {path.name} to {changed}", file=sys.stderr)
        if args.json:
            assert_no_secret(out, key)
            print(json.dumps(out, indent=2))
        return 0

    try:
        key, source, tried = resolve_api_key(args.api_key, base_url, engine)
    except ConnectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("Start the engine, or name the right address with --host.", file=sys.stderr)
        return 2

    if not key:
        print(f"no usable API key for {engine_root(base_url)}:", file=sys.stderr)
        for line in tried:
            # `tried` holds where a key came from ("$AGENTX_API_KEY", a path), never a value.
            print(f"  - {line}", file=sys.stderr)
        print("\nA key comes from the engine you are pointing at, and only from there:", file=sys.stderr)
        if engine["kind"] == "hosted":
            print("  - app.agentx.so, under your workspace settings", file=sys.stderr)
        else:
            print(f"  - the engine's startup log: 'Default project API key: ...'", file=sys.stderr)
            print(f"  - the dashboard at {engine_root(base_url)}, project settings", file=sys.stderr)
            print(f"  - a new project: {sys.argv[0]} --create-project <name>  (irreversible)", file=sys.stderr)
        print("\nThen pass it as --api-key, or export AGENTX_API_KEY.", file=sys.stderr)
        return 3

    print(f"key from {source}, verified against this engine", file=sys.stderr)
    out.update(key_source=source)

    projects = list_projects(base_url, key) if engine["can_list_projects"] else []
    if projects:
        ordered_all = sorted(projects, key=lambda p: not p.get("isDefault"))
        out["project_count"] = len(projects)
        # Always in the JSON, capped: this is what the caller builds the "which project?"
        # question from, and it should not need a second invocation to get it.
        out["projects"] = [{"id": p.get("_id"), "name": p.get("name"),
                            "isDefault": bool(p.get("isDefault"))}
                           for p in ordered_all[: max(1, args.limit)]]
    if args.list_projects:
        # Capped, default first. An engine that has been used for a while accumulates projects
        # faster than anyone wants to read, and the whole list in a transcript is noise around
        # the one line that matters.
        if not projects:
            print(f"no projects to choose from: {engine['reason'] or 'this key listed none'}",
                  file=sys.stderr)
        ordered = sorted(projects, key=lambda p: not p.get("isDefault"))
        shown = ordered[: max(1, args.limit)]
        for p in shown:
            flag = " (default)" if p.get("isDefault") else ""
            print(f"  {p.get('_id')}  {p.get('name')}{flag}", file=sys.stderr)
        if len(ordered) > len(shown):
            print(f"  ... and {len(ordered) - len(shown)} more (--limit to show them, "
                  f"--project <id> to select one by id)", file=sys.stderr)

    if args.write_env:
        chosen_key, chosen_name = key, None
        if args.project:
            # A name can be ambiguous - self-host does not make project names unique - and
            # picking silently would put the traces in whichever row happened to be first.
            by_name = [p for p in projects if p.get("name") == args.project]
            if len(by_name) > 1 and not any(p.get("_id") == args.project for p in projects):
                ids = ", ".join(str(p.get("_id")) for p in by_name[:8])
                more = f" (and {len(by_name) - 8} more)" if len(by_name) > 8 else ""
                raise SystemExit(f"{len(by_name)} projects are named {args.project!r}. "
                                 f"Select one by id instead: {ids}{more}")
            match = pick(projects, args.project)
            if not match:
                names = ", ".join(f"{p.get('name')} ({p.get('_id')})" for p in projects[:20]) or "none listed"
                raise SystemExit(f"no project matches {args.project!r}. Available: {names}")
            chosen_key, chosen_name = match.get("apiKey", key), match.get("name")
        path = Path(args.write_env)
        verb = write_env(path, chosen_key, base_url, args.force)
        where = f" for project {chosen_name!r}" if chosen_name else ""
        print(f"{verb} {path}{where}", file=sys.stderr)
        out["env_file"] = str(path)
        out["project_name"] = chosen_name
        if not args.no_gitignore:
            changed = ensure_gitignored(Path.cwd(), path.name)
            if changed:
                print(f"added {path.name} to {changed}", file=sys.stderr)

    if args.json:
        # `out` carries ids and source labels, never a key. Enforced, not asserted.
        assert_no_secret(out, key)
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
