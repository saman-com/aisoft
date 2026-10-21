"""Fetch the contract comment, and wait for a new one after a push.

A push re-runs the workflow, and until the new review replaces the old one the PR
still shows the previous comment. So "wait for the review" cannot mean "wait for a
comment to exist" - it has to mean "wait for a review written after my push".

The subtlety is that the poster (Slice 1) keeps **one** contract comment per PR
and edits it in place with PATCH, so its id never changes after the first
iteration. Waiting on a larger id therefore works exactly once and then times out
forever. A review counts as new when either:

- its id is larger than the baseline's (a genuinely new comment), or
- it is that same comment and its body or `updated_at` has changed (a rewrite).

`updated_at` alone is not enough because GitHub reports it with one-second
resolution, and a fast rewrite lands in the same second as the review it replaces,
so the body is the signal that actually matters. Bodies are compared with trailing
whitespace ignored, because a baseline carried through a shell
(`--baseline-body "$(cat before.md)"`) loses its trailing newline.

The baseline is the comment the caller read before the push, so
`baseline=<Comment>` or `baseline=<body>` is preferred. `newer_than=<id>` remains
for a caller that has only an id; it cannot detect an in-place rewrite, which is
its own documented limitation.

Polling always ends. An unattended loop that waits forever leaves nobody able to
notice, so the timeout raises with a message naming the PR and what to check.

Usage:

    python3 scripts/driver_comments.py --pr 27 --timeout 600
    python3 scripts/driver_comments.py --pr 27 --newer-than 6306554503
    python3 scripts/driver_comments.py --pr 27 --baseline-body "$(cat before.md)"
"""

# Timeout and interval are documented in AGENT.md. Slice 4 reuses these values
# rather than choosing its own.

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable

# The first line every contract comment starts with, per CONTRACT.md.
CONTRACT_HEADER = "## Confidence Score"

# The account the workflow posts as. Only this account's comments are reviews: a
# human or another bot can post the same headings, and the loop acts on whatever
# it reads.
WORKFLOW_AUTHOR = "github-actions[bot]"

DEFAULT_TIMEOUT = 600.0
DEFAULT_INTERVAL = 15.0


class DriverError(RuntimeError):
    """Base class for driver failures, so callers can catch one type."""


class ReviewTimeout(DriverError):
    """No new contract comment appeared before the deadline."""


@dataclass(frozen=True)
class Comment:
    id: int
    body: str
    author: str
    updated_at: str = ""

    @property
    def score_fifths(self) -> int | None:
        """The score as written in the comment, for logging only.

        Parsing lives in T2.4; this is the cheap reading used to describe what
        was found, and it returns None rather than guessing.
        """
        for line in self.body.splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.endswith("/5") and text[:-2].strip().isdigit():
                return int(text[:-2].strip())
            return None
        return None


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError as error:
        raise DriverError(f"cannot run {args[0]}: {error}") from error


def list_comments(repo: str, pr: int) -> list[dict]:
    """Return every issue comment on the PR, all pages."""
    result = _run(
        ["gh", "api", "--paginate", f"repos/{repo}/issues/{pr}/comments"]
    )
    if result.returncode != 0:
        raise DriverError(
            f"cannot list the comments on #{pr}: "
            f"{result.stderr.strip() or result.returncode}"
        )
    comments: list[dict] = []
    for page in _split_json_arrays(result.stdout):
        comments.extend(page)
    return comments


def _split_json_arrays(out: str) -> list[list[dict]]:
    """Parse the JSON arrays `gh api --paginate` concatenates.

    Any unreadable output raises. Skipping it could drop a page, and a partial
    comment list looks the same as "the review has not arrived yet" - the driver
    would then wait for a timeout instead of reading a review that exists.
    """
    decoder = json.JSONDecoder()
    pages: list[list[dict]] = []
    index = 0
    while index < len(out):
        while index < len(out) and out[index] in " \t\r\n":
            index += 1
        if index >= len(out):
            break
        try:
            value, index = decoder.raw_decode(out, index)
        except json.JSONDecodeError as error:
            raise DriverError(f"cannot parse the comment list: {error}") from error
        if isinstance(value, list):
            pages.append(value)
    return pages


def _to_comment(raw: dict) -> Comment | None:
    if not isinstance(raw, dict):
        return None
    body = raw.get("body")
    comment_id = raw.get("id")
    if not isinstance(body, str) or not isinstance(comment_id, int):
        return None
    user = raw.get("user") or {}
    return Comment(
        id=comment_id,
        body=body,
        author=str(user.get("login") or ""),
        updated_at=str(raw.get("updated_at") or ""),
    )


def _same_account(login: str, author: str) -> bool:
    """Whether `login` is the account `author`.

    Stripping `[bot]` from both sides would make a human account named
    `github-actions` match `github-actions[bot]`, and the loop acts on whatever it
    reads, so the suffix is only ignored when both sides carry it.
    """
    if login == author:
        return True
    return login.endswith("[bot]") and author.endswith("[bot]") and login[:-5] == author[:-5]


def _is_contract_comment(comment: Comment, author: str) -> bool:
    if not _same_account(comment.author, author):
        return False
    return comment.body.lstrip().startswith(CONTRACT_HEADER)


def contract_comment(comments: list[dict], author: str = WORKFLOW_AUTHOR) -> Comment | None:
    """Return the newest contract comment by `author`, or None."""
    newest: Comment | None = None
    for raw in comments:
        parsed = _to_comment(raw)
        if parsed is None or not _is_contract_comment(parsed, author):
            continue
        if newest is None or parsed.id > newest.id:
            newest = parsed
    return newest


@dataclass(frozen=True)
class Baseline:
    """What the caller already read. Fields it does not know stay None.

    `None` means *unknown*, not *empty*: a baseline given only as an id must not
    report an empty body as a change, or every comment would look fresh.
    """

    id: int | None = None
    body: str | None = None
    updated_at: str | None = None


def is_fresh(found: Comment, baseline: Baseline | None) -> bool:
    """Whether `found` is a review written after `baseline` was read.

    The poster edits one comment in place, so the id stays the same across
    iterations and cannot carry freshness on its own. What does change is the
    body, and usually `updated_at`.
    """
    if baseline is None:
        return True

    if baseline.id is not None and found.id != baseline.id:
        # GitHub ids are snowflakes and a rewrite keeps its id, so a different id
        # means a different comment. Only a larger one is newer.
        return found.id > baseline.id

    # Same comment (or unknown id): freshness is whether it has been rewritten.
    # Bodies are compared with trailing whitespace ignored: a baseline carried
    # through shell substitution (`--baseline-body "$(cat before.md)"`) loses its
    # trailing newline, and that must not read as "the review was rewritten".
    if baseline.body is not None and _body_of(found) != _body_of(baseline):
        return True
    if baseline.body is None and not baseline.updated_at:
        return False  # an id-only baseline cannot see an edit
    # An empty `updated_at` is *unknown*, not a value: a `Comment` built without it
    # (the dataclass default) would otherwise compare "" != "2026-..." and report
    # an untouched comment as the new review, ending the wait immediately.
    if not baseline.updated_at:
        return False
    return found.updated_at != baseline.updated_at


def _body_of(value: Comment | Baseline) -> str:
    """The body, normalised so shell-carried copies compare equal."""
    return (value.body or "").replace("\r\n", "\n").strip()


def wait_for_contract_comment(
    repo: str,
    pr: int,
    *,
    newer_than: int | None = None,
    baseline: Comment | str | Baseline | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = DEFAULT_INTERVAL,
    author: str = WORKFLOW_AUTHOR,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Comment:
    """Poll until a contract comment written after `baseline` appears.

    `baseline` is the comment the caller already read. Prefer passing the whole
    `Comment`: the same comment is edited in place, so its id alone cannot show
    that the review was rewritten.
    """
    before = _baseline(baseline, newer_than)
    deadline = clock() + timeout
    last_seen: Comment | None = None

    while True:
        found = contract_comment(list_comments(repo, pr), author)
        if found is not None:
            last_seen = found
            if is_fresh(found, before):
                return found

        if clock() >= deadline:
            raise _timeout(pr, before, last_seen, timeout)

        sleep(interval)


def _baseline(
    baseline: Comment | str | Baseline | None, newer_than: int | None
) -> Baseline | None:
    """Normalise what the caller gave into the fields it actually knows."""
    if isinstance(baseline, Baseline):
        return baseline
    if isinstance(baseline, Comment):
        return Baseline(id=baseline.id, body=baseline.body, updated_at=baseline.updated_at)
    if isinstance(baseline, str):
        return Baseline(body=baseline)
    if newer_than is not None:
        return Baseline(id=newer_than)
    return None


def _timeout(
    pr: int, baseline: Baseline | None, last_seen: Comment | None, timeout: float
) -> ReviewTimeout:
    message = (
        f"no new review on #{pr} within {timeout:.0f}s. "
        "Check the Actions tab: the workflow may have failed, still be running, "
        "or been skipped (fork PRs are skipped)."
    )
    if baseline is not None and last_seen is not None:
        read = f"id {baseline.id}" if baseline.id is not None else "the copy read before the push"
        message += (
            f" A contract comment exists (id {last_seen.id}) but it is unchanged "
            f"from {read}, so it is the review this push is replacing."
        )
    elif baseline is not None and baseline.id is not None:
        message += f" No contract comment newer than id {baseline.id} appeared."
    elif baseline is not None:
        message += " No contract comment whose body differs from the baseline appeared."
    return ReviewTimeout(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--repo", default=None, help="owner/name; defaults to the current repo")
    parser.add_argument("--newer-than", type=int, default=None)
    parser.add_argument(
        "--baseline-body",
        default=None,
        help="body of the comment already read; the wait ends when it is rewritten",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    args = parser.parse_args(argv)

    try:
        repo = args.repo or _run(["gh", "repo", "view", "--json", "nameWithOwner",
                                  "--jq", ".nameWithOwner"]).stdout.strip()
        if not repo:
            print("error: cannot determine the repository", file=sys.stderr)
            return 1
        found = wait_for_contract_comment(
            repo,
            args.pr,
            newer_than=args.newer_than,
            baseline=args.baseline_body,
            timeout=args.timeout,
            interval=args.interval,
        )
    except ReviewTimeout as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except DriverError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    score = found.score_fifths
    shown = f"{score}/5" if score is not None else "score not recognised"
    print(f"comment {found.id} by {found.author} ({shown})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
