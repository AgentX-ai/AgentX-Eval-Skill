#!/usr/bin/env python3
"""
Pull an evaluation and its AI Analysis out of an AgentX self-host engine.

    python3 fetch_analysis.py --list
    python3 fetch_analysis.py <evaluation_id>
    python3 fetch_analysis.py <evaluation_id> --write-export eval-analysis/exports/
    python3 fetch_analysis.py <evaluation_id> --analyze          # spends judge calls
    python3 fetch_analysis.py --list --host agentx                      # hosted
    python3 fetch_analysis.py --list --host https://evals.example.com   # elsewhere

The engine is local unless you say otherwise. `HOST` is the one knob for that, and
two engines answer to a name: `local` (http://localhost:4700, the default) and
`agentx` (https://api.agentx.so, the hosted platform - a different API dialect, see
HOSTED_HOSTNAMES). Anything else is an address: a LAN box, a container, a tunnel, a
shared engine behind TLS. Most specific wins: `--base-url`, then `--host`, then
`$AGENTX_API_BASE_URL`, `$AGENTX_HOST`, `$HOST`.

Talks HTTP to the engine directly and needs nothing but the standard library: no
`agentx-python`, no `python-dotenv`, no virtualenv. That is deliberate. This runs
before the repo under test has been bootstrapped, and making the first step of a
triage depend on the SDK of the thing being triaged is how a run dies at step one.

Three things about self-host that shape everything below:

**The analysis is not on the SDK router.** `client.evaluations.get_report()` hits
`/custom-agent-evaluations/runs/{id}/report`, which self-host does not implement —
it 404s. Self-host's Analyze lives on the dashboard router instead, at
`/evaluate/analyze/{id}`, and the finished narrative is served inline on
`GET /evaluate/{id}`. This script reads the dashboard router.

**Nothing generates an analysis on its own.** On the hosted platform a run has a
report waiting for you. Here, `analysis` is absent until a human presses Analyze
or something calls `POST /evaluate/analyze/{id}`. That call is a real judge pass
over a sample of the run, billed to whichever provider key the engine holds, so
this script never makes it implicitly — it tells you the command, or runs it when
you pass --analyze.

**Keys are per project, and the config file lies.** Each project in the projects
table has its own key and its own data; a key resolves the project, and an
evaluation is invisible to every other key. `~/.agentx/config.json` holds a key
written by whichever engine last ran on this machine, which is not necessarily
the engine on the port you are talking to — a Docker instance keeps its database
in its own volume and mints its own keys. So every candidate key is verified with a
real authenticated read before it is used, and a key that does not work is reported
as a key problem rather than surfacing as a 401 several steps later.

**The engine will hand you a key, on one row of the matrix.** `GET /api/v1/auth/config`
is unauthenticated and exists in both auth modes — it is how the dashboard decides
between login, owner setup, and no-auth at all. Under the default
`AGENTX_AUTH=disabled` it also returns the default project's key outright, which is
what lets a fresh install land on a working screen with nothing to paste. Under
`AGENTX_AUTH=enabled` no key is ever returned, and the hosted platform serves no such
route. Its predecessor `GET /dev/bootstrap` was removed, with a test asserting it 404s;
this route replaced it rather than dropping the idea. It is the *last* candidate below,
because it is always the **default** project — right for a fresh install, wrong for
anyone who has already chosen where their evaluations live.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# The two engines worth having names for. Everything else is typed in full, but these
# two are what people are actually choosing between, so they answer to a word - and the
# choice is offered as exactly local / agentx / anything-you-type.
KNOWN_HOSTS = {
    "local": "http://localhost:4700",
    "agentx": "https://api.agentx.so",
}

# Words people reach for meaning the same two. Not advertised, just not a dead end:
# without them `--host cloud` would be read as a hostname and fail as an unreachable box.
_HOST_SYNONYMS = {
    "cloud": "agentx",
    "hosted": "agentx",
    "platform": "agentx",
    "localhost": "local",
}
DEFAULT_HOST = KNOWN_HOSTS["local"]
DEFAULT_PORT = 4700
DEFAULT_BASE_URL = f"{DEFAULT_HOST}/api/v1"

# api.agentx.so is the hosted platform, whose API is a different dialect from the
# self-host engine this script reads: the analysis lives on another router and ids are
# hex rather than nanoids. Pointing here is allowed - it is a real address people have -
# but the checks below say so plainly instead of letting the difference surface as a
# mystery 404 several calls in.
HOSTED_HOSTNAMES = {"api.agentx.so"}

# nanoid's default alphabet and length. Nothing enforces 21 characters at the API,
# so the bound is loose; the point is only to reject an id that was pasted out of
# the wrong field before it becomes a 404 later on.
_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_HOSTED_ID = re.compile(r"[0-9a-f]{24}")

# The engine's API root, wherever it has been mounted.
_API_ROOT = re.compile(r"/api/v\d+/?$")


class EngineError(RuntimeError):
    """Anything that means "stop and tell the user", with the fix in the message."""


ALLOWED_SCHEMES = ("http", "https")


def checked_url(url: str) -> str:
    """Refuse anything but http/https before the API key rides along with the request.

    The base URL arrives from the environment, an env file or a flag, and urlopen honours
    file:, ftp: and custom schemes as readily as http. Unchecked, a mistyped base turns a
    request into a local file read - and the key is attached either way.
    """
    if urlsplit(url).scheme not in ALLOWED_SCHEMES:
        raise EngineError(
            f"refusing to send credentials to {url!r}: only http:// and https:// are allowed. "
            "Check --host, AGENTX_API_BASE_URL and any env file."
        )
    return url


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------


def normalize_host(value: str) -> str:
    """Turn whatever someone put in HOST into an origin this script can talk to.

    All four of `http://localhost:4700`, `localhost:4700`, `my-box` and
    `https://evals.example.com` are things people write, and only two of them are
    addresses already. A missing scheme means the port is usually missing too - that
    is someone naming a machine rather than an address - so both defaults come from
    the local engine's shape. A scheme that is present is left entirely alone: a
    `https://` host behind a reverse proxy is on 443 and inventing :4700 for it would
    turn a working address into a hang.
    """
    host = value.strip().rstrip("/")
    # A name stands in for the address, so `--host agentx` and the literal
    # https://api.agentx.so are the same request.
    name = host.lower()
    host = KNOWN_HOSTS.get(_HOST_SYNONYMS.get(name, name), host)
    if "://" not in host:
        parts = urlsplit(f"http://{host}")
        # Read the port off the text rather than parts.port, which raises on a
        # malformed one - and a malformed port is still a port the user typed, so
        # appending another would only make the eventual error less readable.
        has_port = ":" in parts.netloc.rsplit("@", 1)[-1].rsplit("]", 1)[-1]
        netloc = parts.netloc if has_port else f"{parts.netloc}:{DEFAULT_PORT}"
        host = parts._replace(netloc=netloc).geturl()
    return host


def resolve_host(explicit: str | None) -> tuple[str, str]:
    """Returns (origin, where it came from). Unset is localhost, which is the whole
    point of the default: the common case needs no configuration, and anything else is
    you saying where the engine actually is."""
    if explicit:
        return normalize_host(explicit), "--host"

    from_env = (os.getenv("AGENTX_HOST") or "").strip()
    if from_env:
        return normalize_host(from_env), "$AGENTX_HOST"

    # A bare HOST is honoured too, because that is the name people reach for, but only
    # when it carries a scheme. `HOST=0.0.0.0` and `HOST=127.0.0.1` are what a dev
    # server, a container image or a Procfile sets for its own listener, and those land
    # in the environment of anything started beside them. Following one silently would
    # point this at a port nothing is serving and report it as the engine being down.
    bare = (os.getenv("HOST") or "").strip()
    if bare:
        # A scheme, or one of the names - `HOST=agentx` is unambiguous in a way that
        # `HOST=0.0.0.0` is not, so it does not need the scheme to be believed.
        known = bare.lower() in KNOWN_HOSTS or bare.lower() in _HOST_SYNONYMS
        if "://" in bare or known:
            return normalize_host(bare), "$HOST"
        print(
            f"note: ignoring HOST={bare!r} - no scheme, so it is more likely some other\n"
            f"      process's listen address than an AgentX engine. Write it as\n"
            f"      HOST=http://{bare}, or use AGENTX_HOST={bare}, to mean this engine.",
            file=sys.stderr,
        )
    return DEFAULT_HOST, "default"


def resolve_base_url(explicit: str | None, host: str | None = None) -> tuple[str, str]:
    """Returns (base url, where it came from).

    Most specific wins: an explicit base url names the router as well as the address,
    so it beats a host, which names only the address. `$AGENTX_API_BASE_URL` sits above
    the host variables for the same reason, and because a repo that already exports it
    is a repo whose harness and whose triage should agree on where they are pointed.
    """
    source = "--base-url"
    base = (explicit or "").strip()
    if not base and host:
        base, source = normalize_host(host), "--host"
    if not base:
        base, source = (os.getenv("AGENTX_API_BASE_URL") or "").strip(), "$AGENTX_API_BASE_URL"
    if not base:
        base, source = resolve_host(None)
    base = base.rstrip("/")

    # A repo already pointed at self-host may hold either form, because the Python
    # SDK appends /custom-agent-evaluations to whatever AGENTX_API_BASE_URL says
    # (agentx/evaluations/client.py). Strip it: each call below names its own router.
    if base.endswith("/custom-agent-evaluations"):
        base = base[: -len("/custom-agent-evaluations")]

    # A bare origin is the other thing people paste, since that is what the browser
    # shows them - and it is all a HOST usually is.
    path = urlsplit(base).path
    if path in ("", "/"):
        base = f"{base}/api/v1"
    elif source not in ("--base-url", "$AGENTX_API_BASE_URL") and not _API_ROOT.search(path):
        # A host given with a path prefix is a host behind a reverse proxy that mounts
        # the engine under a subpath - `https://tools.example.com/agentx`. The API root
        # still hangs off it, so the prefix is kept and /api/v1 goes on the end. Only
        # host-sourced values get this: a base url naming a path meant that path.
        base = f"{base}/api/v1"
    return base, source


def is_hosted(base_url: str) -> bool:
    """Whether this address is the hosted platform rather than a self-host engine."""
    return (urlsplit(base_url).hostname or "").lower() in HOSTED_HOSTNAMES


def _key_works(base_url: str, key: str) -> bool:
    """One cheap authenticated read. A key that cannot list evaluations cannot do anything
    else here either, so this is the whole validity test."""
    try:
        _request(base_url, key, "/evaluate/list?limit=1")
        return True
    except EngineError:
        return False


def resolve_api_key(explicit: str | None, base_url: str) -> tuple[str, str]:
    """Returns (key, where it came from).

    Every fetched candidate is *verified* against the engine before being returned, and the
    order runs from most specific intent to least: something the user typed, then something
    their shell remembers, then a file recording whichever engine last ran here, then the
    engine's own default. The old order put the engine's handout first, which meant a machine
    that had ever run a different engine silently used that engine's key and surfaced a 401
    several steps later, pointing at the evaluation id rather than at the key.

    Guessing wrong is worse than not guessing - but not guessing at all was its own cost.
    `GET /auth/config` is unauthenticated and returns the default project's key under the
    default `AGENTX_AUTH=disabled`, so a cold start on a fresh engine needs no exported
    variable and nothing pasted. It is last precisely because it is always the *default*
    project.
    """
    if explicit:
        return explicit, "--api-key"

    tried: list[str] = []

    from_env = os.getenv("AGENTX_API_KEY")
    if from_env:
        # Trusted without a probe: an explicitly exported key is a statement of intent, and
        # failing it here would hide a genuine engine problem behind a key-resolution error.
        return from_env, "AGENTX_API_KEY"

    config = Path(os.getenv("AGENTX_HOME", str(Path.home() / ".agentx"))) / "config.json"
    if config.is_file():
        try:
            key = json.loads(config.read_text(encoding="utf-8")).get("apiKey")
        except (ValueError, OSError):
            key = None
        if key:
            if _key_works(base_url, key):
                return key, str(config)
            tried.append(
                f"{config} holds a key the engine on {base_url} rejects - that file records "
                f"whichever engine last ran on this machine, not this one"
            )

    # The engine's own handout. Unauthenticated, and present in both auth modes - but it
    # carries a key only under AGENTX_AUTH=disabled, and the hosted platform has no such
    # route at all, so an absent key here is information rather than a failure.
    auth_mode = None
    try:
        payload = _request(base_url, None, "/auth/config")
        if isinstance(payload, dict):
            auth_mode = payload.get("mode")
            key = payload.get("apiKey")
            if key and _key_works(base_url, key):
                return key, "GET /auth/config (this engine's default project)"
            if key:
                tried.append("GET /auth/config returned a key this engine then rejected")
            elif auth_mode == "enabled":
                tried.append(
                    "this engine runs with AGENTX_AUTH=enabled, which hands out no key - "
                    "sign in to the dashboard and copy the project's key"
                )
    except EngineError:
        tried.append("GET /auth/config is not served here (not a self-host engine, or too old)")

    # Legacy engines only. Kept because it costs one request and still works against an
    # older self-host build that predates /auth/config.
    if auth_mode is None:
        try:
            payload = _request(base_url, None, "/dev/bootstrap")
            if isinstance(payload, dict) and payload.get("apiKey"):
                return payload["apiKey"], "GET /dev/bootstrap (legacy engine)"
        except EngineError:
            tried.append("GET /dev/bootstrap is not served by this engine either (removed "
                         "deliberately; /auth/config replaced it)")

    detail = "".join(f"\n  - {t}" for t in tried)
    # Where to look differs by engine, and sending someone to a startup log that does not
    # exist for their deployment is the kind of "help" that costs ten minutes.
    where = (
        "  - app.agentx.so, under your workspace settings\n"
        if is_hosted(base_url)
        else "  - the engine's startup output: 'Default project API key: agtx_local_...'\n"
             "  - or the dashboard's project settings\n"
    )
    raise EngineError(
        f"no usable API key for {base_url}.{detail}\n"
        f"Keys are per project. This engine did not hand one out, so get it from:\n"
        f"{where}"
        f"then export AGENTX_API_KEY=<key> (or pass --api-key)."
    )


def _request(
    base_url: str,
    api_key: str | None,
    path: str,
    *,
    method: str = "GET",
    body: Any = None,
    timeout: float = 30.0,
) -> Any:
    url = checked_url(f"{base_url}{path}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if api_key:
        req.add_header("x-api-key", api_key)
    if data is not None:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - checked_url() allowlists the scheme
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        if exc.code == 401:
            raise EngineError(
                f"401 from {path}. Self-host keys are per project and an evaluation is "
                f"only visible to the key of the project that owns it. List them with:\n"
                f"  curl -s {base_url}/projects"
            ) from exc
        if exc.code == 404 and path.startswith("/evaluate/") and not path.startswith("/evaluate/list"):
            # Two causes, and they used to be one. An evaluation lives in one project
            # on one engine, so a 404 means the wrong key or the wrong box - and now
            # that the box is configurable, naming only the key sends people hunting
            # through projects on an engine that never had the run.
            if is_hosted(base_url):
                raise EngineError(
                    f"404 from {path} on {base_url}, which is the hosted platform.\n"
                    f"This script reads self-host's dashboard router, and the hosted API "
                    f"serves the evaluation under a different one - so a 404 here is as "
                    f"likely to mean the route does not exist as that the id is wrong. If "
                    f"the evaluation you want is on a self-host engine, point --host at "
                    f"it.\n{detail}"
                ) from exc
            raise EngineError(
                f"404 from {path} on {base_url}.\n"
                f"An evaluation belongs to one project on one engine, so either the key "
                f"selects a different project or this is a different engine than the one "
                f"that ran it. `--list` shows what this key can see here; `--host "
                f"<address>` points at another engine.\n{detail}"
            ) from exc
        if exc.code == 404:
            raise EngineError(f"404 from {path}: {detail}") from exc
        raise EngineError(f"HTTP {exc.code} from {path}: {detail}") from exc
    except urllib.error.URLError as exc:
        # Telling someone to run `agentx-server --dev` when they are pointed at a URL on
        # the internet is advice for a different problem, so the remedy follows the kind
        # of address rather than being one sentence for both.
        if is_hosted(base_url) or urlsplit(base_url).scheme == "https":
            raise EngineError(
                f"cannot reach {base_url} ({exc.reason}). Check the address is right and that this machine can reach it - a VPN, a firewall or a proxy between you and it will "
                f"look exactly like this. `--host local` reads a self-host engine on this machine."
            ) from exc
        raise EngineError(
            f"cannot reach the engine at {base_url} ({exc.reason}). Start it with "
            f"`agentx-server --dev`, or point --host (or $AGENTX_HOST) at wherever it "
            f"is actually listening - it does not have to be this machine."
        ) from exc
    except ValueError as exc:
        raise EngineError(f"{path} did not return JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def list_evaluations(base_url: str, api_key: str, limit: int) -> list[dict[str, Any]]:
    payload = _request(base_url, api_key, f"/evaluate/list?limit={limit}") or {}
    rows = []
    for ev in payload.get("evaluations", []):
        dataset = ev.get("datasetId")
        stats = ev.get("liveStatistics") or {}
        rows.append(
            {
                "evaluation_id": ev.get("_id", ""),
                "dataset": dataset.get("name") if isinstance(dataset, dict) else dataset,
                "status": ev.get("status"),
                "rated": stats.get("ratedCount"),
                "average": stats.get("averageRating"),
                "analyzed": bool((ev.get("analysis") or {}).get("analysis")),
                "created_at": ev.get("createdAt"),
            }
        )
    return rows


def _case(question: Any, index: int) -> dict[str, Any]:
    """One dataset question, flattened.

    The query and its grading fields hang off `main_question`, not off the question
    itself, and a multi-step case carries more of them in `follow_up_questions`.
    Reading the outer object directly returns empties, which looks exactly like a
    dataset with no expected results.
    """

    def step(node: Any) -> dict[str, Any]:
        node = node or {}
        smoke = node.get("smokeTest") or {}
        return {
            "query": node.get("query", ""),
            "expected_results": node.get("expectedResults"),
            "judge_guideline": node.get("judgeGuideline"),
            "smoke_test_count": smoke.get("count") if smoke.get("enabled") else None,
        }

    question = question or {}
    case: dict[str, Any] = {"index": index}
    case.update(step(question.get("main_question") or question))
    follow_ups = question.get("follow_up_questions") or []
    if follow_ups:
        case["follow_ups"] = [step(f.get("main_question") or f) for f in follow_ups]
    return case


def _result(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_index": row.get("questionIndex"),
        "run_number": row.get("runNumber"),
        "query": row.get("questionText", ""),
        "expected_results": row.get("expectedResults"),
        "response": row.get("responseMessage", ""),
        "rating": row.get("rating"),
        "justification": row.get("justification", ""),
        "is_smoke_test_variant": row.get("isSmokeTestVariant", False),
        "smoke_test_variant_text": row.get("smokeTestVariantText"),
        "trace_id": row.get("traceId"),
        "latency_ms": row.get("latencyMs"),
        "similarity": {
            "vector": row.get("vectorSimilarity"),
            "jaccard": row.get("jaccardSimilarity"),
            "bleu": row.get("bleuScore"),
            "rouge": row.get("rougeScore"),
        },
        "code_scorers": row.get("codeScorerResults") or [],
    }


WORST_CASE_COUNT = 10


def collect(base_url: str, api_key: str, evaluation_id: str) -> dict[str, Any]:
    evaluation = _request(base_url, api_key, f"/evaluate/{evaluation_id}") or {}

    dataset_ref = evaluation.get("datasetId")
    dataset_id = dataset_ref.get("_id") if isinstance(dataset_ref, dict) else dataset_ref
    dataset_name = dataset_ref.get("name") if isinstance(dataset_ref, dict) else ""

    # The dataset is fetched even though /evaluate/{id} already embeds a grading config,
    # because the two are not the same object and the difference is the whole point of
    # the Grading criteria section below. See `grading` for which one actually graded.
    dataset: dict[str, Any] = {}
    if dataset_id:
        try:
            dataset = _request(base_url, api_key, f"/custom-agent-evaluations/datasets/{dataset_id}") or {}
        except EngineError as exc:
            print(f"warning: could not fetch dataset {dataset_id}: {exc}", file=sys.stderr)

    # 404 here just means nobody has analyzed this run yet, which is the normal state
    # of a fresh run and is reported as such rather than treated as a failure.
    metrics: dict[str, Any] = {}
    try:
        metrics = _request(base_url, api_key, f"/evaluate/analyze/{evaluation_id}/metrics") or {}
    except EngineError:
        metrics = {}

    settings = evaluation.get("evaluationSettings") or {}
    narrative = (evaluation.get("analysis") or {}).get("analysis") or {}
    snapshot = (evaluation.get("analysis") or {}).get("statistics") or {}
    live = evaluation.get("liveStatistics") or {}

    # A standalone grading config (its own _id, created on the Evaluator tab) overrides
    # the dataset's own criteria for runs that name it, while expectedResults and
    # judgeGuideline still come from the dataset either way — engine's resolveRunConfig
    # takes `settings ?? dataset` for the criteria but always reads questions off the
    # dataset. So a run can be graded against strings the dataset never mentions, and a
    # triage that reads the dataset's criteria would be reading the wrong rubric.
    settings_id = settings.get("_id")
    standalone = bool(settings_id and dataset_id and settings_id != dataset_id)

    results = [_result(r) for r in evaluation.get("results", [])]
    rated = [r for r in results if r.get("rating") is not None]
    worst = sorted(rated, key=lambda r: r["rating"])[:WORST_CASE_COUNT]

    return {
        "source": {"kind": "selfhost", "base_url": base_url, "evaluation_id": evaluation_id},
        "evaluation_id": evaluation.get("_id", evaluation_id),
        "dataset_id": dataset_id or "",
        "dataset_name": dataset_name or dataset.get("name", ""),
        "dashboard_url": f"{base_url.rsplit('/api/', 1)[0]}/governance",
        "status": evaluation.get("status"),
        "run_source": evaluation.get("runSource"),
        "subject": evaluation.get("evaluationSubject") or {},
        "created_at": evaluation.get("createdAt"),
        "analysis_status": (evaluation.get("analysis") or {}).get("status", "not_started"),
        "baseline": {
            # Live, recomputed from the stored per-result ratings on every read. This is
            # the number to trust and to compare against; see `analysis_snapshot` for why
            # the analysis carries its own copy.
            "rated_count": live.get("ratedCount"),
            "average_score": live.get("averageRating"),
            "min_score": live.get("minRating"),
            "max_score": live.get("maxRating"),
            "rating_variance": snapshot.get("ratingVariance"),
            "consistency_score": narrative.get("consistencyScore"),
            "instruction_adherence": (narrative.get("instructionAdherence") or {}).get("score"),
        },
        # Computed when Analyze ran, from the same stored ratings, so the two agree unless
        # results landed afterwards. When they disagree, the analysis is stale, not wrong.
        "analysis_snapshot": {
            "number_of_runs": snapshot.get("numberOfRuns"),
            "average_score": snapshot.get("averageRating"),
            "min_score": snapshot.get("minRating"),
            "max_score": snapshot.get("maxRating"),
        }
        if snapshot
        else {},
        "summary": narrative.get("summary", ""),
        "strengths": (narrative.get("overallAssessment") or {}).get("strengths", []),
        "weaknesses": (narrative.get("overallAssessment") or {}).get("weaknesses", []),
        "response_patterns": narrative.get("responsePatterns") or {},
        "reasoning_analysis": narrative.get("reasoningAnalysis") or {},
        "tool_usage_analysis": narrative.get("toolUsageAnalysis") or {},
        "instruction_adherence": narrative.get("instructionAdherence") or {},
        "recommendations": [
            {
                "number": i,
                "category": rec.get("category", ""),
                "priority": rec.get("priority"),
                "text": rec.get("recommendation", ""),
                "reasoning": rec.get("reasoning", ""),
            }
            for i, rec in enumerate(narrative.get("recommendations") or [], start=1)
        ],
        "grading": {
            "applied_from": "standalone evaluation settings" if standalone else "the dataset",
            "settings_id": settings_id,
            "settings_name": settings.get("name"),
            "acceptance_criteria": settings.get("acceptanceCriteria"),
            "rejection_criteria": settings.get("rejectionCriteria"),
            "evaluation_criteria": settings.get("evaluationCriteria"),
            "judge_prompt": settings.get("judgePrompt"),
            "judge_model": settings.get("judgeModel"),
            "number_of_requests": settings.get("numberOfRequests"),
            "similarity_metrics": {
                name: bool((settings.get(name) or {}).get("enabled"))
                for name in ("vectorSimilarity", "jaccardSimilarity", "bleuScore", "rougeScore")
            },
            "code_scorers": [
                {"name": s.get("name"), "enabled": s.get("enabled"), "code": s.get("code", "")}
                for s in (settings.get("codeScorers") or [])
            ],
            "dataset_criteria": {
                "acceptance_criteria": dataset.get("acceptanceCriteria"),
                "rejection_criteria": dataset.get("rejectionCriteria"),
                "evaluation_criteria": dataset.get("evaluationCriteria"),
            }
            if standalone
            else {},
            "cases": [_case(q, i) for i, q in enumerate(dataset.get("questions") or [])],
        },
        "results": results,
        "worst_cases": worst,
        "judge_evidence": metrics.get("judgeEvidence") or [],
        "judge_models": metrics.get("modelSnapshot") or {},
    }


def analyze(base_url: str, api_key: str, evaluation_id: str, judges: list[str], quality_mode: str) -> dict[str, Any]:
    """Runs Analyze and waits. Synchronous server-side: there is no job queue here, so
    the HTTP call itself holds open for the whole judge pass — up to (sampled items x
    judges) provider calls — and returns already terminal. Nothing to poll."""
    body: dict[str, Any] = {"qualityMode": quality_mode}
    if judges:
        body["judges"] = [{"model": m} for m in judges]
    return _request(base_url, api_key, f"/evaluate/analyze/{evaluation_id}", method="POST", body=body, timeout=900.0) or {}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_markdown(data: dict[str, Any]) -> str:
    out: list[str] = []
    add = out.append
    b = data["baseline"]
    g = data["grading"]

    add(f"# AI Analysis: {data.get('dataset_name') or 'evaluation'}\n")
    add(f"_Fetched from {data['source']['base_url']} for evaluation `{data['evaluation_id']}`._\n")

    add("## Identifiers\n")
    add("| Field | Value |")
    add("| --- | --- |")
    add(f"| Evaluation ID | `{data['evaluation_id']}` |")
    add(f"| Dataset ID | `{data['dataset_id']}` |")
    add(f"| Dataset | {data.get('dataset_name') or '-'} |")
    if g.get("settings_id"):
        add(f"| Grading config ID | `{g['settings_id']}` |")
    add(f"| Base URL | {data['source']['base_url']} |")
    add(f"| Analysis status | {data['analysis_status']} |")
    add("")

    subject = data.get("subject") or {}
    if subject:
        add("## Subject under test\n")
        add("| Field | Value |")
        add("| --- | --- |")
        for label, key in (("Kind", "kind"), ("Name", "displayName"), ("Framework", "framework"), ("Runtime", "runtime")):
            if subject.get(key):
                add(f"| {label} | {subject[key]} |")
        if subject.get("metadata"):
            add(f"| Metadata | `{json.dumps(subject['metadata'])}` |")
        add("")

    add("## Statistics\n")
    add("| Metric | Value |")
    add("| --- | --- |")
    for label, key in (
        ("Rated results", "rated_count"),
        ("Average score", "average_score"),
        ("Min score", "min_score"),
        ("Max score", "max_score"),
        ("Rating variance", "rating_variance"),
        ("Consistency score", "consistency_score"),
        ("Instruction adherence", "instruction_adherence"),
    ):
        if b.get(key) is not None:
            add(f"| {label} | {_fmt(b[key])} |")
    add("")
    add("Read from `liveStatistics`, recomputed from the stored per-result ratings on")
    add("every read.\n")

    snapshot = data.get("analysis_snapshot") or {}
    if snapshot.get("average_score") is not None and b.get("average_score") is not None:
        if abs(snapshot["average_score"] - b["average_score"]) > 0.005:
            add(
                f"**Stale analysis.** It was written against an average of "
                f"{_fmt(snapshot['average_score'])} over {snapshot.get('number_of_runs')} results; "
                f"the run now averages {_fmt(b['average_score'])} over {b.get('rated_count')}. "
                f"Results landed after Analyze ran, so its prose describes a subset. Re-run it.\n"
            )

    if data.get("summary"):
        add("## Summary\n")
        add(data["summary"] + "\n")

    if data.get("strengths") or data.get("weaknesses"):
        add("## Overall assessment\n")
        for heading, key in (("Strengths", "strengths"), ("Weaknesses", "weaknesses")):
            items = data.get(key) or []
            if items:
                add(f"### {heading}")
                for item in items:
                    add(f"- {item}")
                add("")

    for heading, key, fields in (
        ("Response patterns", "response_patterns", (("Similarities", "similarities"), ("Differences", "differences"), ("Outliers", "outliers"))),
        ("Reasoning", "reasoning_analysis", (("Patterns", "reasoningPatterns"), ("Gaps", "reasoningGaps"))),
        ("Tool usage", "tool_usage_analysis", (("Patterns", "patterns"), ("Issues", "issues"))),
    ):
        node = data.get(key) or {}
        if not node:
            continue
        add(f"## {heading}\n")
        for prose_key in ("cotQuality", "effectiveness"):
            if node.get(prose_key):
                add(node[prose_key] + "\n")
        for label, field in fields:
            items = node.get(field) or []
            if items:
                add(f"**{label}.**")
                for item in items:
                    add(f"- {item}")
                add("")

    adherence = data.get("instruction_adherence") or {}
    if adherence.get("analysis"):
        add("## Instruction adherence\n")
        add(f"**Score:** {_fmt(adherence.get('score'))}\n")
        add(adherence["analysis"] + "\n")
        for deviation in adherence.get("deviations") or []:
            add(f"- {deviation}")
        add("")

    # Before the recommendations on purpose: whoever reads a recommendation should
    # already know what the answers were graded against.
    add("## Grading criteria\n")
    add(f"These are the strings the judge scored against, taken from **{g['applied_from']}**")
    add(f"(`{g.get('settings_name') or g.get('settings_id')}`). A recommendation that conflicts")
    add("with them lowers the score, however sensible it sounds in isolation.\n")
    for label, key in (
        ("Acceptance criteria", "acceptance_criteria"),
        ("Rejection criteria", "rejection_criteria"),
        ("Evaluation criteria", "evaluation_criteria"),
    ):
        if g.get(key):
            add(f"**{label}.** {g[key]}\n")
    if g.get("judge_model"):
        add(f"**Judge model.** `{g['judge_model']}`\n")
    if g.get("judge_prompt"):
        add("**Judge prompt.**\n")
        add("```")
        add(g["judge_prompt"])
        add("```\n")
    if g.get("number_of_requests"):
        add(f"**Runs per question.** {g['number_of_requests']}\n")

    enabled_metrics = [name for name, on in (g.get("similarity_metrics") or {}).items() if on]
    if enabled_metrics:
        add(f"**Similarity metrics scored.** {', '.join(enabled_metrics)}\n")

    if g.get("code_scorers"):
        add("**Code scorers.** These execute against every answer and are part of the")
        add("grading surface, so they are frozen for the comparison like any criteria string.\n")
        for scorer in g["code_scorers"]:
            add(f"- `{scorer['name']}` ({'enabled' if scorer.get('enabled') else 'disabled'})")
        add("")

    if g.get("dataset_criteria") and any(g["dataset_criteria"].values()):
        add("### The dataset's own criteria, which did NOT grade this run\n")
        add("This run named a standalone grading config, so these were overridden. They are")
        add("here only so you do not triage against the wrong rubric by opening the dataset.\n")
        for label, key in (
            ("Acceptance criteria", "acceptance_criteria"),
            ("Rejection criteria", "rejection_criteria"),
            ("Evaluation criteria", "evaluation_criteria"),
        ):
            if g["dataset_criteria"].get(key):
                add(f"- **{label}.** {g['dataset_criteria'][key]}")
        add("")

    if g.get("cases"):
        add("### Test cases\n")
        add("`expectedResults` and `judgeGuideline` always come from the dataset, even when")
        add("a standalone config supplied the criteria above.\n")
        for case in g["cases"]:
            add(f"#### Case {case['index']}\n")
            add(f"- **Query.** {case['query']}")
            if case.get("expected_results"):
                add(f"- **Expected result.** {case['expected_results']}")
            if case.get("judge_guideline"):
                add(f"- **Judge guideline.** {case['judge_guideline']}")
            if case.get("smoke_test_count"):
                add(f"- **Smoke test variants.** {case['smoke_test_count']}")
            add("")

    if data.get("recommendations"):
        add("## Recommendations\n")
        for rec in data["recommendations"]:
            priority = f" - {rec['priority']} priority" if rec.get("priority") else ""
            add(f"### {rec['number']}. {rec['category']}{priority}\n")
            add(rec["text"] + "\n")
            if rec.get("reasoning"):
                add(f"_Reasoning: {rec['reasoning']}_\n")
    elif data["analysis_status"] == "not_started":
        add("## Recommendations\n")
        add("None: nobody has analyzed this run yet. Run it with `--analyze`, or")
        add(f"`POST {data['source']['base_url']}/evaluate/analyze/{data['evaluation_id']}`.\n")

    if data.get("worst_cases"):
        add("## Judge evidence: lowest-scoring cases\n")
        add("Stored per-result ratings, worst first, with the expected answer alongside.\n")
        for case in data["worst_cases"]:
            add(f"### Q{case['question_index']} run {case['run_number']} - rated {_fmt(case['rating'])}\n")
            add(f"- **Query.** {case['query']}")
            if case.get("expected_results"):
                add(f"- **Expected.** {case['expected_results']}")
            add(f"- **Answer.** {case['response'][:1500]}")
            if case.get("justification"):
                add(f"- **Judge said.** {case['justification']}")
            similarity = {k: v for k, v in (case.get("similarity") or {}).items() if v is not None}
            if similarity:
                add("- **Similarity.** " + ", ".join(f"{k} {_fmt(v)}" for k, v in similarity.items()))
            for scorer in case.get("code_scorers") or []:
                add(f"- **Code scorer `{scorer.get('name')}`.** {_fmt(scorer.get('score'))} — {scorer.get('reasoning') or scorer.get('error') or ''}")
            if case.get("is_smoke_test_variant"):
                add("- **Smoke test variant** of the original question, not the question itself.")
            if case.get("trace_id"):
                # The execution timeline behind this score: which tools were called, what came
                # back, how many turns. Worth pulling for any case whose failure is not obvious
                # from the answer alone, which is most of them.
                add(f"- **Trace.** `{case['trace_id']}` — `GET /ingest/traces/{case['trace_id']}`")
            add("")

    if data.get("judge_evidence"):
        add("## Multi-judge agreement\n")
        add("Each sampled answer was independently re-rated by the judges below. `finalScore`")
        add("is their average and is **not** the stored rating this run was scored on — a wide")
        add("spread marks a question the rubric leaves ambiguous, not a defect in the answer.\n")
        add("| Q | Run | finalScore | Agreement | Judges |")
        add("| --- | --- | --- | --- | --- |")
        for item in data["judge_evidence"]:
            judges = ", ".join(
                f"{j.get('judgeVariant')}={_fmt(j.get('rating'))}" for j in item.get("judges", [])
            )
            add(
                f"| {item.get('questionIndex')} | {item.get('runNumber')} | "
                f"{_fmt(item.get('finalScore'))} | {item.get('disagreementBand')} | {judges} |"
            )
        add("")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("evaluation_id", nargs="?", help="evaluation / run id (nanoid, e.g. oE1YMG5wqmu4j2bhTtw1X)")
    ap.add_argument("--list", action="store_true", help="list recent evaluations and exit")
    # One address, two ways to write it, so taking both at once could only mean the user
    # believes one of them is doing something it is not.
    where = ap.add_mutually_exclusive_group()
    where.add_argument("--host", help=f"engine address, or one of {', '.join(f'{k} ({v})' for k, v in KNOWN_HOSTS.items())} (default: $AGENTX_HOST, $HOST, or local)")
    where.add_argument("--base-url", help=f"engine API base, when it is not <host>/api/v1 (default: $AGENTX_API_BASE_URL)")
    ap.add_argument("--api-key", help="project API key (default: $AGENTX_API_KEY, then a verified ~/.agentx/config.json)")
    ap.add_argument("--write-export", type=Path, metavar="DIR", help="render a markdown export into DIR for the triage to read")
    ap.add_argument("--field", help="print one value bare, e.g. dataset_id")
    ap.add_argument("--analyze", action="store_true", help="run Analyze first if none exists (spends judge calls)")
    ap.add_argument("--force-analyze", action="store_true", help="re-run Analyze even if one already exists")
    ap.add_argument("--judges", default="", help="comma-separated judge models, up to 3 (default: the engine's)")
    ap.add_argument("--quality-mode", default="balanced", choices=("balanced", "quality_first"))
    args = ap.parse_args()

    try:
        base_url, base_url_source = resolve_base_url(args.base_url, args.host)
        api_key, key_source = resolve_api_key(args.api_key, base_url)

        # Which engine answered is the one thing that is invisible in every other line of
        # output and expensive in every direction when wrong, so it is stated every time
        # rather than only when something fails. It costs one line on stderr.
        print(f"engine: {base_url} (from {base_url_source}), key from {key_source}", file=sys.stderr)

        if args.list:
            rows = list_evaluations(base_url, api_key, 20)
            if not rows:
                print(f"no evaluations under the key from {key_source}.", file=sys.stderr)
                print(f"Other projects have their own: curl -s {base_url}/projects", file=sys.stderr)
                return 1
            print(f"{'evaluation id':<24} {'rated':>5} {'avg':>6}  {'analyzed':<9} dataset")
            for row in rows:
                print(
                    f"{row['evaluation_id']:<24} {str(row['rated'] or 0):>5} "
                    f"{_fmt(row['average']):>6}  {'yes' if row['analyzed'] else 'no':<9} {row['dataset'] or '-'}"
                )
            return 0

        if not args.evaluation_id:
            ap.error("an evaluation_id is required (or --list to find one)")

        evaluation_id = args.evaluation_id
        # The id's shape says which platform it came from, so it is only wrong relative to
        # where you are pointed. A hex id is correct against the hosted address and a
        # guaranteed 404 against a self-host engine, which is worth stopping for; the
        # reverse is worth a word rather than a refusal.
        if _HOSTED_ID.fullmatch(evaluation_id) and not is_hosted(base_url):
            print(
                f"{evaluation_id!r} is a 24-character hex id, which is a hosted AgentX id, "
                f"but {base_url} is a self-host engine, whose ids are nanoids. Find the "
                f"self-host id with --list, or point --host at the hosted platform.",
                file=sys.stderr,
            )
            return 2
        if is_hosted(base_url) and not _HOSTED_ID.fullmatch(evaluation_id):
            print(
                f"note: {base_url} is the hosted platform, whose ids are 24-character hex, "
                f"and {evaluation_id!r} is not one. Continuing, but expect a 404 if this id "
                f"came from a self-host engine.",
                file=sys.stderr,
            )
        if not _ID.fullmatch(evaluation_id):
            print(f"{evaluation_id!r} is not a valid evaluation id.", file=sys.stderr)
            return 2

        if args.force_analyze:
            print(f"running Analyze on {evaluation_id} (this spends judge calls)...", file=sys.stderr)
            outcome = analyze(base_url, api_key, evaluation_id, [j for j in args.judges.split(",") if j], args.quality_mode)
            print(f"Analyze finished: {outcome.get('status')}", file=sys.stderr)

        data = collect(base_url, api_key, evaluation_id)

        if args.analyze and not args.force_analyze and data["analysis_status"] == "not_started":
            print(f"no analysis yet, running one (this spends judge calls)...", file=sys.stderr)
            outcome = analyze(base_url, api_key, evaluation_id, [j for j in args.judges.split(",") if j], args.quality_mode)
            print(f"Analyze finished: {outcome.get('status')}", file=sys.stderr)
            data = collect(base_url, api_key, evaluation_id)

        if data["analysis_status"] == "not_started":
            print(
                f"note: this run has no analysis, so there are no recommendations to triage.\n"
                f"      The stored ratings, the rubric and the worst answers are all still here,\n"
                f"      which is enough to work from. To add the narrative, re-run with --analyze.",
                file=sys.stderr,
            )
        elif data["analysis_status"] == "failed":
            print("note: the last Analyze failed; only the stored ratings are available.", file=sys.stderr)

    except EngineError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.write_export:
        args.write_export.mkdir(parents=True, exist_ok=True)
        path = args.write_export / f"analysis_{data['evaluation_id']}.md"
        path.write_text(render_markdown(data), encoding="utf-8")
        print(f"wrote {path}", file=sys.stderr)
        if not data["grading"].get("acceptance_criteria"):
            print(
                "note: this run's grading config has no acceptance criteria, so the triage "
                "has less rubric to check recommendations against.",
                file=sys.stderr,
            )

    if args.field:
        value: Any = data
        for part in args.field.split("."):
            if not isinstance(value, dict) or part not in value:
                print(f"no such field: {args.field}", file=sys.stderr)
                return 1
            value = value[part]
        print(value)
    else:
        print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
