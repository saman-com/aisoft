"""Find the pull request that belongs to the current branch.

This is the first read-only piece of the local driver. It answers one question -
"which PR should I read a review from?" - and it is careful about the difference
between *there is no PR* and *we could not ask*:

- `NoPullRequest` means the branch genuinely has none. Slice 5 acts on that by
  creating one.
- `GhFailed` means the question could not be answered: `gh` is missing, not
  authenticated, or returned something unparseable. Acting on this would mean
  creating a second PR beside an existing one, so it is a hard stop.

Creating one is the second half. It refuses when the branch already has an open
PR, because two PRs on one branch means two review runs and two loops fixing the
same code. Reusing an existing PR instead of refusing is T5.2; until then the
refusal is reported with the PR that was found, so a caller can carry on with it.

Usage:

    python3 scripts/driver_pr.py                # PR for the current branch
    python3 scripts/driver_pr.py --branch foo
    python3 scripts/driver_pr.py --create       # open one if the branch has none
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# `driver_git` owns every git call, so the commit list a PR body is built from
# comes from there rather than from a second copy of `git log` here.
from driver_git import DEFAULT_BRANCHES, GitError, commits_on_branch  # noqa: E402

PR_FIELDS = "number,url,state,headRefName"

# GitHub rejects a title past 256 characters, and truncates silently rather than
# saying so, so stay under it. A commit subject in a list is cut sooner: the body
# is read, not skimmed like a title, and 100 characters is about one line.
TITLE_LIMIT = 200
COMMIT_LIMIT = 100


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


class PullRequestExists(DriverError):
    """The branch already has an open pull request, so none was created."""

    def __init__(self, message: str, pull_request: PullRequest) -> None:
        super().__init__(message)
        self.pull_request = pull_request


def build_title(commits: list[str], branch: str) -> str:
    """A PR title from the branch's commits, never empty and never a paragraph.

    GitHub takes the first line of a commit subject as the title, so a single
    commit gives the most specific title available. Several commits give a count
    and the older subject: "Slice 5 – Pi Creates the PR" is a better title than a
    joined list of every commit, and the body then carries the detail.

    The branch name is the last resort. A branch with no commits the base does
    not have has nothing to describe, and an empty title is rejected by
    `gh pr create` rather than fixed by it.
    """
    subjects = [line.strip() for line in commits if line.strip()]
    if not subjects:
        return branch
    if len(subjects) == 1:
        return _clip(subjects[0], TITLE_LIMIT)
    return _clip(f"{subjects[0]} (+{len(subjects) - 1} more)", TITLE_LIMIT)


def _clip(text: str, limit: int) -> str:
    """Cut at a word boundary, because a title truncated mid-word reads badly.

    GitHub rejects titles past 256 characters and silently wraps long ones. Being
    under the limit ourselves means the title that was written is the title that
    shows.
    """
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " not in cut:
        return cut
    return cut[: cut.rindex(" ")].rstrip() + "..."


def build_body(
    *, branch: str, base: str, commits: list[str], body: str | None = None
) -> str:
    """The PR description: the caller's words, then what the branch contains.

    `body` is passed straight through when given, because a caller that wrote a
    description knows more than this function can derive. The generated part is
    the commit list, which is also the one part that is always true: it comes from
    git, not from a summary of it.
    """
    if body is not None and body.strip():
        return body

    lines = [
        f"Opened from `{branch}` into `{base}`.",
        "",
    ]
    subjects = [line.strip() for line in commits if line.strip()]
    if subjects:
        lines.append(f"Commits on this branch ({len(subjects)}):")
        lines.append("")
        lines.extend(f"- {_clip(subject, COMMIT_LIMIT)}" for subject in subjects)
    else:
        # Not an error, and worth saying plainly: a PR with no commits ahead of
        # the base has nothing to review, and a reader should see that at once.
        lines.append(f"No commits on `{branch}` that `{base}` does not already have.")
    return "\n".join(lines) + "\n"


def create_pull_request(
    *,
    branch: str,
    base: str = "master",
    title: str | None = None,
    body: str | None = None,
    commits: list[str] | None = None,
    existing: PullRequest | None = None,
) -> PullRequest:
    """Open a pull request for `branch`, refusing to make a second one.

    Duplicate PRs are the failure this function is most careful about, because
    they are hard to undo: the review workflow then runs twice, two loops fix the
    same branch, and the branch ends up with two histories of review comments.

    Two things prevent it, and both are cheap:

    - `existing` lets a caller that already looked pass what it found. The lookup
      has done the work; asking `gh pr create` to discover it again would race
      with the workflow that also acts on the branch.
    - When it is None, the branch is looked up once before creating. That is one
      extra `gh pr list`, and it turns "probably no PR" into "no PR as of now".

    It is still not a lock. Two drivers started in the same second can both find
    no PR and both create one. That is T5.2's problem: reuse of an existing PR is
    a different feature from creating one, and pretending one call is the other
    is how a half-built idempotency ends up creating duplicates anyway.
    """
    if branch in DEFAULT_BRANCHES:
        raise GhFailed(
            f"refusing to open a pull request from the default branch {branch!r}. "
            "A PR is a branch asking to be merged somewhere, and the base cannot "
            "be its own head."
        )

    if existing is not None:
        raise PullRequestExists(
            f"#{existing.number} is already open for this branch: {existing.url}",
            existing,
        )

    # Ask before creating, and let a broken `gh` stop us: "I could not check"
    # must not become "there was nothing there".
    try:
        found = find_pull_request(branch)
    except NoPullRequest:
        found = None
    if found is not None:
        raise PullRequestExists(
            f"#{found.number} is already open for this branch: {found.url}", found
        )

    if commits is None:
        commits = []
    resolved_title = _clip(title, TITLE_LIMIT) if title else build_title(commits, branch)
    resolved_body = build_body(branch=branch, base=base, commits=commits, body=body)

    # `--head` is explicit so the PR is opened from the branch that was asked
    # about, not from whatever is checked out in the directory gh runs in.
    result = _run(
        [
            "gh", "pr", "create",
            "--base", base,
            "--head", branch,
            "--title", resolved_title,
            "--body", resolved_body,
        ]
    )
    if result.returncode != 0:
        # `gh` prints the reason on stderr (a missing branch, no commits between
        # the branches, no permission). Passing it through unchanged is what makes
        # the failure actionable; a returned guess would hide it.
        detail = (result.stderr or result.stdout).strip()
        raise GhFailed(
            f"gh pr create failed for {branch!r} into {base!r}: "
            f"{detail or result.returncode}"
        )

    # gh prints the new PR's URL on stdout. Creating the PR and failing to read
    # it back is still a success: the PR exists, and a caller that treats it as a
    # failure would retry and try to create a second one.
    url = result.stdout.strip().splitlines()[-1].strip() if result.stdout.strip() else ""
    number = _number_from_url(url)
    if number is None:
        # Fall back to the lookup rather than inventing a number: the PR exists,
        # and asking for it by head ref is the same call that guards duplicates.
        found = find_pull_request(branch)
        return found

    return PullRequest(number=number, url=url, state="OPEN", head_ref=branch)


def _number_from_url(url: str) -> int | None:
    """The PR number at the end of a GitHub PR URL, or None if it is not one."""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def resolve(branch: str | None = None) -> PullRequest:
    """Return the PR for `branch`, or for the current branch when None."""
    return find_pull_request(branch or current_branch())


def main(argv: list[str] | None = None) -> int:
    """Find the PR for the branch, or open one with `--create`.

    Exit codes:

        0  a PR was found or created
        1  `gh` or `git` failed
        2  there is no PR for this branch, and none was asked for
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default=None, help="defaults to the current branch")
    parser.add_argument(
        "--create", action="store_true",
        help="open a PR when the branch has none",
    )
    parser.add_argument("--base", default="master", help="the branch to merge into")
    parser.add_argument("--title", default=None)
    parser.add_argument("--body", default=None)
    args = parser.parse_args(argv)

    try:
        branch = args.branch or current_branch()
        if not args.create:
            pr = find_pull_request(branch)
        else:
            pr = create_pull_request(
                branch=branch,
                base=args.base,
                title=args.title,
                body=args.body,
                commits=commits_on_branch(branch, base=args.base),
            )
    except NoPullRequest as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except PullRequestExists as error:
        # A PR that is already open is the outcome the caller wanted, so this is
        # not a failure. Say which one and print it the way a lookup would.
        print(f"already open: {error}", file=sys.stderr)
        pr = error.pull_request
    except (GhFailed, GitError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"{pr.display} {pr.state.lower()} {pr.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
