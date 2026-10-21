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
```

### 2. TDD where there is code
Write the failing test first, then the code. Config and docs have no test; they get
evidence instead (a parsed file, a passing linter, a real run).

### 3. Close the issue with evidence
`gh issue close <n> --comment "..."` naming the files, the test count, and what was
actually run. Tick `docs/TASKS.md` in the same commit series.

### 4. One PR per slice
Do not open a PR until every issue in the slice is closed. Branch
`slice-N-<description>`, title `Slice N – <name>`.

---

## Running Pi non-interactively

The driver calls Pi like this (`scripts/driver_pi.py`):

```bash
pi --print --no-session --approve [extra args] < prompt.md
```

| Flag | Why it is there |
|---|---|
| `--print` | One-shot: process the prompt, write the final text to stdout, exit. |
| `--no-session` | Each iteration starts from the repository, not from a previous iteration's session. |
| `--approve` | Print mode cannot show the trust prompt, so an unapproved run silently skips project resources. |

**The prompt goes on stdin, never in argv.** It is model-written text containing
backticks, quotes, `$` and pipes: a shell would interpret it, and a command line
would expose it to every process on the machine. Long reviews also exceed
`ARG_MAX`.

Pi's exit status is meaningful: non-zero means the run failed, was aborted, or was
cut off at the model's output-token limit. Its stderr is the diagnosis. Every run
has a deadline that kills Pi's process group, since Pi starts `git` and possibly a
language server.

---

## Hard invariants

Things that must not be "simplified" back. Each one is here because removing it
caused a real failure.

### The reviewer reads config from the default branch, never the PR head

`.github/workflows/pr-agent.yml` does **not** set `PR_AGENT_CONFIG_BRANCH`. PR-Agent
therefore loads `.pr_agent.toml` from `master`.

A PR author controls the head branch, and PR-Agent's host-only key filtering does
not cover `extra_instructions` or `model`. Reading config from the head let a PR
rewrite the instructions and ask to be scored 5/5, which is the exact value the
loop uses to decide it is done. Do not add that variable back, and do not pass a
config path pointing at the checked-out branch.

Consequence: a change to `.pr_agent.toml` on a branch has no effect until it is
merged to `master`. Settings that a run must use *now* (token budget, reasoning,
model registration, check-run publishing) also live in the workflow `env:`, where
environment variables take precedence.
`tests/test_pr_agent_config.py` guards both halves: it asserts the branch variable
is absent and that the workflow `env:` values match the TOML.

### The push is explicit, verified, and never forced

`scripts/driver_git.py` owns every write the loop makes to git. It refuses the
default branch, refuses a branch that changed mid-run (Pi has a shell and can run
`git checkout`), pushes an explicit refspec with no force flag, and reads
`refs/remotes/origin/<branch>` back afterwards instead of trusting the exit status.

A push that reports success but did not land would leave the loop waiting for a
review of a commit that never arrived, so the verification is not optional. A
force-push is never acceptable here: it rewrites a branch a human may have based
work on, and a rejected push means the remote moved and a human needs to look.

A clean tree whose head is already on the remote raises `NothingToPush` (exit 2),
not success. Treating it as success burns an iteration re-triggering a review of
unchanged code. A failed commit or push raises (exit 1) and names the local commit
that did not reach the remote.

### The contract comment is rendered by code, not by the model

PR-Agent always reshapes model output into a GFM table
(`pr_agent/algo/utils.py::convert_to_markdown_v2`), so it cannot emit the three
`##` headings. `scripts/render_contract_comment.py` builds the comment from
PR-Agent's structured review instead. Do not try to get the headings out of
`extra_instructions`.
