"""Decide what the loop does with the review it just read.

This module answers one question: given a review and the attempt number, is the
loop finished, does it go again, or does it hand the PR to a human? It owns no
state and runs nothing, so the rule can be tested on its own and read as a rule.

The success definition is the one settled in docs/TASKS.md and AGENT.md:

    success <=> score == 5
               or (score >= 4 and no P0/P1 issue)

The second half is not a concession, it is the case that actually happens. The
reviewer plateaus at 4/5 while listing findings it rates as minor (PR #27), so a
loop that only accepts 5/5 can never finish on those PRs, burns all five
attempts and reports failure on a branch that is fine.

Which issues count as blocking is decided in `parse_contract_comment.py`, next to
the parser, because it depends on how the bullet is written:

- `[P0]` and `[P1]` block.
- `[P2]` does not block, but only at score 4 or better. A 3/5 with one nit is
  still a 3/5, and the score is what says so.
- A bullet with no tag, or with a tag the contract does not define, blocks. The
  reviewer failing to state a severity is not evidence that the finding is safe.

The limit is `MAX_ITERATIONS` from `driver_iteration.py`. This module decides
whether another attempt would help; the limit decides whether it is allowed.
They are checked in that order here so the human-handoff message can say the
limit was the reason, rather than being reached by a guard that raised first.

**`iteration` counts attempts already made, not the attempt about to run.** That
is what makes "iteration 5, still not good enough" mean "stop": five attempts
have happened and none of them worked. The cycle therefore asks about `N - 1` on
the way in (four attempts made, so a fifth may still run) and about `N` on the
way out (five attempts made). Reading it the other way would make the fifth
attempt a no-op and cap the loop at four fixes.

**A fix that changes nothing ends the loop early.** Passing `previous=` lets the
rule compare the blocking findings of this review with the ones Pi was given. If
they are the same findings, the last attempt bought nothing and four more of the
same will not help either. `Outcome.REPEATED` says that, so a caller can report
it differently from "out of attempts". Success is still checked first: a clean
review is never a repeat, whatever `previous` held.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from driver_iteration import MAX_ITERATIONS
from parse_contract_comment import SUFFICIENT_SCORE, SUCCESS_SCORE, Review


class Outcome(Enum):
    """What the loop does next."""

    SUCCESS = "success"
    CONTINUE = "continue"
    # Out of attempts.
    HUMAN = "human"
    # Still throwing, but the last attempt changed nothing. Stopping here is a
    # judgement about the work, not about the budget, so it is its own outcome:
    # "we ran out of tries" and "more tries are pointless" are different things
    # for whoever picks this up, even though both end the loop.
    REPEATED = "repeated"


@dataclass(frozen=True)
class Decision:
    """The verdict, plus the line the operator will read.

    The message is part of the result rather than assembled by the caller: three
    call sites printing three different phrasings of "we are done" is how a log
    stops being trustworthy.
    """

    outcome: Outcome
    message: str
    iteration: int

    @property
    def is_done(self) -> bool:
        """True when the loop should stop, for any of the reasons above."""
        return self.outcome is not Outcome.CONTINUE


def _where(pr: int | None) -> str:
    return f"PR #{pr}" if pr is not None else "the PR"


def _blocking_reason(review: Review, blocking: tuple[str, ...]) -> str:
    """Why this review is not finished, in the terms the review itself used.

    A 3/5 with an empty Issues section is not a contradiction: the reviewer said
    the code is not good enough without naming a finding, and that alone is
    reason enough to keep going. Saying "0 blocking issues" here would read as a
    bug in the driver.
    """
    if blocking:
        noun = "issue" if len(blocking) == 1 else "issues"
        # Issue text usually ends in its own full stop. Keeping it would double
        # up against the one this message adds.
        listed = "; ".join(issue.rstrip(". ") for issue in blocking)
        return f"{len(blocking)} blocking {noun}: {listed}"
    return (
        f"no P0/P1 issues listed, but {review.score}/5 is below the "
        f"{SUFFICIENT_SCORE}/5 needed to stop"
    )


def _repeated_message(
    review: Review, previous: Review, iteration: int, pr: int | None
) -> str:
    """The line for a fix that came back with the same findings it was given.

    Both scores are printed. They can differ, because the score also reflects
    findings that are not blocking, and "still scores 3/5" would be a lie when it
    fell from 4/5. The blocking findings being identical is the reason for the
    verdict, so that is what the sentence is built around.
    """
    count = review.blocking_count
    noun = "issue" if count == 1 else "issues"
    if review.score == previous.score:
        scored = f"still scores {review.score}/5"
    elif review.score < previous.score:
        scored = f"scored {review.score}/5, down from {previous.score}/5"
    else:
        scored = f"scored {review.score}/5, up from {previous.score}/5"
    return (
        f"{_where(pr)} {scored} after iteration {iteration}, and the same "
        f"{count} blocking {noun} came back unchanged since iteration "
        f"{iteration - 1}. The last fix did not touch them, so another attempt "
        "would repeat it. A human needs to look at this PR."
    )


def decide(
    review: Review,
    *,
    iteration: int,
    pr: int | None = None,
    previous: Review | None = None,
) -> Decision:
    """Classify the review as success, continue, human needed, or repeated.

    `iteration` is how many attempts have been made when this review was read.

    `previous` is the review the just-finished attempt was given, so the two can
    be compared for a fix that changed nothing. Omit it to decide on this review
    alone, which is what the cycle does before it runs Pi: nothing has been
    attempted yet, so there is nothing to compare.

    Order matters. Success is checked first, so a clean review ends the loop even
    if `previous` happened to carry the same (now absent) findings. The repeat
    check then comes before the limit, so a pointless loop stops when it is
    detected rather than when the budget runs out. The limit stays last, and
    `iteration == 0` can never report it because nothing has been attempted.
    """
    if review.is_clean:
        if review.score >= SUCCESS_SCORE:
            message = f"{_where(pr)} is ready for merge ({review.score}/5)."
        else:
            # Only reachable at the sufficient score, since a lower one is never
            # clean. Say what was tolerated, so a 4/5 stop is not mistaken for a
            # perfect review. An empty Issues section says nothing extra.
            minor = len(review.issues) - review.blocking_count
            if minor:
                detail = f"no P0/P1 issues, {minor} P2 issue{'s' if minor != 1 else ''}"
            else:
                detail = "no issues"
            message = f"{_where(pr)} is ready for merge ({review.score}/5: {detail})."
        return Decision(Outcome.SUCCESS, message, iteration)

    reason = _blocking_reason(review, review.blocking_issues)

    # Same blocking findings as last time means the attempt bought nothing.
    #
    # Two guards make this safe. `iteration > 0` keeps the cycle's first review
    # out of it: nothing has been attempted, so nothing can have been a no-op.
    # The non-empty check is not redundant, because `is_clean` lets a review with
    # no issues through at a low score: two of those fingerprint empty, would
    # compare equal, and would report a repeat for a pair of reviews that list
    # nothing at all. "No findings twice" is a score problem, not a no-op fix.
    if (
        previous is not None
        and iteration > 0
        and review.blocking_fingerprint
        and review.blocking_fingerprint == previous.blocking_fingerprint
    ):
        message = _repeated_message(review, previous, iteration, pr)
        return Decision(Outcome.REPEATED, message, iteration)

    if iteration < MAX_ITERATIONS:
        message = (
            f"{_where(pr)} scored {review.score}/5. {reason[0].upper()}{reason[1:]}. "
            f"Continuing to iteration {iteration + 1}/{MAX_ITERATIONS}."
        )
        return Decision(Outcome.CONTINUE, message, iteration)

    message = (
        f"{_where(pr)} still scores {review.score}/5 after {iteration} iterations, "
        f"the hard limit of {MAX_ITERATIONS}. {reason[0].upper()}{reason[1:]}. "
        "A human needs to look at this PR."
    )
    return Decision(Outcome.HUMAN, message, iteration)
