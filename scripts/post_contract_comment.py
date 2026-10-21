"""Post the contract comment, replacing the previous one.

`gh pr comment` appends, so a PR pushed to five times would end up with five
review comments and a parser reading the newest could still pick up a stale
one. This keeps exactly one: it looks for a contract comment by the workflow
account and edits it in place, and only creates one when none exists.

Finding the comment by content rather than by a hidden marker keeps the body
identical to CONTRACT.md and lets the poster and the Slice 2 parser agree on
what a contract comment is.

Usage inside the workflow:

    python3 scripts/post_contract_comment.py --pr 27 --body-file contract-comment.md
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

# The first line every contract comment starts with, per CONTRACT.md.
CONTRACT_HEADER = "## Confidence Score"


def _login(comment: dict) -> str:
    user = comment.get("user") or {}
    return str(user.get("login") or "")


def _is_same_account(login: str, author: str) -> bool:
    """Whether `login` is the account this workflow posts as.

    GitHub reports the Actions token as `github-actions` in some responses and
    `github-actions[bot]` in others, so the suffix is normalised. Nothing wider
    is accepted: matching any bot account would let this script PATCH another
    tool's comment, which is destructive and hard to notice. Callers must pass
    the login the token actually posts under - see `resolve_author`.
    """
    return bool(login) and login.removesuffix("[bot]") == author.removesuffix("[bot]")


def pick_contract_comment(comments: list[dict], author: str) -> int | None:
    """Return the id of the contract comment by `author`, if any.

    "Newest" is by numeric id. GitHub ids are snowflakes, so they sort by
    creation time, and the API returns comments oldest first - the last match
    wins. If a PR somehow holds more than one, every one but the highest id is
    stale output from an earlier version of this workflow, and updating the
    highest keeps the parser reading the fresh one.
    """
    newest: int | None = None
    for item in comments:
        if not isinstance(item, dict):
            continue
        body = item.get("body")
        comment_id = item.get("id")
        if not isinstance(body, str) or not isinstance(comment_id, int):
            continue
        if not body.lstrip().startswith(CONTRACT_HEADER):
            continue
        if not _is_same_account(_login(item), author):
            continue
        if newest is None or comment_id > newest:
            newest = comment_id
    return newest


def _gh(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["gh", *args], input=stdin, capture_output=True, text=True, check=True
    )
    return result.stdout


def _write_body(repo: str, pr: int | None, body: str, comment_id: int | None) -> None:
    """Write `body` through the API's JSON input.

    The body is model-derived text of unbounded length. Sending it as a command
    line argument risks hitting ARG_MAX, and `gh api -f body=...` turns a value
    starting with `@` into a file reference. Passing the JSON payload on stdin
    avoids both: nothing is parsed out of the body and no length limit applies.
    """
    payload = json.dumps({"body": body})
    if comment_id is None:
        _gh("api", "--method", "POST", f"repos/{repo}/issues/{pr}/comments",
            "-H", "Content-Type: application/json", "--input", "-", stdin=payload)
    else:
        _gh("api", "--method", "PATCH", f"repos/{repo}/issues/comments/{comment_id}",
            "-H", "Content-Type: application/json", "--input", "-", stdin=payload)


def list_comments(repo: str, pr: int) -> list[dict]:
    out = _gh(
        "api",
        "--paginate",
        f"repos/{repo}/issues/{pr}/comments",
        "--jq",
        "[.[] | {id: .id, body: .body, user: {login: .user.login, type: .user.type}}]",
    )
    # --paginate concatenates one JSON array per page.
    comments: list[dict] = []
    for chunk in _split_json_arrays(out):
        comments.extend(chunk)
    return comments


def _split_json_arrays(out: str) -> list[list[dict]]:
    decoder = json.JSONDecoder()
    chunks: list[list[dict]] = []
    index = 0
    while index < len(out):
        while index < len(out) and out[index] in " \t\r\n":
            index += 1
        if index >= len(out):
            break
        value, index = decoder.raw_decode(out, index)
        if isinstance(value, list):
            chunks.append(value)
    return chunks


def resolve_author(fallback: str = "github-actions[bot]") -> str:
    """Return the login this token posts comments as.

    Hardcoding `github-actions[bot]` is wrong for a workflow that authenticates
    with a GitHub App or a PAT: every run would fail to recognise its own
    comment and add another one. The API answer is authoritative, and the
    fallback covers a token that cannot read its own user (some fine-grained
    tokens).
    """
    try:
        login = _gh("api", "user", "--jq", ".login").strip()
    except (subprocess.CalledProcessError, OSError):
        # CalledProcessError: the token cannot read its own user. OSError:
        # `gh` is missing or not executable, which would otherwise escape as a
        # traceback and skip the fallback this function promises.
        return fallback
    return login or fallback


def post(repo: str, pr: int, body: str, author: str) -> str:
    """Create or update the contract comment; return a line describing what happened."""
    existing = pick_contract_comment(list_comments(repo, pr), author)
    _write_body(repo, pr, body, existing)
    if existing is None:
        return f"created a new contract comment on #{pr}"
    return f"updated contract comment {existing} on #{pr}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--body-file", required=True, type=pathlib.Path)
    parser.add_argument("--repo", default=None, help="owner/name; defaults to the current repo")
    parser.add_argument("--author", default=None, help="login the token posts as; detected if omitted")
    args = parser.parse_args(argv)

    # Reading the body first: a missing or unreadable file must be a clean
    # error, not a traceback, and posting nothing is better than guessing at a
    # body or creating an empty comment.
    try:
        body = args.body_file.read_text(encoding="utf-8")
    except OSError as error:
        print(f"error: cannot read {args.body_file}: {error}", file=sys.stderr)
        return 1

    author = args.author or resolve_author()
    repo = args.repo or _gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()
    if not body.lstrip().startswith(CONTRACT_HEADER):
        print(f"error: {args.body_file} is not a contract comment", file=sys.stderr)
        return 1
    try:
        print(post(repo, args.pr, body, author))
    except subprocess.CalledProcessError as error:
        print(f"error: gh {' '.join(str(a) for a in error.cmd[1:])} failed: {error.stderr}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
