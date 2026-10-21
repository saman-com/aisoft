"""Tests for invoking Pi non-interactively.

T3.1 asks for a command the driver can call. The interesting part is not the
happy path but the four ways an unattended invocation goes wrong:

- The prompt contains backticks, quotes or `$` and the shell mangles it.
- The prompt is larger than the kernel's argument limit.
- Pi hangs, so the loop waits forever.
- Pi fails but the caller treats it as a successful fix.

The prompt is therefore written to Pi's stdin, never through a shell, and every
run has a deadline that kills the whole process group - Pi starts child processes
(git, a language server), and killing only the parent would leave them running.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import driver_pi  # noqa: E402
from driver_pi import PiError, build_command, run_pi  # noqa: E402

PROMPT = "Fix scripts/driver.py: add a timeout.\n"


class BuildCommandTest(unittest.TestCase):
    def test_uses_print_and_no_session(self):
        """`--print` makes it one-shot; `--no-session` stops runs accumulating state."""
        command = build_command()
        self.assertIn("--print", command)
        self.assertIn("--no-session", command)

    def test_approves_project_resources(self):
        """Print mode cannot show the trust prompt, so the decision is made up front."""
        self.assertIn("--approve", build_command())

    def test_the_prompt_is_not_on_the_command_line(self):
        """A prompt in argv is visible to every process and limited to ARG_MAX."""
        command = build_command()
        self.assertNotIn(PROMPT, command)
        self.assertFalse(any("Fix scripts" in part for part in command))

    def test_extra_arguments_are_appended(self):
        command = build_command(extra_args=("--model", "openrouter/x"))
        self.assertEqual(command[-2:], ["--model", "openrouter/x"])


class RunPiTest(unittest.TestCase):
    def fake_process(self, returncode=0, stdout="DONE\n", stderr=""):
        process = mock.MagicMock()
        process.communicate.return_value = (stdout, stderr)
        process.returncode = returncode
        return process

    def test_sends_the_prompt_on_stdin(self):
        process = self.fake_process()
        with mock.patch.object(driver_pi.subprocess, "Popen", return_value=process) as popen:
            run_pi(PROMPT, cwd="/tmp", timeout=10)
        self.assertEqual(popen.call_args.kwargs.get("stdin"), subprocess.PIPE)
        self.assertEqual(process.communicate.call_args.args[0], PROMPT)

    def test_never_goes_through_a_shell(self):
        """A shell would mangle backticks and `$` in a prompt written by a model."""
        process = self.fake_process()
        with mock.patch.object(driver_pi.subprocess, "Popen", return_value=process) as popen:
            run_pi(PROMPT, cwd="/tmp", timeout=10)
        self.assertFalse(popen.call_args.kwargs.get("shell", False))

    def test_a_prompt_full_of_shell_metacharacters_arrives_intact(self):
        process = self.fake_process()
        nasty = "Use `rm -rf /` and $(whoami) and \"quotes\" and | pipe."
        with mock.patch.object(driver_pi.subprocess, "Popen", return_value=process) as popen:
            run_pi(nasty, cwd="/tmp", timeout=10)
        self.assertEqual(process.communicate.call_args.args[0], nasty)

    def test_reports_success_and_output(self):
        with mock.patch.object(driver_pi.subprocess, "Popen", return_value=self.fake_process()):
            run = run_pi(PROMPT, cwd="/tmp", timeout=10)
        self.assertTrue(run.ok)
        self.assertEqual(run.returncode, 0)
        self.assertIn("DONE", run.stdout)

    def test_a_failing_pi_is_not_reported_as_success(self):
        process = self.fake_process(returncode=1, stderr="Error: model not found")
        with mock.patch.object(driver_pi.subprocess, "Popen", return_value=process):
            run = run_pi(PROMPT, cwd="/tmp", timeout=10)
        self.assertFalse(run.ok)
        self.assertIn("model not found", run.stderr)

    def test_an_empty_prompt_is_refused(self):
        """Pi with no prompt exits immediately; that is a caller bug, not a fix."""
        for empty in ("", "   \n\n"):
            with self.subTest(prompt=repr(empty)):
                with self.assertRaises(PiError):
                    run_pi(empty, cwd="/tmp", timeout=10)

    def test_a_timeout_kills_the_process_group(self):
        """Pi starts children; killing only the parent leaves them holding the repo."""
        process = self.fake_process()
        process.communicate.side_effect = subprocess.TimeoutExpired(cmd="pi", timeout=10)
        with mock.patch.object(driver_pi.subprocess, "Popen", return_value=process) as popen:
            with mock.patch.object(driver_pi.os, "killpg") as killpg:
                with self.assertRaises(PiError) as caught:
                    run_pi(PROMPT, cwd="/tmp", timeout=10)
        self.assertIn("10", str(caught.exception))
        killpg.assert_called_once()
        self.assertTrue(popen.call_args.kwargs.get("start_new_session"))

    def test_a_missing_binary_is_a_clear_error(self):
        with mock.patch.object(driver_pi.subprocess, "Popen", side_effect=FileNotFoundError("no pi")):
            with self.assertRaises(PiError) as caught:
                run_pi(PROMPT, cwd="/tmp", timeout=10)
        self.assertIn("pi", str(caught.exception))

    def test_the_cwd_is_the_repository_being_fixed(self):
        process = self.fake_process()
        with mock.patch.object(driver_pi.subprocess, "Popen", return_value=process) as popen:
            run_pi(PROMPT, cwd="/tmp/some-repo", timeout=10)
        self.assertEqual(popen.call_args.kwargs.get("cwd"), "/tmp/some-repo")


if __name__ == "__main__":
    unittest.main()
