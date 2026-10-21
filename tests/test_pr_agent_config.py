"""Contract tests for .pr_agent.toml (slice 1, T1.2).

Run with: python3 -m unittest discover -s tests -v
Requires Python 3.11+ for tomllib (no third-party deps).
"""

import pathlib
import tomllib
import unittest

CONFIG_PATH = pathlib.Path(__file__).resolve().parent.parent / ".pr_agent.toml"


def load_config():
    with CONFIG_PATH.open("rb") as fh:
        return tomllib.load(fh)


class ReviewFlagsTest(unittest.TestCase):
    """The review must always carry a score, a risk level and a merge verdict."""

    def setUp(self):
        self.config = load_config()["pr_reviewer"]

    def test_score_review_is_required(self):
        self.assertIs(self.config.get("require_score_review"), True)

    def test_risk_assessment_is_required(self):
        self.assertIs(self.config.get("require_risk_assessment"), True)

    def test_merge_recommendation_is_required(self):
        self.assertIs(self.config.get("require_merge_recommendation"), True)


class PublishTargetTest(unittest.TestCase):
    """PR-Agent must not post a table comment next to our contract comment.

    ``config.publish_output`` cannot be turned off to achieve that: PR-Agent
    gates ``push_outputs`` - the file sink that hands us the structured review
    - on that same flag. Publishing to a check run instead keeps the review
    running and the structured output flowing while leaving the conversation
    to the contract comment.
    """

    def setUp(self):
        self.config = load_config()

    def test_review_is_not_published_as_a_comment(self):
        self.assertIs(self.config.get("github", {}).get("publish_as_check_run"), True)

    def test_publish_output_stays_enabled_for_the_structured_output(self):
        self.assertIsNot(
            self.config.get("config", {}).get("publish_output"),
            False,
            "disabling publish_output also disables the push_outputs file sink",
        )


class ModelTest(unittest.TestCase):
    """Every model the reviewer can reach must be an OpenRouter model.

    A stale non-OpenRouter fallback (PR-Agent ships ``gpt-5.6-terra``) would
    need an OpenAI key we do not have, so a primary-model hiccup would surface
    as an auth error instead of a review.
    """

    def setUp(self):
        self.config = load_config()["config"]

    def test_primary_model_is_openrouter(self):
        model = self.config.get("model", "")
        self.assertTrue(model.startswith("openrouter/"), model)

    def test_fallback_models_are_openrouter(self):
        fallbacks = self.config.get("fallback_models", [])
        self.assertTrue(fallbacks, "fallback_models must be set explicitly")
        for model in fallbacks:
            self.assertTrue(model.startswith("openrouter/"), model)

    def test_custom_model_max_tokens_is_set(self):
        """PR-Agent must have a token budget for our OpenRouter alias.

        This was a real CI failure, not a hypothetical: ``get_max_tokens``
        looks the model up in PR-Agent's ``MAX_TOKENS`` table and then in
        litellm, neither of which knows the alias, and raises before any model
        call is made. The error text is
        "Model openrouter/deepseek/deepseek-v4.1-flash-1017 is not defined in
        MAX_TOKENS in ./pr_agent/algo/__init__.py and no
        custom_model_max_tokens is set".
        """
        value = self.config.get("custom_model_max_tokens")
        self.assertIsInstance(value, int)
        self.assertGreater(value, 0)


class WorkflowTest(unittest.TestCase):
    """Guards wiring that only shows up at runtime.

    Read as text: the suite must run on a bare interpreter (tomllib only), and
    these are plain "is the value passed" assertions, not YAML structure.
    """

    WORKFLOW_PATH = CONFIG_PATH.parent / ".github" / "workflows" / "pr-agent.yml"

    def setUp(self):
        self.text = self.WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_openrouter_key_comes_from_the_repository_secret(self):
        self.assertIn("OPENROUTER__KEY: ${{ secrets.OPENROUTER_API_KEY }}", self.text)

    def test_github_token_is_passed(self):
        self.assertIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", self.text)

    def test_config_is_read_from_the_pr_head(self):
        """Otherwise a PR cannot change .pr_agent.toml: the run would use the
        default branch's copy and the change would only take effect once merged."""
        self.assertIn("PR_AGENT_CONFIG_BRANCH: ${{ github.event.pull_request.head.ref }}", self.text)

    def test_fork_pull_requests_are_skipped(self):
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.text)

    def test_synchronize_is_a_configured_action(self):
        """A push must re-review; the fix loop waits for exactly that.

        PR-Agent's default ``pr_actions`` is [opened, reopened,
        ready_for_review, review_requested]. A run triggered by a push logged
        "Skipping action: synchronize" and exited green having done nothing.
        """
        self.assertIn("github_action_config.pr_actions:", self.text)
        self.assertIn('"synchronize"', self.text)

    def test_structured_output_is_requested(self):
        """Without the file sink the render step has nothing to read.

        These are host-only settings, so they must be here rather than in
        .pr_agent.toml.
        """
        self.assertIn("PUSH_OUTPUTS__ENABLE: \"true\"", self.text)
        self.assertIn('PUSH_OUTPUTS__CHANNELS: \'["file"]\'', self.text)
        self.assertIn("PUSH_OUTPUTS__FILE_PATH:", self.text)


if __name__ == "__main__":
    unittest.main()
