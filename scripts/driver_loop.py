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

Exit codes follow the rest of the driver: 0 the loop ran to a verdict (whatever
the verdict was), 1 the environment or the loop failed, 2 there was nothing to
do, 3 the review did not satisfy the contract.

Usage:

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
from driver_comments import DEFAULT_INTERVAL, DEFAULT_TIMEOUT, DriverError, ReviewTimeout
from driver_cycle import CycleResult, NoReview, repository, run_cycle, summarize
from driver_decision import Decision, Outcome
from driver_git import GitError, NothingToPush
from driver_iteration import MAX_ITERATIONS, IterationLimit, check_iteration, describe_iteration
from driver_pi import DEFAULT_TIMEOUT as PI_DEFAULT_TIMEOUT
from driver_pi import PiError
from driver_pr import GhFailed, NoPullRequest, PullRequest
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


def run_loop(
    *,
    repo: str,
    start_iteration: int = 1,
    branch: str | None = None,
    pr: int | None = None,
    dry_run: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = DEFAULT_INTERVAL,
    author: str | None = None,
    pi_timeout: float = PI_DEFAULT_TIMEOUT,
    quiet: bool = False,
    cycle: Callable[..., CycleResult] = run_cycle,
) -> LoopResult:
    """Run cycles until the rule says stop. Raises rather than half-finishing.

    `cycle` is the one seam: it defaults to the real `run_cycle`, and tests pass a
    scripted stand-in so the repetition can be checked without Pi or gh. Every
    keyword other than `iteration` is passed through to it unchanged.
    """
    check_iteration(start_iteration)
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
        result = cycle(iteration=iteration, **extra)
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
    except (GhFailed, PiError, GitError, IterationLimit, LoopError, DriverError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.dry_run:
        # Said before the status, not after: the verdict is the line a reader
        # stops on, and appending a note below it would bury it. A dry run always
        # stops on "another attempt is due", because nothing was fixed, so this
        # says plainly that no attempt ran.
        driver_log.log("dry run: nothing was changed and no attempt was made.")
    announce(loop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
