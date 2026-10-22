"""The iteration counter and its hard limit of five.

Slice 3 runs one cycle. This adds the thing that makes repeating it safe: a
counter that cannot exceed five, refuses to start below one, and says so in the
same words in the log and in the error.

Three decisions are deliberate:

- **The limit lives here and nowhere else.** `MAX_ITERATIONS` is imported by the
  loop, the CLI and the tests. A second copy in a flag default is a second answer
  to "how many attempts are left".
- **The guard runs before the work, not after.** `check_iteration` is called on
  the way into an iteration, so attempt six is refused before Pi runs, before a
  commit exists and before a push lands. Validating afterwards would push a sixth
  fix and then admit it should not have.
- **The guard is a separate question from "should we iterate".** This module
  answers "is another attempt allowed". T4.2 answers "is another attempt useful"
  (score 5, clean enough, no P0/P1 left). Keeping them apart means a change to
  the stopping rule cannot widen the blast radius to six attempts by accident.

The counter is passed in rather than held as module state. The loop owns its own
count, so two loops in one process (a test, a retry) cannot corrupt each other.
"""

from __future__ import annotations

from driver_comments import DriverError

# The hard cap on fix attempts. Five is the number in the design, in CONTRACT.md's
# surrounding docs and in the issue; it is not a tuning knob to raise mid-run.
MAX_ITERATIONS = 5


class IterationLimit(DriverError):
    """The loop is not allowed to run another attempt.

    A `DriverError` because the CLIs already map that to exit 1: the run could not
    do what it was asked, and repeating it unchanged will not help.
    """


def check_iteration(iteration: int) -> int:
    """Return `iteration` if it may run, otherwise raise `IterationLimit`.

    Both ends are checked. A caller that starts at 0 would otherwise get six
    attempts out of a limit of five, and the off-by-one would look like a
    generous limit rather than a bug.
    """
    if iteration < 1:
        raise IterationLimit(
            f"iteration {iteration} is not a real attempt: numbering starts at 1. "
            f"The loop runs at most {MAX_ITERATIONS} iterations."
        )
    if iteration > MAX_ITERATIONS:
        raise IterationLimit(
            f"iteration {iteration} is past the hard limit of {MAX_ITERATIONS}. "
            "There will be no further attempts: the PR needs a human."
        )
    return iteration


def describe_iteration(iteration: int) -> str:
    """The `iteration N/5` label used in logs and status lines."""
    return f"iteration {iteration}/{MAX_ITERATIONS}"


def is_final(iteration: int) -> bool:
    """True when this iteration is the last one the limit allows."""
    return iteration == MAX_ITERATIONS
