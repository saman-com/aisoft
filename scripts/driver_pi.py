"""Run Pi non-interactively with a prompt written by PR-Agent.

The documented invocation (T3.1) is:

    pi --print --no-session --approve [extra args] < prompt.md

Three choices matter for an unattended loop:

- **`--print`** makes Pi process the prompt and exit. Without it, piped stdin also
  selects print mode, but relying on that means a change in TTY detection silently
  turns the driver into an interactive session that waits forever.
- **`--no-session`** keeps each fix iteration independent. Sessions are for
  resuming human work; an iteration should start from the repository, not from
  whatever a previous iteration was thinking.
- **`--approve`** settles project trust up front. Print mode cannot show the trust
  prompt, so an unapproved run silently skips project resources instead of failing
  visibly.

The prompt is written to stdin, never passed as an argument. A prompt is model
output: it contains backticks, quotes, `$`, and pipes, all of which a shell would
interpret and a command line would expose to every process on the machine.
Prompts also exceed the kernel's argument limit once they carry a review.

Every run has a deadline, and the deadline kills the process group rather than
just the parent. Pi runs `git`, and possibly a language server; killing only the
parent leaves those children holding the working tree, and the caller's next
commit then sees a repository in flux.
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass

DEFAULT_TIMEOUT = 1800.0

# `--print` for one-shot execution, `--no-session` so iterations do not share
# state, `--approve` because print mode cannot answer the trust prompt.
BASE_ARGS = ("pi", "--print", "--no-session", "--approve")


class PiError(RuntimeError):
    """Pi could not be run, timed out, or was asked to run without a prompt."""


@dataclass(frozen=True)
class PiRun:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def build_command(extra_args: tuple[str, ...] | list[str] = ()) -> list[str]:
    """The argv for an unattended Pi run.

    The prompt is absent on purpose: it goes to stdin.
    """
    return [*BASE_ARGS, *extra_args]


def run_pi(
    prompt: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    extra_args: tuple[str, ...] | list[str] = (),
) -> PiRun:
    """Run Pi once with `prompt`, returning its output.

    Raises PiError when Pi cannot be started, gets no prompt, or exceeds
    `timeout`. A non-zero exit is returned rather than raised: the caller decides
    whether a failed iteration is fatal, and its stderr is the diagnosis.
    """
    if not prompt or not prompt.strip():
        raise PiError("refusing to run pi with an empty prompt")

    command = build_command(extra_args)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Own process group, so the timeout can kill Pi's children too.
            start_new_session=True,
        )
    except FileNotFoundError as error:
        raise PiError(
            f"cannot run pi: {error}. Install Pi or put it on PATH."
        ) from error
    except OSError as error:
        raise PiError(f"cannot run pi: {error}") from error

    try:
        stdout, stderr = process.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        try:
            process.communicate(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            pass
        raise PiError(
            f"pi did not finish within {timeout:.0f}s and was killed"
        ) from None

    return PiRun(
        returncode=process.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
    )


def _kill_group(process: subprocess.Popen) -> None:
    """Kill Pi and everything it started."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        # Already gone, or the group cannot be signalled; fall back to the parent.
        try:
            process.terminate()
        except OSError:
            pass
