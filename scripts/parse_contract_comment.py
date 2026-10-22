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

# An optional `[P0]` / `[P1]` / `[P2]` tag at the start of an issue bullet. The
# tag is written by the reviewer, so it is read case-insensitively.
SEVERITY_TAG = re.compile(r"^\s*\[(?P<tag>[^\]]{1,16})\]\s*")

# The only severities whose presence is decided. Everything else, including a
# bullet with no tag at all, is treated as blocking. Guessing that an
# unrecognised tag means "cosmetic" is how a real bug ships.
BLOCKING_SEVERITIES = frozenset({"P0", "P1"})
NON_BLOCKING_SEVERITIES = frozenset({"P2"})

# What is dropped from a fingerprint's description: punctuation, spacing and
# case, which vary without carrying meaning.
FINGERPRINT_SEPARATORS = re.compile(r"[^\w]+", re.UNICODE)

# What is collapsed on a location: whitespace only. A path's own `_`, `-`, `/`
# and `.` are kept, because folding them would let `a.py` collide with `a/py`.
FINGERPRINT_SPACE = re.compile(r"\s+")

# The dash between the location and the rest of a bullet, per CONTRACT.md. All
# three dash characters appear in practice, so any of them separates the two.
LOCATION_SEPARATOR = re.compile(r"\s+[—–-]\s+", re.UNICODE)

# The line or line range on a location (`a.py:1`, `a.py:1-2`). Dropped from a
# fingerprint because Pi's edits move lines: the same unfixed finding is reported
# at a new line number on the next pass.
LOCATION_LINES = re.compile(r":\d+(?:-\d+)?$")

FENCE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")


class ParseError(ValueError):
    """The comment does not satisfy the contract."""


def severity_of(issue: str) -> str | None:
    """The `P`-tag on an issue bullet, upper-cased, or None when there is none.

    Returns whatever tag was written, which is not the same as "a severity this
    module understands". `[P3]` and `[HIGH]` come back as `"P3"` and `"HIGH"`;
    `is_blocking` is what decides how to read them.
    """
    match = SEVERITY_TAG.match(issue)
    return match.group("tag").strip().upper() if match else None


def is_blocking(issue: str) -> bool:
    """True unless the bullet is explicitly tagged as cosmetic.

    Only `[P2]` is non-blocking. A missing tag and an unknown tag both block:
    the reviewer failing to say how bad a finding is does not make it safe, and
    an unrecognised tag is more likely a format change than a downgrade.
    """
    return severity_of(issue) not in NON_BLOCKING_SEVERITIES


def issue_fingerprint(issue: str) -> str:
    """A stable comparison key for one bullet, for spotting a repeated finding.

    Used to answer "is this the same problem as last time", so it has to survive
    the differences a re-review produces and ignore the ones that are noise:

    - The severity tag is dropped: a finding that is still there may be reported
      as `[P1]` on one pass and `[P0]` on the next, and it is the same problem.
    - The line numbers are dropped, keeping the file. Pi's commit moves lines, so
      `a.py:1` becoming `a.py:9` is the same unfixed finding, not a new one.
    - The header (`<header>: `) is dropped. The reviewer rewords it freely and
      the description is the part that says what is wrong.
    - Case, punctuation and spacing are folded, so `A.PY:1 — Wrong: X.` and
      `a.py — wrong: x` compare equal.

    Deliberately not fuzzy: no edit distance, no synonym matching. A false
    "repeated" verdict stops a loop that was making progress, which is worse
    than the extra iterations it saves, so a description the reviewer rephrased
    beyond recognition simply fails to match.
    """
    text = SEVERITY_TAG.sub("", issue, count=1).strip()

    # `<location> — <header>: <description>`. The header is optional and the
    # description may contain its own colons and dashes, so both splits take
    # only the first separator.
    parts = LOCATION_SEPARATOR.split(text, maxsplit=1)
    if len(parts) == 2:
        location, rest = parts
    else:
        # No dash: the whole bullet is the finding. Falling back to the whole
        # text is safer than treating the first token as a path.
        location, rest = "", text

    location = LOCATION_LINES.sub("", location)
    _, colon, description = rest.partition(": ")
    body = description if colon else rest

    # The location keeps its punctuation and only loses whitespace; the
    # description loses punctuation too. Folding a path's `.` and `/` would let
    # `a.py` and `a/py` compare equal.
    path = FINGERPRINT_SPACE.sub("", location.lower())
    words = FINGERPRINT_SEPARATORS.sub(" ", body.lower()).strip()
    return " ".join(part for part in (path, " ".join(words.split())) if part)


# A score that needs no further work on its own, regardless of the issues listed.
SUCCESS_SCORE = 5

# The score at which a review with nothing blocking counts as done. The reviewer
# plateaus here while listing low-confidence findings, so treating 4/5 as "keep
# going" means paying Pi to chase findings the reviewer itself rated as minor.
SUFFICIENT_SCORE = 4


@dataclass(frozen=True)
class Review:
    score: int
    issues: tuple[str, ...]
    agent_prompt: str

    @property
    def blocking_issues(self) -> tuple[str, ...]:
        """The issues the loop must still fix before a human merges."""
        return tuple(issue for issue in self.issues if is_blocking(issue))

    @property
    def blocking_count(self) -> int:
        return len(self.blocking_issues)

    @property
    def blocking_fingerprint(self) -> tuple[str, ...]:
        """The blocking findings reduced to comparison keys, sorted.

        Sorted and deduplicated so a re-review that reports the same findings in
        a different order, or lists one twice, still compares equal. Empty when
        nothing blocks, which is why a clean review can never look "repeated".
        """
        return tuple(sorted({issue_fingerprint(i) for i in self.blocking_issues}))

    @property
    def is_clean(self) -> bool:
        """True when the review needs no further work, per docs/TASKS.md T4.2.

            success <=> score == 5 or (score >= 4 and no P0/P1 issue)

        This is the loop's real exit. `score == 5` alone is not it: the reviewer
        settles at 4/5 while reporting only nits, and stopping there would mean
        the loop could never finish on those PRs.
        """
        if self.score >= SUCCESS_SCORE:
            return True
        return self.score >= SUFFICIENT_SCORE and not self.blocking_issues


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
    """The first `X/5` in the section.

    CONTRACT.md says to take the first integer followed by `/5`, so a line of
    prose above the score does not make the comment unreadable. A number outside
    0-5 raises rather than being clamped: a score the renderer could not have
    produced means something upstream is wrong.
    """
    for line in text.splitlines():
        match = SCORE_LINE.match(line)
        if not match:
            continue
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
