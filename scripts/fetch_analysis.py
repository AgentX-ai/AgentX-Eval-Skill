#!/usr/bin/env python3
"""
Fetch an evaluation analysis straight from the API, by evaluation id.

    python3 fetch_analysis.py <evaluation_id>
    python3 fetch_analysis.py <evaluation_id> --write-export eval-analysis/exports/

Emits the same JSON shape as parse_export.py, so everything downstream is
identical whichever way the analysis arrived. With --write-export it also renders
a markdown file in the shape of the dashboard export, which is what the triage
brief reads.

Why prefer this over the downloaded markdown, when both work:

The dashboard export is rendered in the browser, and it leaves out the four
fields that matter most when the question is "what should I change in the code":
`acceptance_criteria`, `rejection_criteria`, `evaluation_criteria`, and the
per-case `judge_guideline`. Those are the rubric. Without them a triage has to
reconstruct what "good" meant from the judge's prose, and the most common way a
report misleads is by recommending something the rubric actively penalises. The
dataset API has all four, so fetching gets you the grading surface first-hand.

The export also cannot tell you whether it is current. An id can.

Requires `agentx-python` and `AGENTX_API_KEY`. Reads `.env` if `python-dotenv` is
installed, so it works from a repo checkout without exporting anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


def _load_env(explicit: Path | None) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if explicit is not None:
        load_dotenv(explicit)
    else:
        for candidate in (Path.cwd() / ".env", Path.cwd().parent / ".env"):
            if candidate.is_file():
                load_dotenv(candidate)
                break


def _val(obj: Any, *names: str, default: Any = None) -> Any:
    """First present attribute, tolerating snake_case and camelCase drift."""
    for name in names:
        if obj is None:
            return default
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
        if isinstance(obj, dict) and name in obj and obj[name] is not None:
            return obj[name]
    return default


def _reports(client: Any) -> Any:
    """Find something with `get_report` on it.

    `AgentX.evaluations` is a runner built for the run-then-analyze flow. It
    forwards `get_analysis_status`, but not `get_report`, so there is no public
    route from the top-level client to a finished report. Three fallbacks, best
    first, so this keeps working if the runner ever grows the method:

      1. the runner itself, if it has grown one
      2. the evaluations client it wraps
      3. a client built from the environment
    """
    runner = getattr(client, "evaluations", None)
    if runner is not None and hasattr(runner, "get_report"):
        return runner

    inner = getattr(runner, "_client", None)
    if inner is not None and hasattr(inner, "get_report"):
        return inner

    from agentx.evaluations.client import EvaluationsClient

    return EvaluationsClient(
        api_key=os.environ["AGENTX_API_KEY"],
        base_url=os.getenv("AGENTX_API_BASE_URL"),
        workspace_id=os.getenv("AGENTX_WORKSPACE_ID"),
    )


def _step(step: Any) -> dict[str, Any]:
    """One query and everything the judge was told about grading it."""
    smoke = _val(step, "smoke_test", "smokeTest")
    return {
        "query": _val(step, "query", default=""),
        "expected_results": _val(step, "expected_results", "expectedResults"),
        "judge_guideline": _val(step, "judge_guideline", "judgeGuideline"),
        "smoke_test_count": _val(smoke, "count") if smoke is not None else None,
    }


def _case(question: Any, index: int) -> dict[str, Any]:
    """A dataset question, flattened.

    The query and its grading fields live on `main_question`, not on the question
    itself, and a multi-step case carries more of them in `follow_up_questions`.
    Reading the outer object directly returns empties, which looks exactly like a
    dataset that has no expected results, so flatten explicitly.
    """
    main = _val(question, "main_question", "mainQuestion")
    case: dict[str, Any] = {"index": index}
    case.update(_step(main if main is not None else question))
    follow_ups = _val(question, "follow_up_questions", "followUpQuestions", default=[]) or []
    if follow_ups:
        case["follow_ups"] = [_step(f) for f in follow_ups]
    return case


def collect(evaluation_id: str) -> dict[str, Any]:
    try:
        from agentx import AgentX
    except ImportError:
        print(
            "agentx-python is not installed. `pip install agentx-python`, or use\n"
            "parse_export.py against a downloaded .md export instead.",
            file=sys.stderr,
        )
        raise SystemExit(3)

    if not os.getenv("AGENTX_API_KEY"):
        print(
            "AGENTX_API_KEY is not set. It has to be a key whose account owns the\n"
            "evaluation, on the API host that ran it. A development key will not\n"
            "authenticate against production.",
            file=sys.stderr,
        )
        raise SystemExit(3)

    client = AgentX.from_env()
    report = _reports(client).get_report(evaluation_id)

    dataset = None
    dataset_id = _val(report, "dataset_id", "datasetId", default="")
    if dataset_id:
        try:
            dataset = client.evaluations.datasets.get(dataset_id)
        except Exception as exc:  # noqa: BLE001
            # Worth continuing without: the analysis alone still supports a
            # triage, it just loses the rubric. Say so rather than failing.
            print(
                f"warning: could not fetch dataset {dataset_id}: {exc}\n"
                f"         proceeding without the grading criteria, which weakens "
                f"the triage.",
                file=sys.stderr,
            )

    stats = _val(report, "statistics")
    adherence = _val(report, "instruction_adherence", "instructionAdherence")

    recommendations = []
    for i, rec in enumerate(_val(report, "recommendations", default=[]) or [], start=1):
        recommendations.append(
            {
                "number": i,
                "category": _val(rec, "category", default="") or "",
                "priority": _val(rec, "priority"),
                "text": _val(rec, "recommendation", default="") or "",
                "reasoning": _val(rec, "reasoning", default="") or "",
            }
        )

    grading: dict[str, Any] = {}
    if dataset is not None:
        grading = {
            "acceptance_criteria": _val(dataset, "acceptance_criteria", "acceptanceCriteria"),
            "rejection_criteria": _val(dataset, "rejection_criteria", "rejectionCriteria"),
            "evaluation_criteria": _val(dataset, "evaluation_criteria", "evaluationCriteria"),
            "number_of_requests": _val(dataset, "number_of_requests", "numberOfRequests"),
            "cases": [
                _case(q, i)
                for i, q in enumerate(_val(dataset, "questions", default=[]) or [])
            ],
        }

    return {
        "source": {"kind": "api", "evaluation_id": evaluation_id},
        "dataset_id": dataset_id,
        "evaluation_id": _val(report, "run_id", "runId", default=evaluation_id),
        "workspace_id": _val(report, "workspace_id", "workspaceId", default=""),
        "dataset_name": _val(dataset, "name", default=""),
        "dashboard_url": _val(report, "dashboard_url", "dashboardUrl", default=""),
        "baseline": {
            "runs": _val(stats, "number_of_runs", "numberOfRuns"),
            "average_score": _val(stats, "average_rating", "averageRating"),
            "min_score": _val(stats, "min_rating", "minRating"),
            "max_score": _val(stats, "max_rating", "maxRating"),
            # The report model exposes no variance field. Spread stands in for
            # it, and is labelled as a substitute wherever it is displayed.
            "score_variance": None,
            "consistency_score": _val(report, "consistency_score", "consistencyScore"),
            "instruction_adherence": _val(adherence, "score"),
        },
        "summary": _val(report, "summary", default=""),
        "strengths": _val(report, "strengths", default=[]),
        "weaknesses": _val(report, "weaknesses", default=[]),
        "recommendations": recommendations,
        "low_scoring_cases": _val(report, "low_scoring_cases", "lowScoringCases", default=[]),
        "grading": grading,
    }


def render_markdown(data: dict[str, Any]) -> str:
    """Render in the shape of the dashboard export, plus the grading criteria.

    The extra section is the whole point: the triage reads this file, and a
    triage that cannot see the rubric will confidently recommend changes the
    rubric penalises.
    """
    b = data["baseline"]
    out: list[str] = []
    add = out.append

    add(f"# AI Analysis: {data.get('dataset_name') or 'evaluation'}\n")
    add(f"_Fetched from the API for evaluation `{data['evaluation_id']}`._\n")

    add("## Identifiers\n")
    add("| Field | Value |")
    add("| --- | --- |")
    add(f"| Evaluation ID | `{data['evaluation_id']}` |")
    add(f"| Dataset ID | `{data['dataset_id']}` |")
    add(f"| Dataset | {data.get('dataset_name') or '-'} |")
    if data.get("workspace_id"):
        add(f"| Workspace ID | `{data['workspace_id']}` |")
    add("")

    add("## Statistics\n")
    add("| Metric | Value |")
    add("| --- | --- |")
    for label, key in (
        ("Runs", "runs"),
        ("Average score", "average_score"),
        ("Min score", "min_score"),
        ("Max score", "max_score"),
        ("Consistency score", "consistency_score"),
        ("Instruction adherence", "instruction_adherence"),
    ):
        if b.get(key) is not None:
            add(f"| {label} | {b[key]} |")
    if b.get("min_score") is not None and b.get("max_score") is not None:
        add(f"| Spread (max - min) | {b['max_score'] - b['min_score']} |")
    add("")
    add("The report model exposes no variance field; spread is shown instead.\n")

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

    # The section the dashboard export does not have, and the reason to prefer
    # this path. Placed before the recommendations deliberately: whoever reads
    # the recommendations should already know what the answers were graded on.
    g = data.get("grading") or {}
    if g:
        add("## Grading criteria\n")
        add("These are the strings the judge scored against. A recommendation")
        add("that conflicts with them lowers the score, however sensible it")
        add("sounds in isolation.\n")
        for label, key in (
            ("Acceptance criteria", "acceptance_criteria"),
            ("Rejection criteria", "rejection_criteria"),
            ("Evaluation criteria", "evaluation_criteria"),
        ):
            if g.get(key):
                add(f"**{label}.** {g[key]}\n")
        if g.get("number_of_requests"):
            add(f"**Runs per question.** {g['number_of_requests']}\n")

        if g.get("cases"):
            add("### Test cases\n")
            for case in g["cases"]:
                add(f"#### Case {case['index']}\n")
                add(f"- **Query.** {case['query']}")
                if case.get("expected_results"):
                    add(f"- **Expected result.** {case['expected_results']}")
                if case.get("judge_guideline"):
                    add(f"- **Judge guideline.** {case['judge_guideline']}")
                add("")

    if data.get("recommendations"):
        add("## Recommendations\n")
        for rec in data["recommendations"]:
            priority = f" - {rec['priority']} priority" if rec.get("priority") else ""
            add(f"### {rec['number']}. {rec['category']}{priority}\n")
            add(rec["text"] + "\n")
            if rec.get("reasoning"):
                add(f"_Reasoning: {rec['reasoning']}_\n")

    if data.get("low_scoring_cases"):
        add("## Judge evidence: lowest-scoring cases\n")
        add("```json")
        add(json.dumps(data["low_scoring_cases"], indent=2)[:20000])
        add("```\n")

    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("evaluation_id", help="evaluation / run id")
    ap.add_argument(
        "--write-export",
        type=Path,
        metavar="DIR",
        help="also render a markdown export into DIR, for the triage to read",
    )
    ap.add_argument("--env-file", type=Path, help="explicit .env to load")
    ap.add_argument("--field", help="print one value bare, e.g. dataset_id")
    args = ap.parse_args()

    _load_env(args.env_file)

    if not re.fullmatch(r"[0-9a-f]{24}", args.evaluation_id):
        print(
            f"{args.evaluation_id!r} is not a 24-character hex id.",
            file=sys.stderr,
        )
        return 2

    try:
        data = collect(args.evaluation_id)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"could not fetch the analysis: {exc}", file=sys.stderr)
        return 1

    if args.write_export:
        args.write_export.mkdir(parents=True, exist_ok=True)
        path = args.write_export / f"analysis_{data['evaluation_id']}.md"
        path.write_text(render_markdown(data), encoding="utf-8")
        print(f"wrote {path}", file=sys.stderr)
        if not (data.get("grading") or {}).get("acceptance_criteria"):
            print(
                "note: no grading criteria came back with the dataset, so the "
                "triage will have to infer the rubric from the harness in the repo.",
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
