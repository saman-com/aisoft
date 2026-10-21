# AGENT.md

Instructions for any AI coding agent working on this repository.

---

## Project Goal

Build a hybrid system where:

1. **Pi** (local coding agent) writes or improves code and can open a PR.
2. **PR-Agent** (GitHub Action + OpenRouter) automatically reviews the PR and posts a structured comment containing:
   - `## Confidence Score` (X/5)
   - `## Issues`
   - `## Agent Prompt`
3. A local driver reads that review, feeds the Agent Prompt back to Pi, and loops (max 5 times) until the score reaches 5/5 or the limit is hit.
4. A human performs the final merge.

We implement this with **tracer-bullet vertical slices**. Each slice delivers a thin end-to-end capability and is validated before the next slice begins.

---

## Key Documents (read these first)

| File | Purpose |
|------|---------|
| `TASKS.md` | Ordered checklist of all work. Source of truth for progress. |
| `CONTRACT.md` | Exact output format PR-Agent must produce and the local parser must consume. |
| `.pr_agent.toml` | Forces PR-Agent to emit the contract format. |
| `AGENT.md` | This file – rules for AI agents. |

If any of these files are missing or incomplete, restore them from the project design before continuing.

---

## How You Must Work

### 1. One issue at a time
- Find the next open GitHub issue whose title starts with `[T` (order: T0.1 → T0.2 → … → T5.3).
- Work **only** on that issue.
- Do not start later issues or later slices.

Useful command:
```bash
gh issue list --state open --limit 50 --json number,title \
  --jq '.[] | select(.title | startswith("[T")) | "\(.number) \(.title)"' | sort