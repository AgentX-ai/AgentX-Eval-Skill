#!/usr/bin/env python3
"""The scheme allowlist every request in this skill passes through, in one place.

The base URL arrives from the environment, an env file or a flag, and `urlopen` honours
`file:`, `ftp:` and custom schemes as readily as `http`. Unchecked, a mistyped base turns
a request into a local file read - and the project API key is attached either way.

Imported by the scripts beside it, which are run by path (`python3 <skill>/scripts/
pick_eval.py`), so the interpreter puts this directory on `sys.path` and a plain
`from url_guard import checked_url` resolves with no packaging and no path juggling.

Runnable on its own, which is the fastest way to answer "why is my base URL refused":

    python3 url_guard.py http://localhost:4700/api/v1     # exit 0, prints the URL
    python3 url_guard.py file:///etc/passwd               # exit 2, prints the refusal
"""

from __future__ import annotations

import argparse
import sys
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")


class UrlSchemeRefused(Exception):
    """A URL that would have carried the key somewhere urlopen should not take it."""


def checked_url(url: str, die=None) -> str:
    """Return *url*, or refuse it because its scheme is not http/https.

    `die` is the caller's own exit path, so a refusal reads in the script's error idiom
    rather than as a traceback. Without one this raises, which is what the import-time
    users (and the tests) want.
    """
    if urlsplit(url).scheme not in ALLOWED_SCHEMES:
        message = (f"refusing to send credentials to {url}: only http:// and https:// "
                   "are allowed. Check AGENTX_API_BASE_URL and any env file.")
        if die is not None:
            die(message)
        raise UrlSchemeRefused(message)
    return url


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url", help="the base URL or full request URL to check")
    args = ap.parse_args()
    try:
        print(checked_url(args.url))
    except UrlSchemeRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
