#!/usr/bin/env python3
"""Create an evaluation dataset on a self-host engine, four ways.

  make_dataset.py --template customer-support            # one of the skill's shipped templates
  make_dataset.py --from-json cases.json                 # a builder-shaped JSON file
  make_dataset.py --from-csv cases.csv --name "My set"   # query,expected_results columns
  make_dataset.py --preview-trace <traceId>              # draft a case from a real trace
  make_dataset.py --preview-session <sessionId>          #   ... or a whole session
  make_dataset.py --suggest-expected --query "..."       # draft a reference answer
  make_dataset.py --add-case <datasetId> --query "..." --trace-id <t>   # append a curated case
  make_dataset.py --create-settings "Strict grading" --acceptance "..."  # a standalone grading config

Every creation writes a PERMANENT row - the v1 API has no dataset DELETE - so creation is
idempotent by name: if a dataset with the same name already exists in this project, its id is
returned and nothing is written. `--dry-run` prints the exact payload instead of POSTing it.

The from-traces path is a three-step contract shared with the dashboard's Add-to-dataset
dialog: `--preview-trace`/`--preview-session` builds the case server-side, a human approves or
edits it, `--add-case` appends the result (deduped server-side). `--suggest-expected` drafts
the reference answer for a case that has none.

Auth: AGENTX_API_KEY / AGENTX_API_BASE_URL, falling back to ./.env.agentx. Nothing derived
from the key is printed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_BASE = "http://localhost:4700/api/v1"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def die(msg: str, code: int = 1) -> "NoReturn":  # noqa: F821
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def load_env_file(path: str) -> dict:
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
    filed = load_env_file(env_file)
    key = os.environ.get("AGENTX_API_KEY") or filed.get("AGENTX_API_KEY") or ""
    base = (
        os.environ.get("AGENTX_API_BASE_URL")
        or filed.get("AGENTX_API_BASE_URL")
        or DEFAULT_BASE
    ).rstrip("/")
    if not key:
        die(f"no AGENTX_API_KEY in the environment or {env_file}.")
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


def call(base: str, key: str, method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        checked_url(base + path),
        data=data,
        method=method,
        headers={"x-api-key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 - checked_url() allowlists the scheme
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
            body = {"error": str(e)}
        return e.code, body
    except urllib.error.URLError as e:
        die(f"cannot reach {base} ({e.reason}). Is the engine running?", 2)


def validate_payload(payload: dict) -> None:
    if not isinstance(payload.get("name"), str) or not payload["name"].strip():
        die("dataset payload needs a non-empty 'name'")
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        die("dataset payload needs a non-empty 'questions' list")
    for i, q in enumerate(questions):
        main = (q or {}).get("main_question") or {}
        if not isinstance(main.get("query"), str) or not main["query"].strip():
            die(f"questions[{i}].main_question.query is missing or empty")


def existing_by_name(base: str, key: str, name: str) -> dict | None:
    status, body = call(base, key, "GET", "/custom-agent-evaluations/datasets")
    if status != 200:
        die(f"datasets list returned {status} - wrong key or wrong engine at {base}", 2)
    for row in body.get("datasets") or []:
        if (row.get("name") or "").strip() == name.strip():
            return row
    return None


def create(base: str, key: str, payload: dict, dry_run: bool, as_json: bool) -> None:
    validate_payload(payload)
    if dry_run:
        print(json.dumps(payload, indent=2))
        return

    found = existing_by_name(base, key, payload["name"])
    if found:
        ident = found.get("_id") or found.get("id")
        if as_json:
            print(json.dumps({"id": ident, "name": payload["name"], "created": False}))
        else:
            print(f"exists: '{payload['name']}' is already {ident} in this project - reusing it, nothing written.")
        return

    n_followups = sum(len(q.get("follow_up_questions") or []) for q in payload["questions"])
    if n_followups:
        print(
            f"note: {n_followups} follow_up_question(s) in this payload. The SDK's custom-agent "
            f"execute path asks main questions only - follow-ups are stored and shown in the "
            f"dashboard but will NOT be asked by a run-eval harness.",
            file=sys.stderr,
        )
    print(
        f"creating dataset '{payload['name']}' ({len(payload['questions'])} case(s)) - "
        f"this row is permanent, the API has no dataset DELETE.",
        file=sys.stderr,
    )
    status, body = call(base, key, "POST", "/custom-agent-evaluations/datasets", payload)
    if status not in (200, 201):
        die(f"create failed with {status}: {body.get('error', body)}", 2)
    ident = body.get("_id") or body.get("id")
    if as_json:
        print(json.dumps({"id": ident, "name": payload["name"], "created": True}))
    else:
        print(f"created: {ident}  '{payload['name']}'")


def payload_from_csv(path: str, args: argparse.Namespace) -> dict:
    if not args.name:
        die("--from-csv needs --name")
    p = Path(path)
    if not p.exists():
        die(f"no such file: {path}")
    questions = []
    # utf-8-sig: Excel exports open with a BOM, which otherwise renames the first
    # column to '\ufeffquery' and fails the required-column check confusingly.
    with p.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = set(reader.fieldnames or [])
        if "query" not in cols:
            die("CSV needs a 'query' column ('expected_results' is optional)")
        for i, row in enumerate(reader, start=2):
            query = (row.get("query") or "").strip()
            if not query:
                print(f"  skipping row {i}: empty query", file=sys.stderr)
                continue
            main = {"query": query}
            expected = (row.get("expected_results") or "").strip()
            if expected:
                main["expectedResults"] = expected
            questions.append({"main_question": main, "follow_up_questions": []})
    payload = {"name": args.name, "questions": questions}
    if args.description:
        payload["description"] = args.description
    if args.number_of_requests:
        payload["numberOfRequests"] = args.number_of_requests
    if args.acceptance:
        payload["acceptanceCriteria"] = args.acceptance
    if args.rejection:
        payload["rejectionCriteria"] = args.rejection
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=".env.agentx")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--dry-run", action="store_true", help="print the payload, POST nothing")

    src = ap.add_mutually_exclusive_group()
    src.add_argument("--template", metavar="NAME", help="a shipped template; --list-templates to see them")
    src.add_argument("--from-json", metavar="PATH", help="builder-shaped JSON file")
    src.add_argument("--from-csv", metavar="PATH", help="CSV with query[,expected_results]")
    src.add_argument("--preview-trace", metavar="TRACE_ID", help="draft a case from one trace")
    src.add_argument("--preview-session", metavar="SESSION_ID", help="draft a case from a session")
    src.add_argument("--suggest-expected", action="store_true", help="draft a reference answer for --query")
    src.add_argument("--add-case", metavar="DATASET_ID", help="append an approved case to a dataset")
    src.add_argument("--create-settings", metavar="NAME", help="create a standalone grading config (idempotent by name)")
    ap.add_argument("--list-templates", action="store_true")

    ap.add_argument("--name", help="dataset name (--from-csv, or to override --from-json)")
    ap.add_argument("--description")
    ap.add_argument("--number-of-requests", type=int)
    ap.add_argument("--acceptance", help="acceptance criteria")
    ap.add_argument("--rejection", help="rejection criteria")

    ap.add_argument("--query", help="for --suggest-expected / --add-case")
    ap.add_argument("--actual", help="the agent's actual output, context for --suggest-expected")
    ap.add_argument("--expected", help="expected results for --add-case")
    ap.add_argument("--trace-id", help="provenance for --add-case")
    ap.add_argument("--session-id", help="provenance for --add-case")
    args = ap.parse_args()

    for flag, value in (
        ("--template", args.template),
        ("--from-json", args.from_json),
        ("--from-csv", args.from_csv),
        ("--preview-trace", args.preview_trace),
        ("--preview-session", args.preview_session),
        ("--add-case", args.add_case),
        ("--create-settings", args.create_settings),
    ):
        if value is not None and not value.strip():
            die(f"{flag} got an empty value - a broken variable expansion looks exactly like this")

    if args.list_templates:
        for f in sorted(TEMPLATES_DIR.glob("*.json")):
            meta = json.loads(f.read_text(encoding="utf-8"))
            print(f"  {f.stem:20} {meta.get('name','?')}  ({len(meta.get('questions',[]))} case(s))")
        return

    key, base = resolve_auth(args.env_file)

    if args.template:
        if "/" in args.template or "\\" in args.template or ".." in args.template:
            die(f"template names are bare names, not paths. Shipped: "
                + ", ".join(sorted(p.stem for p in TEMPLATES_DIR.glob("*.json"))))
        path = TEMPLATES_DIR / f"{args.template}.json"
        if not path.exists():
            names = ", ".join(sorted(p.stem for p in TEMPLATES_DIR.glob("*.json")))
            die(f"no template '{args.template}'. Shipped: {names}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            die(f"template {path.name} is not valid JSON: {exc}")
        if args.name:
            payload["name"] = args.name
        create(base, key, payload, args.dry_run, args.json)
        return

    if args.from_json:
        p = Path(args.from_json)
        if not p.exists():
            die(f"no such file: {args.from_json}")
        try:
            payload = json.loads(p.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            die(f"{args.from_json} is not valid JSON: {exc}")
        if not isinstance(payload, dict):
            die(f"{args.from_json} must be a JSON OBJECT shaped like the POST body "
                f"(name, questions, ...), not a {type(payload).__name__}")
        if args.name:
            payload["name"] = args.name
        create(base, key, payload, args.dry_run, args.json)
        return

    if args.from_csv:
        create(base, key, payload_from_csv(args.from_csv, args), args.dry_run, args.json)
        return

    if args.preview_trace or args.preview_session:
        body = {"traceId": args.preview_trace} if args.preview_trace else {"sessionId": args.preview_session}
        status, out = call(base, key, "POST", "/custom-agent-evaluations/datasets/case-preview", body)
        if status != 200:
            die(f"case-preview returned {status}: {out.get('error', out)}", 3)
        print(json.dumps(out, indent=2))
        return

    if args.suggest_expected:
        if not args.query:
            die("--suggest-expected needs --query")
        body = {"query": args.query}
        if args.actual:
            body["actualOutput"] = args.actual
        status, out = call(base, key, "POST", "/custom-agent-evaluations/datasets/suggest-expected", body)
        if status != 200:
            die(f"suggest-expected returned {status}: {out.get('error', out)}", 3)
        print(json.dumps(out, indent=2))
        return

    if args.create_settings:
        name = args.create_settings.strip()
        status, body = call(base, key, "GET", "/custom-agent-evaluations/evaluation-settings")
        if status != 200:
            die(f"evaluation-settings list returned {status} - wrong key or wrong engine at {base}", 2)
        for row in body.get("evaluationSettings") or []:
            if (row.get("name") or "").strip() == name:
                ident = row.get("_id") or row.get("id")
                if args.json:
                    print(json.dumps({"id": ident, "name": name, "created": False}))
                else:
                    print(f"exists: '{name}' is already {ident} - reusing it, nothing written.")
                return
        payload = {"name": name, "status": "published"}
        if args.description:
            payload["description"] = args.description
        if args.number_of_requests:
            payload["numberOfRequests"] = args.number_of_requests
        if args.acceptance:
            payload["acceptanceCriteria"] = args.acceptance
        if args.rejection:
            payload["rejectionCriteria"] = args.rejection
        if args.dry_run:
            print(json.dumps(payload, indent=2))
            return
        print(f"creating grading config '{name}' - this row is permanent.", file=sys.stderr)
        status, body = call(base, key, "POST", "/custom-agent-evaluations/evaluation-settings", payload)
        if status not in (200, 201):
            die(f"create-settings failed with {status}: {body.get('error', body)}", 2)
        ident = body.get("_id") or body.get("id")
        if args.json:
            print(json.dumps({"id": ident, "name": name, "created": True}))
        else:
            print(f"created: {ident}  '{name}'")
        return

    if args.add_case:
        if not args.query:
            die("--add-case needs --query")
        if not (args.trace_id or args.session_id):
            die("--add-case needs --trace-id or --session-id (provenance is required; for hand-written cases build the dataset with --from-json/--from-csv instead)")
        case = {
            "main_question": {"query": args.query, "expectedResults": args.expected or None},
            "follow_up_questions": [],
            "source": {},
        }
        if args.trace_id:
            case["source"]["traceId"] = args.trace_id
        if args.session_id:
            case["source"]["sessionId"] = args.session_id
        print("appending a case - dataset rows are permanent.", file=sys.stderr)
        status, out = call(
            base, key, "POST",
            f"/custom-agent-evaluations/datasets/{urllib.parse.quote(args.add_case.strip(), safe='')}/cases",
            {"case": case, "dedupe": True},
        )
        if status == 409:
            # Dedupe refusing a repeat is the three-step contract working, not a failure.
            print(f"duplicate: this case is already in {args.add_case} - nothing written.")
            return
        if status not in (200, 201):
            die(f"add-case returned {status}: {out.get('error', out)}", 3)
        print(json.dumps(out, indent=2))
        return

    ap.print_help()


if __name__ == "__main__":
    main()
