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

    def test_output_is_capped(self):
        """An uncapped completion is what made runs take 8-20 minutes.

        With no max_output_tokens the provider's ceiling applies (393216 for
        this model). Two runs spent 8m20s and 10m20s reasoning, finished with
        finish_reason "length" and an empty body, and fell through to the
        fallback model - so every review cost two full-length generations.
        PR-Agent raises anything below 4096 (MIN_MAX_OUTPUT_TOKENS).
        """
        value = self.config.get("max_output_tokens")
        self.assertIsInstance(value, int)
        self.assertGreaterEqual(value, 4096)

    def test_reasoning_effort_is_not_the_expensive_default(self):
        """The default "medium" is what consumed the uncapped budget."""
        self.assertIn(self.config.get("reasoning_effort"), {"none", "minimal", "low"})

    def test_reasoning_effort_is_registered_for_every_model(self):
        """reasoning_effort is ignored unless the model is registered.

        PR-Agent forwards the effort only after litellm reports the model as
        reasoning-capable, and litellm has no entry for this OpenRouter alias.
        Setting reasoning_effort without listing the model here does nothing:
        the run logs showed no "Adding OpenRouter reasoning" line and the model
        kept its default effort.
        """
        registered = self.config.get("additional_reasoning_effort_models", [])
        models = [self.config["model"], *self.config.get("fallback_models", [])]
        for model in models:
            self.assertIn(model.removeprefix("openrouter/"), registered, model)

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

    def test_operational_budget_is_set_in_the_workflow(self):
        """Env outranks repo config, so this survives a stale default branch.

        When the reviewer started reading .pr_agent.toml from master, the token
        budget added in this branch stopped applying and every run failed with
        "Model ... is not defined in MAX_TOKENS ... and no
        custom_model_max_tokens is set". Setting it here means a review can run
        even when the default branch's config is stale or broken.
        """
        self.assertIn('CONFIG__CUSTOM_MODEL_MAX_TOKENS:', self.text)
        self.assertIn('CONFIG__MAX_OUTPUT_TOKENS:', self.text)
        self.assertIn('CONFIG__REASONING_EFFORT:', self.text)
        self.assertIn('CONFIG__ADDITIONAL_REASONING_EFFORT_MODELS:', self.text)
        self.assertIn('GITHUB__PUBLISH_AS_CHECK_RUN: "true"', self.text)

    def test_operational_budget_matches_the_toml(self):
        """The two sources must not drift apart.

        Environment wins, so a mismatch would silently override the checked-in
        value and the toml tests would be asserting something unused.
        """
        config = load_config()["config"]
        for key, env_name in (
            ("max_output_tokens", "CONFIG__MAX_OUTPUT_TOKENS"),
            ("custom_model_max_tokens", "CONFIG__CUSTOM_MODEL_MAX_TOKENS"),
        ):
            self.assertIn(f'{env_name}: "{config[key]}"', self.text, key)

    def test_github_token_is_passed(self):
        self.assertIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", self.text)

    def test_config_is_not_read_from_the_pr_head(self):
        """A PR must not be able to configure the reviewer that scores it.

        Pointing PR_AGENT_CONFIG_BRANCH at the PR head made review config
        changes testable before merge, but it also let a PR author rewrite
        extra_instructions and ask for 5/5 - the one value the loop trusts.
        PR-Agent's host-only filtering does not cover extra_instructions or
        model, so the default branch is the only safe source.
        """
        self.assertNotIn("PR_AGENT_CONFIG_BRANCH:", self.text)

    def test_fork_pull_requests_are_skipped(self):
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.text)

    def test_no_bot_gate(self):
        """A bot-authored PR must still be reviewed.

        Gating on ``github.event.sender.type`` skipped the loop's own pushes.
        Switching the gate to ``pull_request.user.type`` fixed that but broke
        Slice 5: Pi opens the PR, so the author is a bot too, and the loop would
        poll forever for a review that is never dispatched. No loop is possible
        without commits, and posting the contract comment pushes nothing.
        """
        self.assertNotIn("sender.type", self.text)
        self.assertNotIn("pull_request.user.type", self.text)

    def test_synchronize_is_a_configured_action(self):
        """A push must re-review; the fix loop waits for exactly that.

        PR-Agent's default ``pr_actions`` is [opened, reopened,
        ready_for_review, review_requested]. A run triggered by a push logged
        "Skipping action: synchronize" and exited green having done nothing.
        """
        self.assertIn("github_action_config.pr_actions:", self.text)
        self.assertIn('"synchronize"', self.text)

    def test_render_falls_back_to_a_failure_comment(self):
        """A failed review must still post something the loop can read.

        Otherwise the job just goes red and the loop waits for a review that
        already failed.
        """
        self.assertIn("--failure", self.text)
        self.assertIn("if: always()", self.text)

    def test_render_failure_still_yields_a_comment_without_python(self):
        """The inlined printf is the last resort when the renderer itself dies.

        Without it an empty body reaches the post step, the loop gets no answer,
        and nothing distinguishes that from a review still in flight.
        """
        self.assertIn("## Confidence Score\\n0/5", self.text)

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
