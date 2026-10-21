"""Render PR-Agent's structured review into the CONTRACT.md comment.

PR-Agent builds its PR comment in Python (`convert_to_markdown_v2`), so the
model's prose never reaches GitHub verbatim: it is reshaped into a GFM table.
Asking `extra_instructions` for `## Confidence Score` headings therefore cannot
work, which is why the contract comment is rendered here instead, from the
structured review dict PR-Agent emits.

Usage inside the workflow:

    python3 scripts/render_contract_comment.py --jsonl pr-agent-review.jsonl \
        --out contract-comment.md
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys


class ContractError(ValueError):
    """The review cannot be expressed as a contract comment."""


# PR-Agent scores 0-100; the loop speaks in fifths.
SCORE_THRESHOLDS = ((90, 5), (70, 4), (50, 3), (30, 2), (10, 1))

# A line that starts a Markdown heading, e.g. "## Issues" or "# Agent Prompt".
HEADING_ESCAPE = re.compile(r"^(#{1,6})(?=\s|$)", re.MULTILINE)


def _score_to_fifths(raw: object) -> int:
    """Map PR-Agent's 0-100 score onto the loop's 0-5 scale.

    The score arrives as model output that PR-Agent has already parsed as YAML,
    so the same number can show up as `90`, `90.0` or `"90"` depending on how
    the model wrote it. Treating the non-int spellings as contract violations
    would fail the render step and stall the loop on a review that is perfectly
    usable, so numeric forms are coerced. A bool is rejected rather than
    coerced, since `True` is an accident, not a score.
    """
    value: float
    if isinstance(raw, bool):
        raise ContractError(f"score must be a number, got {raw!r}")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        value = raw
    elif isinstance(raw, str):
        try:
            value = float(raw.strip())
        except ValueError:
            raise ContractError(f"score is not a number: {raw!r}") from None
    else:
        raise ContractError(f"missing or non-numeric score: {raw!r}")

    if not math.isfinite(value):
        raise ContractError(f"score is not finite: {raw!r}")
    if not 0 <= value <= 100:
        raise ContractError(f"score out of range 0-100: {raw!r}")

    for floor, fifths in SCORE_THRESHOLDS:
        if value >= floor:
            return fifths
    return 0


LINE_RANGE = re.compile(r"^\s*(\d+)\s*(?:[-–]\s*(\d+))?\s*$")


def _parse_last_line(value: object) -> int:
    """Read the end of a line range, as written, or 0 when there isn't one."""
    match = LINE_RANGE.findall(str(value or "")) or []
    if not match:
        return _parse_line(value)
    first, last = match[0]
    return int(last or first)


def _parse_line(value: object) -> int:
    """Read a line number as the model wrote it, or 0 when there isn't one.

    The schema types these as ints, but the value reaches us as model output, so
    `"42"`, `42.0` and `None` all occur. Anything unusable becomes 0, which
    `_format_location` renders as a file with no line rather than losing the
    file too.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, float):
        return int(value) if value > 0 else 0
    match = LINE_RANGE.match(str(value or ""))
    return int(match.group(1)) if match else 0


def _format_location(path: str, start_raw: object, end_raw: object) -> str:
    """Render `file:start-end`, tolerating a range written into one field.

    A model can put `"42-50"` in `start_line` and leave `end_line` empty, so the
    first integer is read out of `start_line` rather than dropping the location
    entirely. `end_line` may likewise carry a range; its last integer is the end.

    A reversed range (`start=50, end=42`) is rendered as written rather than
    collapsed. The Slice 2 parser reads the comment as the reviewed location, so
    quietly turning an impossible range into a plausible single line would
    misreport where the finding is.
    """
    start = _parse_line(start_raw)
    end = _parse_last_line(end_raw)
    if not path:
        return "(no location)"
    if start < 1:
        return path
    if end < 1 or end == start:
        return f"{path}:{start}"
    return f"{path}:{start}-{end}"


def _format_issue(issue: dict) -> str:
    path = _escape_headings(str(issue.get("relevant_file") or "").strip())
    header = _escape_headings(str(issue.get("issue_header") or "").strip())
    content = _escape_headings(" ".join(str(issue.get("issue_content") or "").split()))
    location = _format_location(path, issue.get("start_line"), issue.get("end_line"))
    title = f"{header}: " if header else ""
    return f"- {location} — {title}{content}".rstrip()


NO_OP_PROMPT = "No issues found; no changes required."


def _escape_headings(text: str) -> str:
    """Neutralise section headings inside model-supplied text.

    The Slice 2 parser splits the comment on the three `##` headings. Model text
    is quoted into the comment, so a finding or prompt that mentions
    `## Issues` would create a fourth heading and reattribute everything after
    it - the parser would read the wrong prompt, or take one issue's text for
    the start of another section. Escaping rather than rejecting keeps a usable
    review usable.

    Only line-initial `#` runs are touched, so a heading mentioned mid-sentence
    stays readable.
    """
    return HEADING_ESCAPE.sub(r"\\\1", text)


def _derive_agent_prompt(issues: list[dict]) -> str:
    """Build a prompt from the findings when the model did not supply one.

    `agent_prompt` is not part of PR-Agent's review schema, so the model may
    skip it, and in practice it always does. Deriving the prompt instead of
    failing keeps the comment useful and the loop indifferent to model
    behaviour we do not control.

    No findings means nothing to fix - the successful end of the loop, not an
    error. Raising here failed the workflow on every clean PR and left no
    comment at all, so the loop could never observe its own success.
    """
    if not issues:
        return NO_OP_PROMPT

    lines = ["Fix the following issues reported by the PR review."]
    for issue in issues:
        path = _escape_headings(str(issue.get("relevant_file") or "").strip())
        header = _escape_headings(str(issue.get("issue_header") or "").strip())
        content = _escape_headings(" ".join(str(issue.get("issue_content") or "").split()))
        location = _format_location(path, issue.get("start_line"), issue.get("end_line"))
        lines.append(f"- {location}: {header}. {content}".rstrip())
    lines.append("Keep each change minimal and leave the rest of the code untouched.")
    return "\n".join(lines)


def render_contract_comment(review: dict) -> str:
    """Return the canonical three-section comment for one structured review."""
    if not isinstance(review, dict):
        raise ContractError(f"review must be a dict, got {type(review).__name__}")

    score = _score_to_fifths(review.get("score"))

    issues = review.get("key_issues_to_review") or []
    if not isinstance(issues, list):
        raise ContractError(f"key_issues_to_review must be a list, got {type(issues).__name__}")
    findings = [issue for issue in issues if isinstance(issue, dict)]
    bullets = [_format_issue(issue) for issue in findings]
    issues_body = "\n".join(bullets) if bullets else "None"

    prompt = review.get("agent_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        prompt = _derive_agent_prompt(findings)
    prompt = _escape_headings(prompt.strip())

    return (
        f"## Confidence Score\n{score}/5\n\n"
        f"## Issues\n{issues_body}\n\n"
        f"## Agent Prompt\n{prompt}\n"
    )


def latest_review(jsonl_path: pathlib.Path) -> dict:
    """Return the payload of the last review record in a push_outputs JSONL file.

    The sink appends one record per tool run, and a record carries its tool in
    `type` (`push_outputs("review", ...)`). Selecting the last line blindly
    would break as soon as another tool writes to the same file, or read an
    empty payload from a record that failed before producing one.
    """
    lines = [line for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ContractError(f"no records in {jsonl_path}")

    found_review = False
    for number, line in enumerate(reversed(lines), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContractError(
                f"record {len(lines) - number + 1} in {jsonl_path} is not JSON: {error}"
            ) from error
        if not isinstance(record, dict):
            continue
        if record.get("type") != "review":
            continue
        found_review = True
        payload = record.get("payload")
        if isinstance(payload, dict) and payload:
            return payload

    if found_review:
        raise ContractError(f"the last review record in {jsonl_path} carries no payload")
    raise ContractError(f"no review record in {jsonl_path}")


def failure_comment(reason: str) -> str:
    """Render the comment to post when no review could be produced.

    A 0/5 score is deliberate: the loop reads the score to decide whether to
    keep going, so this stops it at once and names the real problem. Leaving no
    comment at all would look identical to a review that has not arrived yet,
    and the loop would wait for one that never will.
    """
    reason = _escape_headings(" ".join(reason.split())) or "review failed"
    return f"## Confidence Score\n0/5\n\n## Issues\n- Review failed: {reason}\n\n## Agent Prompt\nDo not attempt a fix. The review itself failed, so there is nothing to act on. Check the workflow run logs.\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", type=pathlib.Path)
    parser.add_argument(
        "--failure",
        metavar="REASON",
        help="write a 0/5 comment for a review that could not be produced",
    )
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args(argv)

    if args.failure is not None:
        comment = failure_comment(args.failure)
    elif args.jsonl is None:
        parser.error("one of --jsonl or --failure is required")
    else:
        try:
            comment = render_contract_comment(latest_review(args.jsonl))
        except (ContractError, OSError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

    if args.out:
        args.out.write_text(comment, encoding="utf-8")
        print(f"wrote {args.out} ({len(comment)} chars)")
    else:
        sys.stdout.write(comment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
