#!/usr/bin/env python3
"""
Pull an improvement report (confirmed production failures, clustered into issues with
recommendations) out of an AgentX self-host engine.

    python3 fetch_report.py <report_id>
    python3 fetch_report.py --list
    python3 fetch_report.py <report_id> --host http://box:4700

Stdlib only, deliberately - this runs before the repo under improvement has been touched,
and the first step of a fix must not depend on the SDK of the thing being fixed.

Host resolution, most specific wins: --base-url, --host, $AGENTX_API_BASE_URL, $AGENTX_HOST,
$HOST, then http://localhost:4700. Key resolution: $AGENTX_API_KEY, then ~/.agentx/config.json,
then the engine's own GET /auth/config (which hands out the DEFAULT project's key on no-auth
installs - right for a fresh install, wrong if your failures live in another project, which is
why it is last). Every candidate key is verified with a real authenticated read first.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_BASE = "http://localhost:4700"
ALLOWED_SCHEMES = {"http", "https"}


def checked_url(url: str) -> str:
    """Refuse anything but http/https before the API key rides along with the request.

    The base URL arrives from the environment or a flag, and urlopen honours file:, ftp: and
    custom schemes as readily as http. Unchecked, a mistyped base turns a request into a local
    file read - and the key is attached either way.
    """
    if urlsplit(url).scheme not in ALLOWED_SCHEMES:
        sys.exit(
            f"refusing to send credentials to {url!r}: only http:// and https:// are allowed. "
            "Check --host, --base-url and $AGENTX_API_BASE_URL."
        )
    return url


def resolve_base(base_url: str | None, host: str | None) -> str:
    base = base_url or host
    base = base or os.environ.get("AGENTX_API_BASE_URL") or os.environ.get("AGENTX_HOST") or os.environ.get("HOST")
    base = (base or DEFAULT_BASE).rstrip("/")
    if not base.startswith("http"):
        base = f"http://{base}"
    if not base.endswith("/api/v1"):
        base = f"{base}/api/v1"
    return base


def get(base: str, path: str, key: str | None) -> dict:
    req = urllib.request.Request(checked_url(f"{base}{path}"))
    if key:
        req.add_header("x-api-key", key)
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - checked_url() allowlists the scheme
        return json.load(resp)


def resolve_key(base: str) -> str:
    candidates: list[str] = []
    if os.environ.get("AGENTX_API_KEY"):
        candidates.append(os.environ["AGENTX_API_KEY"])
    config = Path.home() / ".agentx" / "config.json"
    if config.exists():
        try:
            stored = json.loads(config.read_text()).get("api_key")
            if stored:
                candidates.append(stored)
        except (ValueError, OSError):
            pass
    try:
        handed = get(base, "/auth/config", None).get("apiKey")
        if handed:
            candidates.append(handed)
    except (urllib.error.URLError, ValueError):
        pass
    saw_http_response = False
    for key in candidates:
        try:
            get(base, "/agent-monitoring/improvement-groups", key)
            return key
        except urllib.error.HTTPError:
            saw_http_response = True  # the engine answered - this key just isn't accepted
            continue
        except urllib.error.URLError:
            continue
    if not saw_http_response:
        sys.exit(f"No engine answered at {base} - is it running? (HOST/--host pick the address)")
    sys.exit(
        "No working API key. Set AGENTX_API_KEY to the key of the project holding the "
        "confirmed failures (keys are per project - a key from another project sees nothing)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch an AgentX improvement report")
    parser.add_argument("report_id", nargs="?", help="the report id from Insights > Auto-improve")
    parser.add_argument("--list", action="store_true", help="list every report on the engine")
    parser.add_argument("--host", help="engine address (name or URL)")
    parser.add_argument("--base-url", dest="base_url", help="full API base, overrides --host")
    args = parser.parse_args()

    base = resolve_base(args.base_url, args.host)
    key = resolve_key(base)

    if args.list or not args.report_id:
        reports = get(base, "/agent-monitoring/improvement-reports", key).get("improvementReports", [])
        if not reports:
            groups = get(base, "/agent-monitoring/improvement-groups", key).get("improvementGroups", [])
            member_note = (
                f' The "{groups[0]["name"]}" group holds {groups[0]["memberCount"]} confirmed failure(s) - '
                f"generate a report from it in the dashboard (Insights > Auto-improve)."
                if groups and groups[0]["memberCount"]
                else " Confirm some signals in Review first - confirmed failures accumulate automatically."
            )
            print(f"No improvement reports yet.{member_note}")
            return
        for report in reports:
            print(f"{report['_id']}  {report['createdAt']}  {report['issueCount']} issue(s), {report['memberCount']} failures - {report['summary'][:80]}")
        return

    report = get(base, f"/agent-monitoring/improvement-reports/{args.report_id}", key)["report"]
    json.dump(report, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
