#!/usr/bin/env python3
"""
Pull the machine-readable facts out of an AgentX "AI Analysis" markdown export.

    python3 parse_export.py <export.md> [--field dataset_id]

Prints JSON on stdout. With --field, prints that one value bare, which is what
you want when feeding a shell variable.

Why this is a script and not something you read off the page: the dataset id is
the one value that, if wrong, produces a complete, plausible, expensive result
that answers a different question. The evaluation harness reuses a dataset when
it is handed an id and silently publishes a brand new one when it is not, so a
typo does not fail, it just quietly makes the comparison meaningless. Parsing is
cheap; re-running an evaluation against the wrong dataset is not.

The parser is deliberately strict. A field it cannot find is an error, not a
None, because every consumer of this output treats a missing value as "not
applicable" and would carry on into exactly the failure above.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Rows in the export's two-column tables look like:  | Field | Value |
# Values are often wrapped in backticks. Both are stripped here.
_ROW = re.compile(r"^\|\s*(?P<key>[^|]+?)\s*\|\s*(?P<value>[^|]*?)\s*\|\s*$")

# "### 1. instructions - high priority"  ->  (1, "instructions", "high")
_REC_HEADING = re.compile(
    r"^###\s*(?P<num>\d+)\.\s*(?P<category>[^\n]*?)\s*(?:[-—]\s*(?P<priority>\w+)\s*priority)?\s*$"
)

_MISSING = object()


class ExportParseError(RuntimeError):
    """The export is not shaped the way every AgentX export is shaped."""


def _sections(text: str) -> dict[str, str]:
    """Split on level-2 headings, keeping each section's body.

    Section bodies are what the field lookups scan. Scoping lookups to a section
    matters because the same row label appears more than once across an export:
    "Average score" shows up under both Quality metrics and Statistics, with
    different formatting, and a document-wide search would pick whichever came
    first rather than the one asked for.
    """
    out: dict[str, str] = {}
    current = "_preamble"
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            out[current.strip().lower()] = "\n".join(buf)
            current = line[3:]
            buf = []
        else:
            buf.append(line)
    out[current.strip().lower()] = "\n".join(buf)
    return out


def _rows(section: str) -> dict[str, str]:
    """Every `| key | value |` pair in a section, lowercased keys."""
    found: dict[str, str] = {}
    for line in section.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        key = m.group("key").strip().lower()
        value = m.group("value").strip().strip("`").strip()
        # Skip the header and the `| --- | --- |` separator.
        if key in {"field", "metric"} or set(key) <= {"-", ":"}:
            continue
        found[key] = value
    return found


def _get(rows: dict[str, str], key: str, default: Any = _MISSING) -> str:
    value = rows.get(key.lower())
    if value in (None, "", "-", "—"):
        if default is _MISSING:
            raise ExportParseError(
                f"missing or empty field {key!r}. "
                f"Available in this section: {sorted(rows)}"
            )
        return default
    return value


def _num(value: str | None) -> float | None:
    """Numbers in the export carry stray formatting: '6.50', '100%', '$0.11'."""
    if value is None:
        return None
    cleaned = value.replace("%", "").replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _recommendations(section: str) -> list[dict[str, Any]]:
    """Each numbered recommendation, with its body and italicised reasoning.

    The count matters downstream: the triage brief asks for one row per
    recommendation, and "did every recommendation get a verdict" is the first
    thing worth checking about a mapping table.
    """
    recs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body: list[str] = []

    def flush() -> None:
        if current is None:
            return
        text = "\n".join(body).strip()
        reasoning = ""
        m = re.search(r"_Reasoning:\s*(.*?)_\s*$", text, re.S)
        if m:
            reasoning = " ".join(m.group(1).split())
            text = text[: m.start()].strip()
        current["text"] = " ".join(text.split())
        current["reasoning"] = reasoning
        recs.append(current)

    for line in section.splitlines():
        m = _REC_HEADING.match(line)
        if m:
            flush()
            current = {
                "number": int(m.group("num")),
                "category": (m.group("category") or "").strip(),
                "priority": (m.group("priority") or "").strip() or None,
            }
            body = []
        elif current is not None:
            body.append(line)
    flush()
    return recs


def parse(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    sections = _sections(text)

    for required in ("identifiers", "statistics"):
        if required not in sections:
            raise ExportParseError(
                f"no '## {required.title()}' section. This does not look like an "
                f"AgentX analysis export. Sections found: {sorted(sections)}"
            )

    ident = _rows(sections["identifiers"])
    stats = _rows(sections["statistics"])
    subject = _rows(sections.get("subject under test", ""))
    adherence_section = sections.get("instruction adherence", "")

    # "**Score:** 5.5 . **Confidence:** medium" is prose, not a table row.
    adherence = None
    m = re.search(r"\*\*Score:\*\*\s*([\d.]+)", adherence_section)
    if m:
        adherence = float(m.group(1))

    result: dict[str, Any] = {
        "source_file": str(path),
        "dataset_id": _get(ident, "Dataset ID"),
        "evaluation_id": _get(ident, "Evaluation ID"),
        "workspace_id": _get(ident, "Workspace ID"),
        "dataset_name": _get(ident, "Dataset", default=""),
        "grading_config_id": _get(ident, "Grading config ID", default=""),
        "subject": {
            "kind": _get(subject, "Kind", default=""),
            "name": _get(subject, "Name", default=""),
            "framework": _get(subject, "Framework", default=""),
            "runtime": _get(subject, "Runtime", default=""),
        },
        "baseline": {
            "runs": _num(_get(stats, "Runs", default="")),
            "average_score": _num(_get(stats, "Average score")),
            "min_score": _num(_get(stats, "Min score", default="")),
            "max_score": _num(_get(stats, "Max score", default="")),
            "score_variance": _num(_get(stats, "Score variance", default="")),
            "consistency_score": _num(_get(stats, "Consistency score", default="")),
            "instruction_adherence": adherence,
        },
        "recommendations": _recommendations(sections.get("recommendations", "")),
    }

    # An id that is not a 24-character hex ObjectId is almost always a row that
    # was read out of the wrong column, and it is worth catching here rather
    # than as a 404 twenty minutes into a run.
    for key in ("dataset_id", "evaluation_id", "workspace_id"):
        value = result[key]
        if not re.fullmatch(r"[0-9a-f]{24}", value):
            raise ExportParseError(
                f"{key} {value!r} is not a 24-character hex id. "
                f"The Identifiers table may have been misread."
            )

    if not result["recommendations"]:
        raise ExportParseError(
            "no numbered recommendations found. The triage step has nothing to "
            "work from, so stop and check the export."
        )

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("export", type=Path, help="path to the analysis .md export")
    ap.add_argument(
        "--field",
        help="print one value bare instead of the whole JSON, e.g. dataset_id",
    )
    args = ap.parse_args()

    if not args.export.is_file():
        print(f"no such file: {args.export}", file=sys.stderr)
        return 2

    try:
        data = parse(args.export)
    except ExportParseError as exc:
        print(f"could not parse export: {exc}", file=sys.stderr)
        return 1

    if args.field:
        value = data
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
