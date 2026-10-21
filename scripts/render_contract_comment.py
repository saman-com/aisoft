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
import pathlib
import sys


class ContractError(ValueError):
    """The review cannot be expressed as a contract comment."""


# PR-Agent scores 0-100; the loop speaks in fifths.
SCORE_THRESHOLDS = ((90, 5), (70, 4), (50, 3), (30, 2), (10, 1))


def _score_to_fifths(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ContractError(f"missing or non-integer score: {raw!r}")
    if not 0 <= raw <= 100:
        raise ContractError(f"score out of range 0-100: {raw}")
    for floor, fifths in SCORE_THRESHOLDS:
        if raw >= floor:
            return fifths
    return 0


def _format_issue(issue: dict) -> str:
    path = str(issue.get("relevant_file") or "").strip()
    header = str(issue.get("issue_header") or "").strip()
    content = " ".join(str(issue.get("issue_content") or "").split())
    try:
        start = int(issue.get("start_line") or 0)
        end = int(issue.get("end_line") or start)
    except (TypeError, ValueError):
        start = end = 0

    location = path if start < 1 else (f"{path}:{start}" if end <= start else f"{path}:{start}-{end}")
    title = f"{header}: " if header else ""
    return f"- {location or '(no location)'} — {title}{content}".rstrip()


NO_OP_PROMPT = "No issues found; no changes required."


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
        path = str(issue.get("relevant_file") or "").strip()
        header = str(issue.get("issue_header") or "").strip()
        content = " ".join(str(issue.get("issue_content") or "").split())
        try:
            start = int(issue.get("start_line") or 0)
            end = int(issue.get("end_line") or start)
        except (TypeError, ValueError):
            start = end = 0
        location = path if start < 1 else (f"{path}:{start}" if end <= start else f"{path}:{start}-{end}")
        lines.append(f"- {location or '(no location)'}: {header}. {content}".rstrip())
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

    return (
        f"## Confidence Score\n{score}/5\n\n"
        f"## Issues\n{issues_body}\n\n"
        f"## Agent Prompt\n{prompt.strip()}\n"
    )


def latest_review(jsonl_path: pathlib.Path) -> dict:
    """Return the review payload from the last record of a push_outputs JSONL file."""
    records = [line for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records:
        raise ContractError(f"no review records in {jsonl_path}")
    try:
        record = json.loads(records[-1])
    except json.JSONDecodeError as error:
        raise ContractError(f"last record in {jsonl_path} is not JSON: {error}") from error
    payload = record.get("payload")
    if not isinstance(payload, dict) or not payload:
        raise ContractError(f"last record in {jsonl_path} carries no review payload")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", required=True, type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args(argv)

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
