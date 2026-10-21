"""The final status a stopped loop prints.

The driver runs unattended, so this module produces the only output most runs are
ever read through. It is deliberately thin: `driver_decision.py::decide` already
knows why the loop stopped and says so in `Decision.message`. The status frames
that with a greppable headline, the PR address and the decision's own reason:

    READY FOR MERGE (4/5)
    https://github.com/saman-com/aisoft/pull/29
    PR #29 is ready for merge (4/5: no P0/P1 issues, 1 P2 issue).

    STOPPED AFTER 5 ITERATIONS - HUMAN REVIEW NEEDED
    https://github.com/saman-com/aisoft/pull/29
    PR #29 still scores 3/5 after 5 iterations, the hard limit of 5. ...

Three rules it holds to:

- **Only `SUCCESS` says ready for merge.** The obvious next step after that line
  is `gh pr merge`, so printing it for a review that is not clean is the one
  mistake with a real cost to the repository. `Status.is_ready` is derived from
  the outcome, so no caller can print the headline for a different verdict.
- **A stop always carries the address.** The reader is being asked to go and do
  something, and the URL is what lets them. `driver_pr.resolve` hands one back;
  `#29` is the fallback when gh returned a PR without a url.
- **The reason comes from the decision.** Re-deriving it here would let the
  printed reason and the rule that stopped the loop disagree, which is how a log
  stops being trustworthy.

Exit codes live in `EXIT_CODES`. Every outcome exits 0, because the code answers
"did the tool work", not "is the PR good". The driver already uses 1, 2 and 3 for
gh/git/parse failures, and a caller that cannot tell "needs a human" from "gh is
broken" will page the wrong person.

No CLI here on purpose. Reading a review and printing a verdict is `driver.py`'s
job today, and running the loop is T4.5's. This module is what both will call.
"""

from __future__ import annotations

from dataclasses import dataclass

from driver_decision import Decision, Outcome
from driver_pr import PullRequest

# Every outcome exits 0. "Did the tool work" is a different question from "is the
# PR good", and 1/2/3 already mean gh/git/parse failures elsewhere in the driver.
EXIT_CODES: dict[Outcome, int] = {
    Outcome.SUCCESS: 0,
    Outcome.CONTINUE: 0,
    Outcome.HUMAN: 0,
    Outcome.REPEATED: 0,
}

READY = "READY FOR MERGE"
NOT_FINISHED = "NOT FINISHED - ANOTHER ATTEMPT IS DUE"
SAME_ISSUES = "STOPPED: SAME BLOCKING ISSUES - HUMAN REVIEW NEEDED"


@dataclass(frozen=True)
class Status:
    """A final status, split so the headline stays greppable on its own."""

    outcome: Outcome
    headline: str
    detail: str
    address: str | None = None

    @property
    def is_ready(self) -> bool:
        """True only for a success, so callers cannot print the line otherwise."""
        return self.outcome is Outcome.SUCCESS

    @property
    def needs_human(self) -> bool:
        """True when the loop stopped for a reason a person has to pick up.

        `CONTINUE` is excluded: another attempt is due, nobody has been asked for
        anything yet.
        """
        return self.outcome in (Outcome.HUMAN, Outcome.REPEATED)

    @property
    def is_final(self) -> bool:
        return self.outcome is not Outcome.CONTINUE

    def render(self) -> str:
        """Headline, address, then the decision's own reason."""
        lines = [self.headline]
        if self.address:
            lines.append(self.address)
        lines.append(self.detail)
        return "\n".join(lines)


def _address(pr: PullRequest | None) -> str | None:
    """The URL when there is one worth printing, else `#29`, else nothing.

    `PullRequest.url` is not always a URL. `driver.resolve` fills it from gh, but
    `driver._pull_request` sets a placeholder when `--pr` skips the lookup, and a
    status that reads `(given on the command line)` where the address belongs is
    worse than one that reads `#27`. The address is there to be opened, so only a
    URL is printed as one.
    """
    if pr is None:
        return None
    if pr.url.startswith(("https://", "http://")):
        return pr.url
    return f"#{pr.number}"


def _headline(decision: Decision) -> str:
    """The greppable first line, chosen by outcome and never by score.

    The score only decorates the success line. A 4/5 success must not read as
    5/5, and no other outcome may print anything skimmable as ready.
    """
    if decision.outcome is Outcome.SUCCESS:
        if decision.score is None:
            return READY
        return f"{READY} ({decision.score}/5)"
    if decision.outcome is Outcome.HUMAN:
        return f"STOPPED AFTER {decision.iteration} ITERATIONS - HUMAN REVIEW NEEDED"
    if decision.outcome is Outcome.REPEATED:
        return SAME_ISSUES
    return NOT_FINISHED


def report(decision: Decision, *, pr: PullRequest | None = None) -> Status:
    """Build the status for a decision.

    Takes only the decision, because the decision already carries the score and
    the reason. Nothing here can disagree with the rule that stopped the loop.
    """
    return Status(
        outcome=decision.outcome,
        headline=_headline(decision),
        detail=decision.message,
        address=_address(pr),
    )
