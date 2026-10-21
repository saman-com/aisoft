# aisoft – Pi Agent + PR-Agent Review Loop

Pi writes code and opens a PR; PR-Agent reviews it in GitHub Actions and posts a
structured comment (score / issues / agent prompt); a local driver feeds that
prompt back to Pi until the score hits 5/5 or five iterations have run. A human
does the final merge.

Design and plan: [`docs/DESIGN.md`](docs/DESIGN.md),
[`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md),
[`docs/TASKS.md`](docs/TASKS.md). Review output format:
[`CONTRACT.md`](CONTRACT.md).

---

## Required secret: `OPENROUTER_API_KEY`

The PR-Agent workflow calls OpenRouter, so the repository needs one repository
secret. Without it the workflow fails on the models call.

Add it at:

**Settings → Secrets and variables → Actions → New repository secret**

- Name: `OPENROUTER_API_KEY` (exact spelling)
- Value: an OpenRouter API key from <https://openrouter.ai/keys>

Notes:

- The workflow passes this secret to PR-Agent as the `OPENROUTER__KEY`
  environment variable (`.github/workflows/pr-agent.yml`). The two names are
  deliberately different: GitHub holds `OPENROUTER_API_KEY`, the action receives
  `OPENROUTER__KEY`.
- `GITHUB_TOKEN` is provided by Actions automatically; do not create it.
- Secrets are not available to pull requests opened from forks, so PR-Agent
  reviews will skip those until the config is adjusted.
- Never commit the key. Keep it in GitHub Secrets and in a local `.env` that is
  git-ignored.

Fork owners testing this need to add the same secret to their own repository.

---

## Status

Slice 0 (foundation and contracts) is in progress. See `docs/TASKS.md` for the
current checklist.