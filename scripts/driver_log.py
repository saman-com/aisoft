"""Human-readable output for the driver.

The driver runs unattended for up to an hour, so its output is the only record of
what it saw. Two rules follow from that:

- Everything the loop acts on is printed: the score, how many issues, how big the
  prompt is, and the issues themselves. A run can be explained afterwards without
  fetching the comment again.
- A parse failure is printed as an ERROR line and is never shaped like a score.
  A silent failure that reads as "clean" would stop the loop for the wrong reason.

The review is not dumped in full: the comment remains the source of truth for
the prompt text, and pasting a multi-kilobyte prompt into a log buries the lines
that matter.
"""

from __future__ import annotations

from parse_contract_comment import Review

# How many issue lines to print before summarising. Enough to recognise which
# review this is, few enough that the rest of the log stays visible.
MAX_ISSUES_SHOWN = 5


def describe_review(
    review: Review, *, pr: int, comment_id: int, iteration: int
) -> str:
    """Format what the driver read, for the operator reading the log."""
    count = len(review.issues)
    noun = "issue" if count == 1 else "issues"
    lines = [
        f"review #{pr} (comment {comment_id}), iteration {iteration}",
        f"  score:  {review.score}/5",
        f"  issues: {count} {noun}",
        f"  prompt: {len(review.agent_prompt)} chars",
    ]
    if not count:
        lines.append("  no issues reported")
    else:
        shown = review.issues[:MAX_ISSUES_SHOWN]
        lines.extend(f"  - {issue}" for issue in shown)
        if count > MAX_ISSUES_SHOWN:
            lines.append(f"  ... and {count - MAX_ISSUES_SHOWN} more")
    return "\n".join(lines)


def describe_failure(message: str, *, pr: int, comment_id: int | None = None) -> str:
    """Format a parse failure so it cannot be read as a normal result."""
    where = f"PR #{pr}"
    if comment_id is not None:
        where += f" comment {comment_id}"
    return f"ERROR: cannot read the review on {where}: {message}"


def log(text: str) -> None:
    """Print a block, flushing so it appears while the run is still going."""
    print(text, flush=True)
