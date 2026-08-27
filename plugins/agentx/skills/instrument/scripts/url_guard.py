#!/usr/bin/env python3
"""The scheme allowlist every request in this skill passes through, in one place.

The base URL arrives from the environment, an env file or a flag, and `urlopen` honours
`file:`, `ftp:` and custom schemes as readily as `http`. Unchecked, a mistyped base turns
a request into a local file read - and the project API key is attached either way.

Imported by the scripts beside it, which are run by path (`python3 <skill>/scripts/
verify_trace.py`), so the interpreter puts this directory on `sys.path` and a plain
`from url_guard import checked_url` resolves with no packaging and no path juggling.

`raises` is a parameter because the two callers catch different things: agentx_key.py
treats a bad address as a `ConnectionError`, alongside every other way the engine can be
unreachable, while verify_trace.py checks the resolved read URL at its own boundary and
catches `ValueError` there. Hard-coding either one would break the other's handler.

Runnable on its own, which is the fastest way to answer "why is my base URL refused":

    python3 url_guard.py http://localhost:4700/api/v1     # exit 0, prints the URL
    python3 url_guard.py file:///etc/passwd               # exit 2, prints the refusal
"""

from __future__ import annotations

import argparse
import sys
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")

DEFAULT_HINT = "AGENTX_API_BASE_URL and any env file"


class UrlSchemeRefused(ValueError):
    """A URL that would have carried the key somewhere urlopen should not take it."""


def checked_url(url: str, *, raises=UrlSchemeRefused, hint: str = DEFAULT_HINT) -> str:
    """Return *url*, or raise because its scheme is not http/https."""
    if urlsplit(url).scheme not in ALLOWED_SCHEMES:
        raise raises(
            f"refusing to send credentials to {url!r}: only http:// and https:// are allowed. "
            f"Check {hint}."
        )
    return url


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url", help="the base URL or full request URL to check")
    args = ap.parse_args()
    try:
        print(checked_url(args.url))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
