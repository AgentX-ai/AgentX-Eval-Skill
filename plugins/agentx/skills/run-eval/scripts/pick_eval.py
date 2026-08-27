#!/usr/bin/env python3
"""List and validate what an evaluation can run against: datasets and grading configs.

Read-only. Two jobs, both in service of one AskUserQuestion each:

  pick_eval.py                          # datasets + evaluation settings, human-readable
  pick_eval.py --json                   # same, for building the question options
  pick_eval.py --validate-dataset ID    # exit 0 iff the id exists on THIS engine
  pick_eval.py --validate-settings ID   # same for a grading config

Why validation is a first-class flag: a dataset id is engine-scoped and project-scoped.
An id copied from the wrong dashboard, or minted under another project's key, does not
error anywhere downstream - the run would simply 404 at launch after the harness is
already written. Validating up front turns that into a one-line answer before anything
is generated.

Auth comes from AGENTX_API_KEY / AGENTX_API_BASE_URL, falling back to ./.env.agentx
(the file agentx_key.py writes). Nothing derived from the key is ever printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit

DEFAULT_BASE = "http://localhost:4700/api/v1"


def die(msg: str, code: int = 1) -> "NoReturn":  # noqa: F821
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def load_env_file(path: str) -> dict:
    """A four-line dotenv: KEY=VALUE lines, quotes stripped, comments ignored."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


def resolve_auth(env_file: str) -> tuple[str, str]:
    """Real environment wins over the file, same precedence the SDK uses."""
    filed = load_env_file(env_file)
    key = os.environ.get("AGENTX_API_KEY") or filed.get("AGENTX_API_KEY") or ""
    base = (
        os.environ.get("AGENTX_API_BASE_URL")
        or filed.get("AGENTX_API_BASE_URL")
        or DEFAULT_BASE
    ).rstrip("/")
    if not key:
        die(
            "no AGENTX_API_KEY in the environment or " + env_file + ". "
            "Run the instrument skill's agentx_key.py first - it writes .env.agentx."
        )
    return key, base


ALLOWED_SCHEMES = ("http", "https")


def checked_url(url: str) -> str:
    """Refuse anything but http/https before the API key rides along with the request.

    The base URL arrives from the environment, an env file or a flag, and urlopen honours
    file:, ftp: and custom schemes as readily as http. Unchecked, a mistyped base turns a
    request into a local file read - and the key is attached either way.
    """
    if urlsplit(url).scheme not in ALLOWED_SCHEMES:
        die(f"refusing to send credentials to {url}: only http:// and https:// are allowed. "
            "Check AGENTX_API_BASE_URL and any env file.")
    return url


def get(base: str, key: str, path: str):
    req = urllib.request.Request(checked_url(base + path), headers={"x-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 - checked_url() allowlists the scheme
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                die(
                    f"{base + path} answered with something that is not JSON. "
                    f"If the base URL lacks /api/v1, the dashboard SPA answers instead of the API - "
                    f"AGENTX_API_BASE_URL should end in /api/v1.",
                    2,
                )
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {}
        return e.code, body
    except urllib.error.URLError as e:
        die(f"cannot reach {base} ({e.reason}). Is the engine running?", 2)


def smoke_test_variants(questions: list) -> int:
    """Extra rated items the dataset asks for on top of its own questions.

    A question can carry `smokeTest: {enabled, count}`, and the engine then rates that
    many paraphrases of it alongside the original. They are declared here, not invented
    at run time, so the preflight can state the real number instead of a floor - two of
    the three shipped templates enable it, which is why a 6-case dataset finalizes at 8.
    """
    total = 0
    for q in questions or []:
        main = (q or {}).get("main_question") or q or {}
        smoke = main.get("smokeTest") or {}
        if smoke.get("enabled"):
            total += smoke.get("count") or 0
    return total


def summarize_dataset(row: dict) -> dict:
    questions = row.get("questions") or []
    variants = smoke_test_variants(questions)
    requests = row.get("numberOfRequests")
    return {
        "id": row.get("_id") or row.get("id"),
        "name": row.get("name") or "(unnamed)",
        "cases": len(questions),
        "numberOfRequests": requests,
        "smokeTestVariants": variants,
        # What a run of this dataset actually bills, agent call and judge call alike.
        "ratedItems": len(questions) * (requests or 1) + variants,
        "hasCriteria": bool(row.get("acceptanceCriteria") or row.get("rejectionCriteria")),
        "description": (row.get("description") or "")[:140],
    }


def summarize_settings(row: dict) -> dict:
    return {
        "id": row.get("_id") or row.get("id"),
        "name": row.get("name") or "(unnamed)",
        "isDefault": bool(row.get("isDefault")),
        "description": (row.get("description") or "")[:140],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=".env.agentx", help="where to read the key/base URL (default ./.env.agentx)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--limit", type=int, default=12, help="max rows per list (default 12)")
    ap.add_argument("--validate-dataset", metavar="ID", help="check one dataset id exists; exit 3 if not")
    ap.add_argument("--validate-settings", metavar="ID", help="check one settings id exists; exit 3 if not")
    args = ap.parse_args()

    key, base = resolve_auth(args.env_file)

    if args.validate_dataset is not None or args.validate_settings is not None:
        kind = "dataset" if args.validate_dataset is not None else "settings"
        ident = (args.validate_dataset if kind == "dataset" else args.validate_settings).strip()
        if not ident:
            # An empty id is how a broken variable expansion looks - refusing it loudly
            # beats silently listing everything as if the flag were never passed.
            die(f"--validate-{kind if kind == 'dataset' else 'settings'} got an empty id")
        quoted = urllib.parse.quote(ident, safe="")
        path = (
            f"/custom-agent-evaluations/datasets/{quoted}"
            if kind == "dataset"
            else f"/custom-agent-evaluations/evaluation-settings/{quoted}"
        )
        status, body = get(base, key, path)
        if status == 200:
            row = body if isinstance(body, dict) else {}
            summary = summarize_dataset(row) if kind == "dataset" else summarize_settings(row)
            if args.json:
                print(json.dumps({"ok": True, "kind": kind, kind: summary}))
            else:
                extra = ""
                if kind == "dataset":
                    extra = f", {summary['cases']} case(s)"
                    if summary["smokeTestVariants"]:
                        extra += (f" + {summary['smokeTestVariants']} smoke-test variant(s)"
                                  f" = {summary['ratedItems']} rated")
                print(f"ok: {kind} {ident} is '{summary['name']}'{extra} on {base}")
            return
        if args.json:
            print(json.dumps({"ok": False, "kind": kind, "id": ident, "status": status}))
        else:
            print(
                f"NOT FOUND: no {kind} {ident} on {base} under this project's key.\n"
                f"An id from another engine or another project fails exactly like this - "
                f"list what this key can see with pick_eval.py, no flags.",
                file=sys.stderr,
            )
        sys.exit(0 if status == 200 else 3)

    ds_status, ds_body = get(base, key, "/custom-agent-evaluations/datasets")
    st_status, st_body = get(base, key, "/custom-agent-evaluations/evaluation-settings")
    if ds_status != 200:
        die(f"datasets list returned {ds_status} - wrong key or wrong engine at {base}", 2)
    if st_status != 200:
        die(f"evaluation-settings list returned {st_status} - wrong key or wrong engine at {base}", 2)

    datasets = [summarize_dataset(r) for r in (ds_body.get("datasets") or [])]
    settings = [summarize_settings(r) for r in (st_body.get("evaluationSettings") or [])]

    if args.json:
        print(json.dumps({
            "base_url": base,
            "datasets": datasets[: args.limit],
            "dataset_count": len(datasets),
            "evaluation_settings": settings[: args.limit],
            "evaluation_settings_count": len(settings),
        }, indent=2))
        return

    print(f"engine: {base}")
    print(f"\ndatasets ({len(datasets)}):")
    if not datasets:
        print("  none - this project has no datasets yet. A template, a file, or live traces can seed one.")
    for d in datasets[: args.limit]:
        variants = f" +{d['smokeTestVariants']} variant(s)" if d["smokeTestVariants"] else ""
        print(f"  {d['id']}  {d['name']}  ({d['cases']} case(s){variants})")
    print(f"\nevaluation settings ({len(settings)}):")
    for s in settings[: args.limit]:
        mark = "  [default]" if s["isDefault"] else ""
        print(f"  {s['id']}  {s['name']}{mark}")
    if not settings:
        print("  none")


if __name__ == "__main__":
    main()
