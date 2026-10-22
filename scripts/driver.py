"""Read the latest contract review for a pull request.

Slice 2 is read-only: this finds the PR, fetches the contract comment, parses it,
and prints what it found. It decides nothing and changes nothing - Slice 3 adds
the fix cycle on top.

Exit codes are the interface later slices and the operator rely on:

    0  a review was read and printed
    1  the environment failed (gh missing or unauthenticated)
    2  there is nothing to read yet (no PR, or no review comment)
    3  the comment exists but does not satisfy the contract

Exit 2 means "try again later or create the PR"; exit 3 means "the review pipeline
is broken". Collapsing them would hide a broken contract behind a wait.

Usage:

    python3 scripts/driver.py --pr 27
    python3 scripts/driver.py --pr 27 --wait --timeout 600
    python3 scripts/driver.py --branch my-branch --json
    python3 scripts/driver.py --pr 27 --newer-than 6306554503
    python3 scripts/driver.py --pr 27 --wait --baseline-body "$(cat before.md)"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import driver_log
from driver_comments import (
    DEFAULT_INTERVAL,
    DEFAULT_TIMEOUT,
    Comment,
    DriverError,
    ReviewTimeout,
    contract_comment,
    list_comments,
    wait_for_contract_comment,
)
from driver_decision import decide
from driver_pr import GhFailed, NoPullRequest, PullRequest, find_pull_request, resolve
from driver_status import report
from parse_contract_comment import ParseError, Review, parse_comment


class NoReview(DriverError):
    """The PR has no contract comment yet."""


def repository() -> str:
    """Return `owner/name` for the current checkout."""
    import subprocess

    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise GhFailed(f"cannot run gh: {error}") from error
    name = result.stdout.strip()
    if result.returncode != 0 or not name:
        raise GhFailed(
            "cannot determine the repository: "
            f"{result.stderr.strip() or 'gh repo view failed'}"
        )
    return name


def read_review(repo: str, pr: int) -> tuple[Review, Comment]:
    """Fetch and parse the newest contract comment on `pr`."""
    found = contract_comment(list_comments(repo, pr))
    if found is None:
        raise NoReview(f"no contract comment on #{pr} yet")
    return parse_comment(found.body, comment_id=found.id, pr=pr), found


def _pull_request(args: argparse.Namespace) -> PullRequest:
    if args.pr is not None:
        return PullRequest(
            number=args.pr, url="(given on the command line)", state="OPEN", head_ref=""
        )
    return resolve(args.branch)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, default=None, help="PR number; found from the branch if omitted")
    parser.add_argument("--branch", default=None, help="defaults to the current branch")
    parser.add_argument("--repo", default=None, help="owner/name; defaults to the current repository")
    parser.add_argument("--wait", action="store_true", help="poll until a new review appears")
    parser.add_argument("--newer-than", type=int, default=None, help="comment id already read")
    parser.add_argument(
        "--baseline-body",
        default=None,
        help=(
            "body of the contract comment read before the push; a wait returns when "
            "the comment is rewritten. Prefer this over --newer-than: the poster "
            "edits one comment in place, so its id does not change"
        ),
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--json", action="store_true", help="print the parsed review as JSON")
    parser.add_argument("--iteration", type=int, default=1, help="shown in the log only")
    args = parser.parse_args(argv)

    repo: str = ""
    comment: Comment | None = None
    pr_number: int = args.pr or 0
    try:
        repo = args.repo or repository()
        pull_request = _pull_request(args)
        pr_number = pull_request.number
        if args.wait:
            comment = wait_for_contract_comment(
                repo,
                pull_request.number,
                newer_than=args.newer_than,
                baseline=args.baseline_body,
                timeout=args.timeout,
                interval=args.interval,
            )
            review = parse_comment(
                comment.body, comment_id=comment.id, pr=pull_request.number
            )
        else:
            review, comment = read_review(repo, pull_request.number)
    except (NoPullRequest, NoReview, ReviewTimeout) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except ParseError as error:
        print(
            driver_log.describe_failure(
                str(error),
                pr=pr_number,
                comment_id=comment.id if comment else None,
            ),
            file=sys.stderr,
        )
        return 3
    except (GhFailed, DriverError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "pr": pull_request.number,
                    "url": pull_request.url,
                    "comment_id": comment.id,
                    "score": review.score,
                    "issues": list(review.issues),
                    "agent_prompt": review.agent_prompt,
                },
                indent=2,
            )
        )
    else:
        driver_log.log(
            driver_log.describe_review(
                review,
                pr=pull_request.number,
                comment_id=comment.id,
                iteration=args.iteration,
            )
        )
        # The same rule the cycle exits on, so this read-only view can never
        # claim "ready" for a review the loop would keep working on. The status
        # formatting is shared with the cycle, so both print one shape.
        verdict = decide(review, iteration=args.iteration, pr=pull_request.number)
        if verdict.is_done:
            driver_log.log(report(verdict, pr=pull_request).render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
