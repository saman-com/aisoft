"""Find the pull request that belongs to the current branch.

This is the first read-only piece of the local driver. It answers one question -
"which PR should I read a review from?" - and it is careful about the difference
between *there is no PR* and *we could not ask*:

- `NoPullRequest` means the branch genuinely has none. Slice 5 acts on that by
  creating one.
- `GhFailed` means the question could not be answered: `gh` is missing, not
  authenticated, or returned something unparseable. Acting on this would mean
  creating a second PR beside an existing one, so it is a hard stop.

Usage:

    python3 scripts/driver_pr.py                # PR for the current branch
    python3 scripts/driver_pr.py --branch foo
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass

PR_FIELDS = "number,url,state,headRefName"


class DriverError(RuntimeError):
    """Base class for driver failures, so callers can catch one type."""


class GhFailed(DriverError):
    """`gh` could not be run, or its answer could not be understood."""


class NoPullRequest(DriverError):
    """The branch has no open pull request."""


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    state: str
    head_ref: str

    @property
    def display(self) -> str:
        return f"#{self.number}"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command, turning a missing binary into the same error as a failure.

    `subprocess.run` raises FileNotFoundError when `gh` is not installed, which
    would otherwise escape as a traceback from a script meant to explain what
    went wrong.
    """
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError as error:
        raise GhFailed(f"cannot run {args[0]}: {error}") from error


def current_branch() -> str:
    """Return the checked-out branch name."""
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if result.returncode != 0:
        raise GhFailed(f"cannot read the current branch: {result.stderr.strip()}")
    branch = result.stdout.strip()
    # `HEAD` is what git prints for a detached head, and there is no PR to find
    # for a commit that is not on a branch.
    if not branch or branch == "HEAD":
        raise GhFailed("the repository is not on a branch (detached HEAD)")
    return branch


def find_pull_request(branch: str) -> PullRequest:
    """Return the open pull request whose head is `branch`."""
    result = _run(
        ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", PR_FIELDS]
    )
    if result.returncode != 0:
        # Deliberately not NoPullRequest: "gh failed" and "no PR" lead to
        # opposite actions, and guessing the first is the second is how a
        # duplicate PR gets created.
        raise GhFailed(f"gh pr list failed: {result.stderr.strip() or result.returncode}")

    text = result.stdout.strip()
    if not text:
        raise NoPullRequest(
            f"no open pull request for branch {branch!r}. "
            "Push the branch and open one with `gh pr create`, "
            "or run this on a branch that already has a PR."
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise GhFailed(f"cannot parse the gh pr list output: {error}") from error
    if not isinstance(data, list):
        raise GhFailed(f"expected a list of pull requests, got {type(data).__name__}")
    if not data:
        raise NoPullRequest(f"no open pull request for branch {branch!r}")

    # gh lists newest first; more than one entry means the branch targets
    # several base branches. The first is the one GitHub shows.
    for item in data:
        if not isinstance(item, dict):
            continue
        state = str(item.get("state") or "").upper()
        if state and state != "OPEN":
            continue
        number = item.get("number")
        if not isinstance(number, int):
            raise GhFailed(f"pull request without a number in {item!r}")
        return PullRequest(
            number=number,
            url=str(item.get("url") or ""),
            state=state or "OPEN",
            head_ref=str(item.get("headRefName") or branch),
        )

    raise NoPullRequest(f"no open pull request for branch {branch!r}")


def resolve(branch: str | None = None) -> PullRequest:
    """Return the PR for `branch`, or for the current branch when None."""
    return find_pull_request(branch or current_branch())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default=None, help="defaults to the current branch")
    args = parser.parse_args(argv)

    try:
        pr = resolve(args.branch)
    except NoPullRequest as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except GhFailed as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"{pr.display} {pr.state.lower()} {pr.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
