"""Parse the three sections out of a contract comment.

Implements the "Parsing rules" and "Failure rules" sections of CONTRACT.md. The
parser is strict on purpose: a guessed score stops a PR early or keeps the loop
running, and a partial Agent Prompt is handed straight to Pi. Both failures cost
more than one loud error, so a malformed comment always raises.

Two details are easy to get wrong and are handled explicitly:

- Headers are only headers when they are not inside a fenced code block. A
  finding may quote `## Issues`, and reading that quote as a section would split
  the comment in the wrong place.
- Sections must appear in the documented order. If they do not, the text between
  two headers is not what the section claims to hold, and every extraction after
  that point is wrong.

Usage:

    python3 scripts/parse_contract_comment.py --pr 27
    python3 scripts/parse_contract_comment.py --body-file contract-comment.md --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

# The headers, in the order CONTRACT.md requires.
SECTION_TITLES = ("Confidence Score", "Issues", "Agent Prompt")

HEADER_LINE = re.compile(r"^## (?P<title>Confidence Score|Issues|Agent Prompt)\s*$")

# `4/5`, `Score: 4/5`, `Confidence: 4 / 5`, with any surrounding whitespace.
SCORE_LINE = re.compile(
    r"^\s*(?:(?:confidence|score)\s*:\s*)?(?P<value>-?\d+)\s*/\s*5\s*$",
    re.IGNORECASE,
)

# A bullet, per CONTRACT.md: the line starts with `-` after trimming.
BULLET = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")

# The single word that means "no issues", per CONTRACT.md.
NO_ISSUES = {"none", "no issues", "no issues found", "n/a"}

FENCE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")


class ParseError(ValueError):
    """The comment does not satisfy the contract."""


@dataclass(frozen=True)
class Review:
    score: int
    issues: tuple[str, ...]
    agent_prompt: str

    @property
    def is_clean(self) -> bool:
        return self.score == 5 and not self.issues


def _split_sections(body: str) -> dict[str, str]:
    """Map each section title to the text under it.

    Raises on a missing, duplicated or out-of-order section, and on a header that
    appears inside a code fence.
    """
    lines = body.splitlines()
    found: list[tuple[str, int]] = []
    fence: str | None = None

    for index, line in enumerate(lines):
        fence_match = FENCE.match(line)
        if fence_match:
            marker = fence_match.group("fence")
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        header = HEADER_LINE.match(line)
        if header:
            found.append((header.group("title"), index))

    titles = [title for title, _ in found]
    duplicated = {title for title in titles if titles.count(title) > 1}
    if duplicated:
        raise ParseError(f"duplicated section: {sorted(duplicated)[0]}")
    missing = [title for title in SECTION_TITLES if title not in titles]
    if missing:
        raise ParseError(f"missing section: {missing[0]}")

    order = [titles.index(title) for title in SECTION_TITLES]
    if order != sorted(order):
        raise ParseError(
            "sections out of order: expected "
            + ", ".join(SECTION_TITLES)
            + "; found "
            + ", ".join(titles)
        )

    sections: dict[str, str] = {}
    for position, (title, line_index) in enumerate(found):
        end = found[position + 1][1] if position + 1 < len(found) else len(lines)
        sections[title] = "\n".join(lines[line_index + 1 : end])
    return sections


def _parse_score(text: str) -> int:
    for line in text.splitlines():
        if not line.strip():
            continue
        match = SCORE_LINE.match(line)
        if not match:
            break
        value = int(match.group("value"))
        if not 0 <= value <= 5:
            raise ParseError(f"invalid confidence score: {line.strip()}")
        return value
    raise ParseError(f"invalid confidence score: {text.strip() or '(empty)'}")


def _parse_issues(text: str) -> tuple[str, ...]:
    issues: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        bullet = BULLET.match(line)
        if not bullet:
            continue
        issues.append(bullet.group("text"))
    if not issues:
        # An empty section is an empty list; the "None" spellings are just the
        # renderer being explicit about it.
        return ()
    return tuple(issues)


def _parse_prompt(text: str) -> str:
    prompt = text.strip()
    if not prompt:
        raise ParseError("empty agent prompt")
    return prompt


def parse_comment(
    body: str,
    *,
    comment_id: int | None = None,
    pr: int | None = None,
) -> Review:
    """Parse a contract comment, or raise ParseError.

    `comment_id` and `pr` are only used to make error messages point at the
    comment that failed, so a failure can be debugged without hunting for it.
    """
    if not isinstance(body, str) or not body.strip():
        raise ParseError(_context("empty comment", body, comment_id, pr))
    try:
        sections = _split_sections(body)
    except ParseError as error:
        raise ParseError(_context(str(error), body, comment_id, pr)) from None

    try:
        score = _parse_score(sections["Confidence Score"])
        issues = _parse_issues(sections["Issues"])
        agent_prompt = _parse_prompt(sections["Agent Prompt"])
    except ParseError as error:
        raise ParseError(_context(str(error), body, comment_id, pr)) from None

    return Review(score=score, issues=issues, agent_prompt=agent_prompt)


def _context(
    message: str, body: str, comment_id: int | None, pr: int | None
) -> str:
    where = []
    if comment_id is not None:
        where.append(f"comment {comment_id}")
    if pr is not None:
        where.append(f"PR #{pr}")
    prefix = f" [{', '.join(where)}]" if where else ""
    snippet = body.strip().splitlines()[:6]
    excerpt = "\n".join(snippet) if snippet else "(empty)"
    return f"{message}{prefix}\n--- comment starts ---\n{excerpt}\n--- end ---"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--body-file", default=None, help="read the comment from this file")
    parser.add_argument("--pr", type=int, default=None, help="read via gh, and name it in errors")
    parser.add_argument("--comment-id", type=int, default=None, help="name the comment in errors")
    parser.add_argument("--json", action="store_true", help="print the parsed review as JSON")
    args = parser.parse_args(argv)

    if bool(args.body_file) == (args.pr is not None):
        print("error: give exactly one of --body-file or --pr", file=sys.stderr)
        return 1

    if args.body_file:
        try:
            body = open(args.body_file, encoding="utf-8").read()
        except OSError as error:
            print(f"error: cannot read {args.body_file}: {error}", file=sys.stderr)
            return 1
    else:
        from driver_comments import DriverError, contract_comment, list_comments

        repo_result = _gh_repo()
        if repo_result is None:
            print("error: cannot determine the repository", file=sys.stderr)
            return 1
        try:
            found = contract_comment(list_comments(repo_result, args.pr))
        except DriverError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        if found is None:
            print(f"error: no contract comment on #{args.pr}", file=sys.stderr)
            return 2
        body = found.body
        args.comment_id = found.id

    try:
        review = parse_comment(body, comment_id=args.comment_id, pr=args.pr)
    except ParseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 3

    if args.json:
        print(
            json.dumps(
                {
                    "score": review.score,
                    "issues": list(review.issues),
                    "agent_prompt": review.agent_prompt,
                },
                indent=2,
            )
        )
    else:
        print(f"score: {review.score}/5")
        print(f"issues: {len(review.issues)}")
        print(f"agent prompt: {len(review.agent_prompt)} chars")
    return 0


def _gh_repo() -> str | None:
    import subprocess

    result = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or None


if __name__ == "__main__":
    raise SystemExit(main())
