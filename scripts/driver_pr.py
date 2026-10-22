"""Find the pull request that belongs to the current branch.

This is the first read-only piece of the local driver. It answers one question -
"which PR should I read a review from?" - and it is careful about the difference
between *there is no PR* and *we could not ask*:

- `NoPullRequest` means the branch genuinely has none. Slice 5 acts on that by
  creating one.
- `GhFailed` means the question could not be answered: `gh` is missing, not
  authenticated, or returned something unparseable. Acting on this would mean
  creating a second PR beside an existing one, so it is a hard stop.

Getting a PR, when the caller does not care whether it had to be opened, is
`ensure_pull_request`: it returns the branch's open PR and creates one only when
there is none. Two open PRs on one branch means two review runs and two loops
fixing the same code, so nothing here will make a second one while the first is
open. `create_pull_request` keeps the stricter meaning and raises
`PullRequestExists` instead, for a caller that wants to know its PR was new.

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


class CreateFailed(GhFailed):
    """`gh pr create` itself failed, as opposed to a lookup that went wrong.

    A subclass so a caller can act on the difference without matching on the
    message: a failed create can mean another run opened the PR first, while a
    failed lookup after a create means the PR exists and its number is unknown.
    Callers that only care that `gh` failed still catch `GhFailed`.
    """


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


def list_pull_requests(branch: str, *, state: str = "open") -> list[PullRequest]:
    """Every pull request whose head is `branch`, newest first.

    `state` is passed to gh: `open` for the common question, `all` when a caller
    needs to know whether the branch was already merged. An empty list is a
    normal answer. A gh failure is `GhFailed`, never an empty list: "I could not
    check" and "there is nothing there" lead to opposite actions, and reading the
    first as the second is how a duplicate PR gets created.
    """
    result = _run(
        ["gh", "pr", "list", "--head", branch, "--state", state, "--json", PR_FIELDS]
    )
    if result.returncode != 0:
        raise GhFailed(f"gh pr list failed: {result.stderr.strip() or result.returncode}")

    text = result.stdout.strip()
    if not text:
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise GhFailed(f"cannot parse the gh pr list output: {error}") from error
    if not isinstance(data, list):
        raise GhFailed(f"expected a list of pull requests, got {type(data).__name__}")

    pull_requests = []
    for item in data:
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        if not isinstance(number, int):
            raise GhFailed(f"pull request without a number in {item!r}")
        pull_requests.append(
            PullRequest(
                number=number,
                url=str(item.get("url") or ""),
                state=str(item.get("state") or "").upper() or "OPEN",
                head_ref=str(item.get("headRefName") or branch),
            )
        )
    return pull_requests


def find_pull_request(branch: str) -> PullRequest:
    """Return the open pull request whose head is `branch`."""
    open_now = [pr for pr in list_pull_requests(branch) if pr.state == "OPEN"]
    if not open_now:
        raise NoPullRequest(
            f"no open pull request for branch {branch!r}. "
            "Push the branch and open one with `gh pr create`, "
            "or run this on a branch that already has a PR."
        )
    # gh lists newest first; more than one entry means the branch targets
    # several base branches. The first is the one GitHub shows.
    return open_now[0]


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
    # The ellipsis has to fit inside the limit, so it is budgeted for before the
    # cut. Without that, a single long word would come back at exactly `limit`
    # characters with nothing to say it had been shortened.
    cut = text[: limit - len("...")]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip() + "..."


def build_body(
    *, branch: str, base: str, commits: list[str] | None, body: str | None = None
) -> str:
    """The PR description: the caller's words, then what the branch contains.

    `body` is passed straight through when given, because a caller that wrote a
    description knows more than this function can derive. The generated part is
    the commit list, which is also the one part that is always true: it comes from
    git, not from a summary of it.

    `commits` of None and `commits` of [] are different answers and are described
    differently. [] means the list was read and the base has everything the branch
    has. None means nobody read git at all, so claiming "no commits" would state
    something the caller never checked - a caller that omits the argument gets a
    body that says the list was not read.
    """
    if body is not None and body.strip():
        return body

    lines = [
        f"Opened from `{branch}` into `{base}`.",
        "",
    ]
    if commits is None:
        # Not read, so not described. `gh pr create` is happier with a short body
        # than with one that contradicts the diff a reviewer is looking at.
        lines.append(f"The commits on `{branch}` were not listed for this body.")
        return "\n".join(lines) + "\n"

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
    """Open a pull request for `branch`, refusing to make a second open one.

    Duplicate open PRs are the failure this function is most careful about,
    because they are hard to undo: the review workflow then runs twice, two loops
    fix the same branch, and the branch ends up with two histories of review
    comments.

    An open PR on the branch is an error here rather than a return value, and
    that is deliberate: this function's name says *create*, so returning an
    existing PR would let a caller believe a PR it did not open was opened. A
    caller that wants "a PR, new or existing" calls `ensure_pull_request`, which
    is the same lookup with the opposite answer.

    - `existing` lets a caller that already looked pass what it found, so the
      lookup is not paid for twice. It short-circuits before any `gh` call.
    - When it is None the branch is looked up once before creating. One extra
      `gh pr list` turns "probably no PR" into "no PR as of now".

    A branch whose only PRs are closed or merged does get a new one: closing a PR
    is not a veto on the branch, and a merged PR with new commits on top is the
    normal second round of work. `gh` refuses that itself when the branch has
    nothing new, and its message is passed through.

    It is still not a lock. Two drivers started in the same second can both find
    no PR and both create one. `gh` rejects a second PR for the same head and
    base, but that is not a guarantee for every base, so the create is followed by
    the same duplicate check `ensure_pull_request` makes: a second open PR on the
    branch is reported rather than returned as if it were the only one.
    """
    if branch in DEFAULT_BRANCHES:
        raise GhFailed(
            f"refusing to open a pull request from the default branch {branch!r}. "
            "A PR is a branch asking to be merged somewhere, and the base cannot "
            "be its own head."
        )

    if existing is not None and existing.state == "OPEN":
        raise PullRequestExists(
            f"#{existing.number} is already open for this branch: {existing.url}",
            existing,
        )

    # Ask before creating, and let a broken `gh` stop us: "I could not check"
    # must not become "there was nothing there". `NoPullRequest` is the only
    # error that means "go ahead", and it is caught here rather than around the
    # whole function so a `GhFailed` keeps travelling.
    if existing is None:
        try:
            found = find_pull_request(branch)
        except NoPullRequest:
            found = None
        if found is not None:
            raise PullRequestExists(
                f"#{found.number} is already open for this branch: {found.url}", found
            )

    created = _open(branch=branch, base=base, title=title, body=body, commits=commits)
    _refuse_a_second_open(created)
    return created


def ensure_pull_request(
    *,
    branch: str,
    base: str = "master",
    title: str | None = None,
    body: str | None = None,
    commits: list[str] | None = None,
) -> tuple[PullRequest, bool]:
    """Return the branch's open PR, opening one only when there is none.

    This is the call a loop should make: it answers "there is a PR for this
    branch" and says nothing about who opened it. The boolean says whether this
    call created it, which is what a log line or a "first review coming up"
    message needs; nothing else should depend on it, or the caller starts caring
    about a difference that does not matter.

    The existing PR is returned untouched, including its title and body. Rewriting
    them here would be a second author editing a description someone may have
    written by hand, and the review is already reading the code rather than the
    title. `gh pr create` is reached only when the lookup found no open PR.

    One `gh pr list --state all` answers both questions at once: whether an open
    PR exists, and whether the branch has only merged ones. Asking twice before
    creating would leave a moment between the two answers in which another run
    could create the PR.

    Creating is not atomic, so a create is followed by one more lookup: two runs
    that both saw no open PR both reach `gh pr create`, and the second one makes a
    duplicate. That cannot be prevented here, but it can be reported instead of
    handed back as "your PR", which is what would let a loop fix one of two PRs
    while the review comments went to the other. The same goes for a create that
    *failed* while another run's PR appeared in between: that is a duplicate too,
    and gh's own message alone would read as "there is no PR here".
    """
    if branch in DEFAULT_BRANCHES:
        raise GhFailed(
            f"refusing to open a pull request from the default branch {branch!r}. "
            "A PR is a branch asking to be merged somewhere, and the base cannot "
            "be its own head."
        )

    # `--state all`, so a merged PR does not hide behind a missing open one.
    found = list_pull_requests(branch, state="all")
    open_now = [pr for pr in found if pr.state == "OPEN"]
    if open_now:
        return open_now[0], False

    # Any PRs that were found are closed or merged, and neither is a veto: a
    # branch whose last PR merged and then grew new commits is the normal second
    # round. gh refuses the create itself when nothing is new, and its message is
    # passed through, which is more accurate than a rule here guessing.
    try:
        created = _open(branch=branch, base=base, title=title, body=body, commits=commits)
    except CreateFailed as error:
        # A failed create is not the same answer as "there is no PR". Another run
        # can have opened one between the lookup above and this create - gh then
        # refuses the second one - and gh's message on its own reads as "nothing to
        # work on" when the branch does have a PR, just not this run's.
        try:
            duplicates = [pr for pr in list_pull_requests(branch) if pr.state == "OPEN"]
        except GhFailed:
            # The create error is the one that explains what happened; a failed
            # lookup here would replace it with a less useful message.
            duplicates = []
        if not duplicates:
            raise
        numbers = ", ".join(f"#{pr.number}" for pr in duplicates)
        raise GhFailed(
            f"gh pr create failed for {branch!r}, but {numbers} is open for that "
            "branch: another run created the pull request between this call's "
            f"lookup and its create. Run again to reuse it, or close it first. "
            f"gh said: {error}"
        ) from error

    _refuse_a_second_open(created)
    return created, True


def _refuse_a_second_open(created: PullRequest) -> None:
    """Check, after a create, that `created` is the branch's only open PR.

    The lookup a create is preceded by and the create itself are two separate
    moments, so another run can have opened the same PR in between. Two open PRs
    split the review, so say so rather than picking one and acting on it.

    This lives here, called from both entry points, because "refuse to make a
    second open one" is a promise of `create_pull_request` as much as of
    `ensure_pull_request`; leaving the check to the ensure path only would let a
    direct caller return a PR while a duplicate sits beside it.
    """
    open_now = [
        pr for pr in list_pull_requests(created.head_ref) if pr.state == "OPEN"
    ]
    if len(open_now) > 1:
        numbers = ", ".join(f"#{pr.number}" for pr in open_now)
        raise GhFailed(
            f"two open pull requests now point at {created.head_ref!r} ({numbers}): "
            "another run created one between this call's lookup and its create. "
            "Close all but one before running again, or the review and the loop "
            "will not agree about which PR they are working on."
        )
    # One open PR is this call's only when it is the one gh reported creating. A
    # different number means the branch's open PR belongs to another run, and
    # returning `created` would hand back a PR this loop would then push fixes for
    # while the review read the other one.
    if open_now and open_now[0].number != created.number:
        raise GhFailed(
            f"this call created #{created.number} for {created.head_ref!r}, but the "
            f"branch's open pull request is #{open_now[0].number}. Another run "
            "opened one at the same time, so there is no single PR this run can say "
            "it created. Check the branch on GitHub before running again."
        )


def _open(
    *,
    branch: str,
    base: str,
    title: str | None,
    body: str | None,
    commits: list[str] | None,
) -> PullRequest:
    """Run `gh pr create`. The one place a PR is opened.

    Both `create_pull_request` and `ensure_pull_request` end up here, so the
    title and body are built in exactly one way and the "look first" rule cannot
    be true for one caller and forgotten by the other.
    """
    # `commits` stays None when the caller did not read git: `build_title` and
    # `build_body` each describe that case, rather than being handed a `[]` that
    # claims the branch has nothing ahead of its base.
    resolved_title = (
        _clip(title, TITLE_LIMIT)
        if title
        else build_title(commits or [], branch)
    )
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
        raise CreateFailed(
            f"gh pr create failed for {branch!r} into {base!r}: "
            f"{detail or result.returncode}"
        )

    # gh prints the new PR's URL on stdout. The PR exists at this point, but the
    # number is only trustworthy when gh printed it: a lookup answers with the
    # branch's open PRs, not with this call's, so a single open PR may just as
    # well be one another run opened a moment earlier. Returning that PR would
    # hand the caller something it did not create, and every later push would
    # follow the guess while the review read the other PR. An unknown number is
    # therefore reported, not filled in from the branch. The URL is read from
    # whichever line is one: gh prints it first, but may print warnings after it.
    url, number = "", None
    for line in result.stdout.splitlines():
        number = _number_from_url(line.strip())
        if number is not None:
            url = line.strip()
            break
    if number is None:
        output = result.stdout.strip()
        raise GhFailed(
            f"gh created a pull request for {branch!r} but printed no readable PR "
            f"number (output: {output or '(empty)'!r}), so this run cannot tell which "
            "pull request it opened. Check the branch on GitHub, then run again to "
            "reuse it."
        )

    return PullRequest(number=number, url=url, state="OPEN", head_ref=branch)


def _number_from_url(url: str) -> int | None:
    """The PR number at the end of a GitHub PR URL, or None if it is not one."""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def resolve(branch: str | None = None) -> PullRequest:
    """Return the PR for `branch`, or for the current branch when None."""
    return find_pull_request(branch or current_branch())


def main(argv: list[str] | None = None) -> int:
    """Find the PR for the branch, or ensure one with `--create`.

    `--create` is "ensure", not "create unconditionally": an open PR is returned
    rather than duplicated, and the output says which happened. That is the flag a
    script wants, because it does not have to branch on whether the PR was already
    there.

    Exit codes:

        0  a PR was found, or created because there was none
        1  `gh` or `git` failed
        2  there is no PR for this branch, and none was asked for
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default=None, help="defaults to the current branch")
    parser.add_argument(
        "--create", action="store_true",
        help="ensure a PR exists: reuse the open one, create only if there is none",
    )
    parser.add_argument("--base", default="master", help="the branch to merge into")
    parser.add_argument("--title", default=None)
    parser.add_argument("--body", default=None)
    args = parser.parse_args(argv)

    created = False
    try:
        branch = args.branch or current_branch()
        if not args.create:
            pr = find_pull_request(branch)
        else:
            pr, created = ensure_pull_request(
                branch=branch,
                base=args.base,
                title=args.title,
                body=args.body,
                commits=commits_on_branch(branch, base=args.base),
            )
    except NoPullRequest as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (GhFailed, GitError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    verb = "created" if created else "reused"
    print(f"{pr.display} {pr.state.lower()} {pr.url}")
    if args.create:
        # One line saying which path was taken, because "did this just open a PR?"
        # is the question a caller reads the output to answer.
        print(f"{verb} the pull request for {branch}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
