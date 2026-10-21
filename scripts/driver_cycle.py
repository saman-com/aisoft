"""Run one complete fix cycle: read review, let Pi fix, push, read the new review.

This is the piece that makes the loop a loop instead of a pile of tools. It owns
only the ordering and the stopping; every step is delegated to the module that
already does it:

    driver_pr        which PR this branch is on
    driver_comments  which comment is the review, and when a newer one exists
    parse_contract_comment  what the review says
    driver_pi        running Pi with the Agent Prompt
    driver_git       committing and pushing without force

The order is what the cycle is:

    read review -> Pi -> commit & push -> wait for a NEWER review -> parse

Each step is skipped for a reason, never for convenience:

- Pi is not run when the review is clean: there is nothing to fix, and a push
  with no commit would burn a whole iteration and teach the loop nothing.
- Pi is not run when the review cannot be parsed. Half a review is not a prompt.
- The wait happens after the push, so the review it finds was written about the
  code Pi just produced. Waiting first would hand back the review already read.
- The wait is given the whole comment from before the push, not its id: the
  poster edits one comment in place, so the id survives a rewrite. The body is
  what changes.

Exit codes match the rest of the driver, so a caller can script around them:

    0  the cycle completed and a newer review was read
    1  the environment failed (gh, git, or pi)
    2  there is nothing to do (no PR, no review, review already clean, no diff)
    3  the review does not satisfy the contract

Usage:

    python3 scripts/driver_cycle.py --dry-run
    python3 scripts/driver_cycle.py --pr 31 --iteration 1
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import driver_log
from driver_comments import (
    DEFAULT_INTERVAL,
    DEFAULT_TIMEOUT,
    WORKFLOW_AUTHOR,
    Comment,
    DriverError,
    ReviewTimeout,
    contract_comment,
    list_comments,
    wait_for_contract_comment,
)
from driver_git import GitError, NothingToPush, PushResult, commit_and_push
from driver_iteration import check_iteration, describe_iteration
from driver_pi import DEFAULT_TIMEOUT as PI_DEFAULT_TIMEOUT
from driver_pi import PiError, PiRun, run_pi
from driver_pr import GhFailed, NoPullRequest, PullRequest, resolve
from parse_contract_comment import ParseError, Review, parse_comment


class NoReview(DriverError):
    """The PR has no contract comment yet, so there is nothing to act on."""


@dataclass(frozen=True)
class CycleResult:
    """What one cycle did, kept so the caller can report and decide."""

    pr: PullRequest
    before: Review
    after: Review | None
    pi_ok: bool
    pushed: bool
    pi_ran: bool
    before_comment: Comment | None = None
    after_comment: Comment | None = None
    push: PushResult | None = None
    run: PiRun | None = field(default=None, repr=False)


def read_before(repo: str, pull_request: PullRequest, author: str) -> tuple[Review, Comment]:
    """Fetch and parse the contract comment the cycle is about to act on."""
    found = contract_comment(list_comments(repo, pull_request.number), author)
    if found is None:
        raise NoReview(
            f"no contract review on #{pull_request.number} yet. "
            "Wait for the workflow to post one (`gh pr checks`), then run this again."
        )
    review = parse_comment(found.body, comment_id=found.id, pr=pull_request.number)
    return review, found


def run_cycle(
    *,
    repo: str,
    iteration: int,
    branch: str | None = None,
    pr: int | None = None,
    dry_run: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = DEFAULT_INTERVAL,
    author: str = WORKFLOW_AUTHOR,
    pi_timeout: float = PI_DEFAULT_TIMEOUT,
    quiet: bool = False,
) -> CycleResult:
    """Run one iteration of the fix loop. Raises rather than returning half a cycle.

    The iteration is checked against the hard limit before anything else, so a
    sixth attempt is refused before Pi runs, before a commit exists and before a
    push lands. Everything below this line is one permitted attempt.
    """
    check_iteration(iteration)
    pull_request = resolve(branch)
    if pr is not None and pull_request.number != pr:
        raise NoPullRequest(
            f"PR #{pr} is not the open pull request for this branch "
            f"({pull_request.head_ref!r} has #{pull_request.number}). "
            "The cycle commits to the checked-out branch, so it can only act on "
            "the PR for that branch."
        )

    before, before_comment = read_before(repo, pull_request, author)
    if not quiet:
        driver_log.log(
            driver_log.describe_review(
                before,
                pr=pull_request.number,
                comment_id=before_comment.id,
                iteration=iteration,
            )
        )

    if dry_run:
        if not quiet:
            driver_log.log("dry run: reviewed and parsed, nothing changed.")
        return CycleResult(
            pr=pull_request, before=before, after=None,
            pi_ok=False, pushed=False, pi_ran=False,
            before_comment=before_comment,
        )

    if before.is_clean:
        if not quiet:
            driver_log.log("already 5/5 with no issues; nothing to fix.")
        return CycleResult(
            pr=pull_request, before=before, after=before,
            pi_ok=True, pushed=False, pi_ran=False,
            before_comment=before_comment, after_comment=before_comment,
        )

    if not quiet:
        driver_log.log(
            f"running pi ({describe_iteration(iteration)}) from the Agent Prompt"
        )
    run = run_pi(before.agent_prompt, timeout=pi_timeout)

    if run.returncode != 0:
        raise PiError(
            f"pi exited {run.returncode} on {describe_iteration(iteration)}. "
            f"stderr: {run.stderr.strip() or '(empty)'}"
        )
    if not quiet and run.stdout.strip():
        driver_log.log("--- pi output ---\n" + run.stdout.rstrip() + "\n--- end pi output ---")

    push = commit_and_push(
        iteration=iteration,
        score=before.score,
        issues=len(before.issues),
        # An explicit PR found by branch always has a head ref; a caller-supplied
        # number never reaches this point without one either.
        expected_branch=pull_request.head_ref or None,
    )
    if not quiet:
        driver_log.log(f"pushed {push.commit} to {push.branch}")

    if not quiet:
        driver_log.log(
            f"waiting up to {timeout:.0f}s for a newer review "
            f"(polling every {interval:.0f}s)"
        )
    after_comment = wait_for_contract_comment(
        repo,
        pull_request.number,
        baseline=before_comment,
        timeout=timeout,
        interval=interval,
        author=author,
    )
    after = parse_comment(
        after_comment.body, comment_id=after_comment.id, pr=pull_request.number
    )
    if not quiet:
        driver_log.log(
            driver_log.describe_review(
                after,
                pr=pull_request.number,
                comment_id=after_comment.id,
                iteration=iteration + 1,
            )
        )

    return CycleResult(
        pr=pull_request, before=before, after=after,
        pi_ok=True, pushed=True, pi_ran=True,
        before_comment=before_comment, after_comment=after_comment,
        push=push, run=run,
    )


def summarize(result: CycleResult) -> str:
    """One line describing what changed, for the operator and the issue record."""
    before = f"{result.before.score}/5"
    if result.after is None:
        return f"{before} (dry run, nothing changed)"
    after = f"{result.after.score}/5"
    if result.after.score > result.before.score:
        delta = f"{before} -> {after}"
    elif result.after.score == result.before.score:
        delta = f"{before} -> {after} (same score, issues changed)" if (
            result.after.issues != result.before.issues
        ) else f"{before} -> {after} (unchanged)"
    else:
        delta = f"{before} -> {after} (score dropped)"
    return f"{delta} on {result.pr.url or f'#{result.pr.number}'}"


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, default=None, help="must match the PR for this branch")
    parser.add_argument("--branch", default=None, help="defaults to the current branch")
    parser.add_argument("--repo", default=None, help="owner/name; defaults to the current repository")
    parser.add_argument(
        "--iteration",
        type=int,
        default=1,
        help="this attempt's number, 1..5; a value outside that range is refused",
    )
    parser.add_argument("--dry-run", action="store_true", help="read and parse, change nothing")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--pi-timeout", type=float, default=PI_DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    try:
        result = run_cycle(
            repo=args.repo or repository(),
            iteration=args.iteration,
            branch=args.branch,
            pr=args.pr,
            dry_run=args.dry_run,
            timeout=args.timeout,
            interval=args.interval,
            pi_timeout=args.pi_timeout,
        )
    except ParseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 3
    except (NoPullRequest, NoReview, ReviewTimeout, NothingToPush) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (GhFailed, PiError, GitError, DriverError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    driver_log.log(summarize(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
