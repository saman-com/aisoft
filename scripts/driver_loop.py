"""Run the fix loop: cycle after cycle until the rule says stop.

`driver_cycle.py` does one attempt. This repeats it, and the whole of this module
is the repetition: which number to hand over next, when to stop, and what to
print at the end. Everything it decides with already exists elsewhere:

    driver_cycle.run_cycle   one attempt: read, fix, push, wait, read again
    driver_decision.decide   whether another attempt is useful
    driver_iteration         whether another attempt is allowed
    driver_status.report     what a stopped loop prints

The stop test is one line, because the cycle has already decided:
`result.decision.outcome is not Outcome.CONTINUE`. Nothing here re-reads the
score, and nothing here re-derives the rule. A second copy of the stopping rule
in the loop is how the loop and the log start disagreeing about why a run ended.

Three things are worth stating because they are easy to get wrong:

- **The cycle waits, the loop does not.** `run_cycle` returns only after it has
  read a review written about the push it just made, so the next cycle's
  `read_before` is already the newest review. Waiting again between cycles would
  spend a second timeout on a review that had arrived.
- **The loop never asks for attempt six.** `run_cycle` refuses it, so the ask
  would surface as a crash in the middle of a run. The loop is the last place
  that number is written down, so it is where it must not be written. A
  `CONTINUE` on attempt 5 is a contradiction between the rule and the limit, and
  it raises rather than printing "another attempt is due" for an attempt that
  cannot happen.
- **A dry run runs once.** Nothing changes, so a second pass would read the same
  review and print the same thing.

With `--create` the loop also starts the run: it ensures the branch has a pull
request (opening one only when none is open), waits for the first contract review,
and then runs the cycles against that PR. Without it nothing is looked up and the
first cycle must find its own PR and review, which is what Slice 4 did.

`--create` and `--dry-run` are refused together. A dry run promises to change
nothing, and opening a pull request is a change.

Exit codes follow the rest of the driver: 0 the loop ran to a verdict (whatever
the verdict was), 1 the environment or the loop failed, 2 there was nothing to
do, 3 the review did not satisfy the contract.

A dry run is the one case where 0 does not mean "reached a verdict". It changes
nothing, so when the PR is not already clean it stops on `CONTINUE`: the loop
says another attempt is due without claiming one happened. That is a successful
dry run, so it exits 0 and the status line says `NOT FINISHED - ANOTHER ATTEMPT
IS DUE`. Reading the exit code as "the PR is ready" is what the headline is for;
the code only says the run itself completed.

Usage:

    python3 scripts/driver_loop.py --create        # no PR yet: create, then loop
    python3 scripts/driver_loop.py --dry-run
    python3 scripts/driver_loop.py --pr 31 --repo owner/name
    python3 scripts/driver_loop.py --iteration 3   # resume a half-run loop
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import driver_log
from driver_comments import (
    DEFAULT_INTERVAL,
    DEFAULT_TIMEOUT,
    WORKFLOW_AUTHOR,
    DriverError,
    ReviewTimeout,
    comment_time,
    contract_comment,
    list_comments,
    wait_for_contract_comment,
)
from driver_cycle import CycleResult, NoReview, repository, run_cycle, summarize
from driver_decision import Decision, Outcome
from driver_git import (
    GitError,
    NothingToPush,
    commits_on_branch,
    current_branch,
    last_commit_time,
)
from driver_iteration import MAX_ITERATIONS, IterationLimit, check_iteration, describe_iteration
from driver_pi import DEFAULT_TIMEOUT as PI_DEFAULT_TIMEOUT
from driver_pi import PiError
from driver_pr import GhFailed, NoPullRequest, PullRequest, ensure_pull_request
from driver_status import Status, report
from parse_contract_comment import ParseError


class LoopError(DriverError):
    """The loop cannot continue, and no amount of retrying would change that."""


@dataclass(frozen=True)
class LoopResult:
    """Every cycle the loop ran, in order.

    The whole history is kept rather than just the last result: the evidence a
    run is judged on is the sequence of scores, and "3 -> 4 -> 5" says something
    a single final 5/5 does not.
    """

    iterations: tuple[CycleResult, ...]

    @property
    def final(self) -> Decision:
        """The verdict of the last cycle, which is why the loop stopped."""
        if not self.iterations:
            raise LoopError("the loop stopped without running a single attempt")
        decision = self.iterations[-1].decision
        if decision is None:
            raise LoopError("the loop stopped on a cycle that produced no verdict")
        return decision

    @property
    def pr(self) -> PullRequest | None:
        """The PR, when a cycle got far enough to identify one."""
        return self.iterations[-1].pr if self.iterations else None

    def status(self) -> Status:
        return report(self.final, pr=self.pr)

    def scores(self) -> list[str]:
        """The score after each attempt, e.g. `['3/5', '4/5', '5/5']`.

        A cycle that stopped before a new review existed has no `after`, so its
        `before` is the score it was handed. That keeps one entry per attempt.
        """
        shown = []
        for result in self.iterations:
            review = result.after if result.after is not None else result.before
            shown.append(f"{review.score}/5")
        return shown


def start(
    *,
    repo: str,
    branch: str,
    pr: int | None = None,
    base: str = "master",
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = DEFAULT_INTERVAL,
    author: str = WORKFLOW_AUTHOR,
    quiet: bool = False,
) -> PullRequest:
    """Get the branch a PR, and wait for the review this run should act on.

    Returns the PR. This is the step Slice 4 did not have: a run that starts from a branch with
    commits and no pull request. Two things happen, in this order, and the order
    is the whole point:

    - **Ensure the PR.** `driver_pr.ensure_pull_request` is the only place a PR is
      opened, so a branch that already has one is reused by the same call and
      there is no second create path to keep in step with the first.
    - **Wait for a review.** `run_cycle` raises `NoReview` when the PR has no
      contract comment, and a PR opened a second ago never does: the workflow has
      not run yet. Waiting here is what lets the first cycle read a review instead
      of failing on a fresh PR.

    The wait needs a baseline, and a created PR and a reused one need opposite
    ones. A PR that was just created has no contract comment at all, so `None` is
    right there: the first one that appears is the review of the first push,
    whatever its id. A reused PR already has one, and that comment describes an
    earlier iteration, so waiting with no baseline would return at once and the
    first cycle would act on a stale verdict. For a reused PR the baseline is the
    comment that is already there, which makes the wait mean "wait for the review
    of the newest push".

    A reused PR whose newest comment is at least as new as the newest commit is
    already reviewed: there is no rewrite coming, and waiting would block until
    the timeout and then report "nothing to do" on a PR that has a valid current
    review. Rerunning `--create` on a branch that was just reviewed is the common
    case, so that review is returned and the loop starts at once. When the
    comparison cannot be made (no commit time, no `updated_at`) the wait still
    happens, because acting on a review that may be stale is the worse mistake.

    A caller-supplied `pr` must match what the branch actually has. The cycle
    commits to the checked-out branch, so acting on a different PR would push a fix
    for one review onto another.

    `base` must be a different branch. `--branch feature --base feature` gives an
    empty `git log` and asks `gh` to open a PR from a branch into itself, which is
    refused deeper down with a message that does not say why.
    """
    if branch == base:
        raise LoopError(
            f"{branch!r} cannot be its own base. A pull request is a branch asking "
            "to be merged into another one, so --base has to name a different "
            "branch than --branch."
        )
    commits = commits_on_branch(branch, base=base)
    pull_request, created = ensure_pull_request(
        branch=branch, base=base, commits=commits
    )
    if pr is not None and pull_request.number != pr:
        raise LoopError(
            f"PR #{pr} is not the open pull request for {branch!r} "
            f"(that branch has #{pull_request.number}). The loop commits to the "
            "checked-out branch, so it can only act on the PR for that branch."
        )
    if not quiet:
        # `created` is reported and nothing else depends on it: the loop acts on
        # the PR, and who opened it does not change what happens next.
        verb = "created" if created else "reused"
        count = len(commits)
        noun = "commit" if count == 1 else "commits"
        driver_log.log(
            f"{verb} PR #{pull_request.number} for {branch!r} into {base!r} "
            f"({count} {noun}): {pull_request.url}"
        )

    baseline = None
    already_current = None
    if not created:
        # Reused, so whatever the poster already wrote is the review of an earlier
        # push and is what the wait has to beat. `contract_comment` returns the
        # newest one, or `None` for a PR someone opened by hand and the workflow
        # has not reached yet - then waiting from scratch is right.
        baseline = contract_comment(list_comments(repo, pull_request.number), author)
        if baseline is not None:
            # A comment written after the newest commit is the review of that
            # commit, not of an earlier push, so there is nothing left to wait
            # for. Comparing timestamps is what makes a plain rerun of `--create`
            # proceed instead of timing out on a review it already has.
            pushed_at = last_commit_time(branch)
            reviewed_at = comment_time(baseline)
            if (
                pushed_at is not None
                and reviewed_at is not None
                and reviewed_at >= pushed_at
            ):
                already_current = baseline
    if already_current is not None:
        if not quiet:
            driver_log.log(
                f"the review on #{pull_request.number} already covers the newest "
                "push; starting the loop"
            )
        return pull_request
    if not quiet:
        # A reused PR may already have a review, so what is being waited for is the
        # one the newest push produces; saying "the first review" there would
        # promise something that may already have arrived.
        wanted = "the first review" if created else "a review of the newest push"
        driver_log.log(
            f"waiting up to {timeout:.0f}s for {wanted} "
            f"(polling every {interval:.0f}s)"
        )
    wait_for_contract_comment(
        repo,
        pull_request.number,
        baseline=baseline,
        timeout=timeout,
        interval=interval,
        author=author,
    )
    if not quiet:
        driver_log.log(f"the review on #{pull_request.number} is in; starting the loop")
    return pull_request


def run_loop(
    *,
    repo: str,
    start_iteration: int = 1,
    branch: str | None = None,
    pr: int | None = None,
    create: bool = False,
    base: str = "master",
    dry_run: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = DEFAULT_INTERVAL,
    author: str | None = None,
    pi_timeout: float = PI_DEFAULT_TIMEOUT,
    quiet: bool = False,
    cycle: Callable[..., CycleResult] | None = None,
) -> LoopResult:
    """Run cycles until the rule says stop. Raises rather than half-finishing.

    `cycle` is the one seam: it defaults to the real `run_cycle`, and tests pass a
    scripted stand-in so the repetition can be checked without Pi or gh. Every
    keyword other than `iteration` is passed through to it unchanged.

    The default is resolved here rather than in the signature. A `= run_cycle`
    default would be bound when this module is imported, so replacing
    `driver_loop.run_cycle` to test the seam would silently have no effect.

    `create=True` runs `start` first: ensure the PR, wait for the review that is
    not the one already read, and then loop against that PR. It is refused with
    `dry_run`, which promises to change nothing, and opening a PR is a change.
    """
    perform = cycle if cycle is not None else run_cycle
    check_iteration(start_iteration)
    if create and dry_run:
        raise LoopError(
            "--create cannot be combined with --dry-run: a dry run changes nothing, "
            "and opening a pull request is a change. Run --create on its own, or "
            "--dry-run on the PR that already exists."
        )

    if create:
        # The branch is resolved once, here, and handed to every cycle below: the
        # PR was ensured for this branch, and a cycle that resolved a different one
        # would push the fix somewhere the review is not.
        branch = branch or current_branch()
        pull_request = start(
            repo=repo,
            branch=branch,
            pr=pr,
            base=base,
            timeout=timeout,
            interval=interval,
            author=author or WORKFLOW_AUTHOR,
            quiet=quiet,
        )
        # The number comes from the PR that was ensured, not from the caller, so
        # every cycle below acts on the PR this run actually started against. A
        # mismatch between an explicit `pr` and the branch was refused in `start`.
        pr = pull_request.number

    if not quiet:
        driver_log.log(
            f"loop: starting at {describe_iteration(start_iteration)}, "
            f"at most {MAX_ITERATIONS} attempts"
        )

    extra = {
        "repo": repo,
        "branch": branch,
        "pr": pr,
        "dry_run": dry_run,
        "timeout": timeout,
        "interval": interval,
        "pi_timeout": pi_timeout,
        "quiet": quiet,
    }
    if author is not None:
        extra["author"] = author

    iterations: list[CycleResult] = []
    iteration = start_iteration
    while True:
        try:
            result = perform(iteration=iteration, **extra)
        except NothingToPush as error:
            # Pi ran and changed nothing, so there is no push and no review will
            # ever be written about it. Waiting would poll for a review of a
            # commit that does not exist until `timeout`, so the loop stops here.
            #
            # It is not "nothing to do", which is what exit 2 says elsewhere: the
            # branch was handed a finding, an attempt was spent, and the attempt
            # produced no diff. That needs a person, and it needs the number of
            # the attempt that stalled.
            raise LoopError(
                f"{describe_iteration(iteration)} changed nothing, so nothing was "
                "pushed and no review will follow. Pi did not edit the code the "
                "review pointed at; a human needs to look at this."
            ) from error
        iterations.append(result)
        if not quiet:
            driver_log.log(f"{describe_iteration(iteration)}: {summarize(result)}")

        decision = result.decision
        if decision is None:
            # Every path through `run_cycle` sets one, so this is a broken
            # contract rather than a state to handle. Guessing the verdict from
            # the score would print a status nobody had decided on.
            raise LoopError(
                f"{describe_iteration(iteration)} finished without a verdict. "
                "There is no safe way to guess whether to stop."
            )

        if decision.outcome is not Outcome.CONTINUE:
            break
        if dry_run:
            # Nothing was changed, so the next cycle would read the same review
            # and reach the same CONTINUE. One pass is the whole answer.
            break
        if iteration >= MAX_ITERATIONS:
            raise LoopError(
                f"the rule asked for iteration {iteration + 1} but the limit is "
                f"{MAX_ITERATIONS}. A CONTINUE on the last attempt means the "
                "stopping rule and the limit disagree; stopping there would print "
                "'another attempt is due' for an attempt that cannot run."
            )
        iteration += 1

    return LoopResult(iterations=tuple(iterations))


def announce(loop: LoopResult) -> Status:
    """Print the final status once and return it. The last thing a run prints."""
    status = loop.status()
    driver_log.log(status.render())
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, default=None, help="must match the PR for this branch")
    parser.add_argument("--branch", default=None, help="defaults to the current branch")
    parser.add_argument("--repo", default=None, help="owner/name; defaults to the current repository")
    parser.add_argument(
        "--iteration",
        type=int,
        default=1,
        help="first attempt of this run, 1..5; a value outside that range is refused",
    )
    parser.add_argument(
        "--create",
        action="store_true",
        help=(
            "ensure the branch has a PR (create only if none is open), wait for a "
            "review newer than the one it already has, then loop"
        ),
    )
    parser.add_argument(
        "--base", default="master", help="the branch the PR is opened against"
    )
    parser.add_argument("--dry-run", action="store_true", help="read and parse, change nothing")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--pi-timeout", type=float, default=PI_DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    try:
        loop = run_loop(
            repo=args.repo or repository(),
            start_iteration=args.iteration,
            branch=args.branch,
            pr=args.pr,
            create=args.create,
            base=args.base,
            dry_run=args.dry_run,
            timeout=args.timeout,
            interval=args.interval,
            pi_timeout=args.pi_timeout,
        )
    except ParseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 3
    except (NoPullRequest, NoReview, ReviewTimeout) as error:
        # Nothing to do: no PR, no review yet, or no review arrived in time. These
        # are states of the world, not failures of the run, so they keep exit 2.
        # `NothingToPush` is deliberately absent: the loop turns it into a
        # `LoopError` (exit 1) because a spent attempt that produced no diff is
        # not "nothing to do".
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (GhFailed, PiError, GitError, IterationLimit, LoopError, DriverError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.dry_run:
        # Said before the status, not after: the verdict is the line a reader
        # stops on, and appending a note below it would bury it.
        #
        # It is printed whatever the verdict, because both cases need it and it is
        # true in both. On a PR that is not clean the run stops on `CONTINUE` and
        # the status says `NOT FINISHED`, which on its own reads like a run that
        # stopped one attempt short instead of one that never tried. On a clean PR
        # the status says `READY FOR MERGE`, and this line is what tells the reader
        # the PR was already clean rather than newly fixed.
        driver_log.log("dry run: nothing was changed and no attempt was made.")
    announce(loop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
