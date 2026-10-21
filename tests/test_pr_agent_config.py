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


if __name__ == "__main__":
    unittest.main()
