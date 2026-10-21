"""Stage, commit and push Pi's changes.

The changes being committed were written by an agent, so this refuses rather than
guesses at every point where a wrong answer would be silently plausible:

- **Never the default branch.** The loop fixes a feature branch. Committing to
  `master` would bypass the review the loop exists to satisfy.
- **Never a branch Pi switched to.** Pi has a shell and can run `git checkout`;
  the caller passes the branch it started on and the push is refused if it moved.
- **Never an empty push.** If Pi changed nothing, the caller needs that as its own
  outcome (`NothingToPush`) instead of pushing a no-op that re-triggers the
  review, returns the same score and burns an iteration.
- **Never a force-push.** The refspec is explicit (`HEAD:refs/heads/<branch>`) and
  carries no force flag. Rewriting a branch a human may have based work on is
  never what a fix iteration needs.
- **Never trust the push exit status alone.** After pushing, the remote-tracking
  ref is read back and compared with the local commit. A push that reported
  success but did not land would otherwise leave the loop waiting for a review of
  a commit that never arrived.

Every git call is a subprocess with `shell=False`. Commit messages are built here
rather than passed in, so the iteration number is always in the history.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

REMOTE = "origin"

DEFAULT_BRANCHES = ("master", "main")


class GitError(RuntimeError):
    """A git command failed, or the repository is not in a state to proceed."""


class NothingToPush(GitError):
    """There is nothing to commit and nothing unpushed."""


@dataclass(frozen=True)
class PushResult:
    branch: str
    commit: str
    made_commit: bool
    remote: str = REMOTE


def _git(args: list[str], *, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command, turning a missing binary into a GitError."""
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False, cwd=cwd
        )
    except OSError as error:
        raise GitError(f"cannot run git: {error}") from error


def _require(args: list[str], *, what: str, cwd: str | None = None) -> str:
    completed = _git(args, cwd=cwd)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise GitError(f"{what} failed: {detail or completed.returncode}")
    return completed.stdout.strip()


def build_message(iteration: int, score: int | None = None, issues: int | None = None) -> str:
    """The commit message for one fix iteration.

    The iteration number is in the subject so history shows how many rounds the
    loop needed. The score and issue count go in the body: useful when reading the
    log later, noise in a subject line that gets truncated.
    """
    subject = f"fix: address PR-Agent feedback (iteration {iteration})"
    details = []
    if score is not None:
        details.append(f"Score was {score}/5.")
    if issues is not None:
        noun = "issue" if issues == 1 else "issues"
        details.append(f"{issues} {noun} reported.")
    details.append("Changes written by Pi from the Agent Prompt.")
    return subject + "\n\n" + " ".join(details) + "\n"


def current_branch(cwd: str | None = None) -> str:
    branch = _require(
        ["rev-parse", "--abbrev-ref", "HEAD"], what="reading the current branch", cwd=cwd
    )
    if not branch or branch == "HEAD":
        raise GitError("the repository is not on a branch (detached HEAD)")
    return branch


def commit_and_push(
    *,
    iteration: int,
    score: int | None = None,
    issues: int | None = None,
    expected_branch: str | None = None,
    cwd: str | None = None,
) -> PushResult:
    """Commit any changes and push the branch. Raises on anything unexpected."""
    branch = current_branch(cwd=cwd)

    if branch in DEFAULT_BRANCHES:
        raise GitError(
            f"refusing to push to the default branch {branch!r}: "
            "the loop fixes a feature branch, and changing master is a human's decision"
        )
    if expected_branch is not None and branch != expected_branch:
        raise GitError(
            f"the branch changed from {expected_branch!r} to {branch!r} during the run. "
            f"Switch back to {expected_branch!r} before committing; "
            "pushing here would put the fix on the wrong branch."
        )

    status = _require(["status", "--porcelain"], what="reading the working tree", cwd=cwd)
    made_commit = False
    if status:
        _require(["add", "-A"], what="staging the changes", cwd=cwd)
        completed = _git(["commit", "-m", build_message(iteration, score, issues)], cwd=cwd)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            hint = ""
            if "identity" in detail.lower():
                hint = (
                    "\nSet it with: git config user.name <name> && git config user.email <email>"
                )
            raise GitError(f"git commit failed: {detail}{hint}")
        made_commit = True

    head = _require(["rev-parse", "HEAD"], what="reading the new commit", cwd=cwd)

    # A clean tree whose head is already on the remote means Pi changed nothing.
    # Say so instead of pushing a no-op: the caller must not wait for a review of
    # code that did not change.
    if not made_commit and _remote_matches(branch, head, cwd=cwd):
        raise NothingToPush(
            f"no changes to push: the working tree is clean and HEAD matches "
            f"{REMOTE}/{branch} as of the last fetch, so Pi did not change anything"
        )

    # Explicit refspec, no force flag: this can only fast-forward the branch.
    pushed = _git(["push", REMOTE, f"HEAD:refs/heads/{branch}"], cwd=cwd)
    if pushed.returncode != 0:
        detail = (pushed.stderr or pushed.stdout).strip()
        # Say where the commit ended up: a local commit that never reached the
        # remote is recoverable, but only if the operator knows it exists.
        note = (
            f" The commit {head[:8]} exists locally but was not pushed."
            if made_commit
            else ""
        )
        raise GitError(f"pushing {branch} to {REMOTE} failed: {detail}{note}")

    # Read the ref back rather than trusting the exit status: a push that did not
    # land would leave the loop waiting for a review that never comes.
    landed = _require(
        ["rev-parse", f"refs/remotes/{REMOTE}/{branch}"],
        what="confirming the push",
        cwd=cwd,
    )
    if landed != head:
        raise GitError(
            f"push did not land: refs/remotes/{REMOTE}/{branch} is {landed[:8]}, "
            f"but HEAD is {head[:8]}"
        )

    return PushResult(branch=branch, commit=head, made_commit=made_commit)


def _remote_matches(branch: str, head: str, *, cwd: str | None = None) -> bool:
    """Whether the local remote-tracking ref already points at `head`.

    Deliberately no fetch: this only decides whether there is anything local left
    to send. A stale tracking ref makes the answer pessimistic (we push again,
    harmlessly), never optimistic in a way that loses a commit.
    """
    completed = _git(["rev-parse", "--verify", f"refs/remotes/{REMOTE}/{branch}"], cwd=cwd)
    return completed.returncode == 0 and completed.stdout.strip() == head


def main(argv: list[str] | None = None) -> int:
    """Commit and push by hand, for the manual runs T3.4 describes.

    Exit codes: 0 pushed, 1 failed, 2 nothing to push.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Stage, commit and push Pi's changes.")
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--score", type=int, default=None)
    parser.add_argument("--issues", type=int, default=None)
    parser.add_argument("--branch", default=None, help="refuse if the branch changed")
    parser.add_argument("--cwd", default=None, help="repository to work in")
    args = parser.parse_args(argv)

    try:
        pushed = commit_and_push(
            iteration=args.iteration,
            score=args.score,
            issues=args.issues,
            expected_branch=args.branch,
            cwd=args.cwd,
        )
    except NothingToPush as error:
        print(f"nothing to push: {error}", file=sys.stderr)
        return 2
    except GitError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    action = "committed and pushed" if pushed.made_commit else "pushed"
    print(f"{action} {pushed.branch} ({pushed.commit[:8]}) to {pushed.remote}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
