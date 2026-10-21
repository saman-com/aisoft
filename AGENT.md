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

### Waiting for a re-review is bounded, and its numbers are fixed here

After a push the loop waits for the *new* review, not just any review, via
`scripts/driver_comments.py::wait_for_contract_comment`. Two numbers are settled
here so Slice 4 does not invent its own:

| Setting | Value | Why |
|---|---|---|
| `DEFAULT_TIMEOUT` | **600 s (10 minutes)** | A workflow run measured 50 s to ~2 min; 10 min leaves room for queuing without hiding a broken pipeline behind a long wait. |
| `DEFAULT_INTERVAL` | **15 s** | A run takes far longer than 15 s, so faster polling only spends API calls; slower makes every iteration feel stalled. |

Both live in `scripts/driver_comments.py` and are asserted by
`tests/test_driver_comments.py::DocumentedNumbersTest`. Change them there, and
update this table in the same commit. A timeout raises `ReviewTimeout` (exit 2),
which is *not* the same as a broken review (exit 3): it means "the review has not
arrived yet", so the message names the PR and says to check the Actions tab.

**Freshness is decided on content, not on the comment id.** The poster keeps one
contract comment per PR and edits it in place, so the id never changes after the
first iteration. Pass the `Comment` you read (`baseline=`): the wait returns when
the id grows beyond it or its body (or `updated_at`) differs. An id-only baseline
(`newer_than=`) cannot detect an in-place rewrite and will time out; that limitation
is documented by its own test. The old comment is never accepted as the new one.

### The iteration limit is five, and it is checked before the work

`scripts/driver_iteration.py` owns the counter. `MAX_ITERATIONS = 5` is defined
once, imported by the cycle and the CLI, and printed as `iteration N/5` so the
log says how many attempts are left.

`check_iteration` runs on the way *into* an iteration, before the PR is read,
before Pi runs, before a commit exists and before anything is pushed. A limit
checked afterwards would push a sixth fix and then report that it should not have
run. A message cannot undo a push. Attempt `0` is refused too, because a counter
that starts at zero quietly buys a sixth attempt.

The guard answers "is another attempt allowed". T4.2 answers a different
question: "would another attempt help" (score 5, or 4+ with no P0/P1). Keeping
them separate means changing the stopping rule cannot widen the hard cap.

Exceeding the limit raises `IterationLimit` (a `DriverError`, exit 1): repeating
the run unchanged will not help, and the message says a human is needed.

### The loop stops at 5/5, or at 4/5 with nothing serious left

`scripts/driver_decision.py::decide` owns the stopping rule and returns one of
four outcomes. The cycle reads it and does nothing else about scoring:

| Outcome | Condition | The cycle |
|---|---|---|
| `SUCCESS` | score 5, **or** score >= 4 with no blocking issue | stop, print ready for merge |
| `REPEATED` | same blocking findings as the last attempt, and attempts made > 0 | stop, print human needed (a no-op fix) |
| `CONTINUE` | not finished, and attempts made < 5 | run Pi, commit, push, wait |
| `HUMAN` | not finished, and attempts made == 5 | stop, print human needed |

Success is checked before `REPEATED`, so a clean review always ends the loop as
a success. The limit is checked after, so `REPEATED` can stop before the limit is
reached, which is the point of it.

The 4/5 half is not a relaxation of the bar, it is the case that happens. The
reviewer plateaus at 4/5 while listing findings it rates as minor (PR #27); a
loop that only accepts 5/5 can never finish on those PRs, spends all five
attempts and reports failure on a branch that is fine.

**Blocking is decided by the `[P0]`/`[P1]`/`[P2]` tag** on each Issues bullet,
read by `parse_contract_comment.py::is_blocking` next to the parser. `[P0]` and
`[P1]` block; `[P2]` does not. A bullet with no tag, or with a tag the contract
does not define, blocks: the reviewer failing to state a severity is not evidence
that the finding is safe, and an unknown tag is more likely a format change than
a downgrade. An empty Issues section is no blocking issues, which is why the
plateau case exits cleanly.

**`iteration` counts attempts already made, not the one about to run.** The cycle
asks about `N - 1` on the way in, so attempt 5 still runs, and about `N` on the
way out, when attempt 5's review is what the limit applies to. Reading it the
other way makes the fifth attempt a no-op and caps the loop at four fixes.
`tests/test_driver_cycle.py::DecisionTest` pins both directions.

**The renderer cannot emit these tags yet.** PR-Agent's review schema has no
severity field, so `scripts/render_contract_comment.py` writes every bullet
untagged and on a real PR every finding blocks. That costs nothing on the case
that actually occurs (4/5 with an empty Issues section still succeeds), but the
"4/5 with only nits" exit stays dormant until a severity reaches the comment.
Fixing that is a CONTRACT.md change, which requires the renderer, `.pr_agent.toml`
and the parser to move together. `tests/test_severity_roundtrip.py` records the
gap and is the test that should fail first when it is closed.

### A fix that changes nothing ends the loop early

A loop with five attempts assumes attempts make progress. When Pi cannot fix a
finding, or fixes it in a way the reviewer still objects to, the remaining
attempts are paid for and buy nothing; the PR ends up needing a human anyway,
four iterations later. `decide` therefore compares the new review's blocking
findings against the review Pi was given on the previous attempt, and returns
`REPEATED` when they match.

The comparison key is `Review.blocking_fingerprint`: a sorted tuple of the
normalised `path:message` of each P0/P1 finding. It must be loose about things a
re-review changes and strict about things that mean the finding changed.
Normalised away: line numbers and ranges, the severity tag, the header word
before the colon, case, repeated spaces, trailing punctuation, bullet order and
duplicate bullets. Kept: the path and the message. So `a.py:1` and `a.py:57` are
the same finding (Pi's commit moves lines), and `a.py` and `b.py` are not. Folding
punctuation out of the path as well would make `a.py` and `a/py` the same file,
which is why the path is only lowercased.

Two guards keep it from firing when it should not:

- **`iteration > 0`.** The cycle's first decision for a review is made before any
  attempt against it, so there is no `previous` to pass. Comparing there would
  stop the loop before Pi ran.
- **A non-empty fingerprint.** `is_clean` lets a review with no issues through at
  a low score, and two such reviews both fingerprint empty. Without the guard
  they compare equal and report a repeat for a pair of reviews that list nothing.
  No findings twice is a score problem, not a fix that did nothing.

Detection reads only blocking findings, so a reviewer reshuffling its P2 nits
between attempts neither looks like progress nor triggers a stop.

### The contract comment is rendered by code, not by the model

PR-Agent always reshapes model output into a GFM table
(`pr_agent/algo/utils.py::convert_to_markdown_v2`), so it cannot emit the three
`##` headings. `scripts/render_contract_comment.py` builds the comment from
PR-Agent's structured review instead. Do not try to get the headings out of
`extra_instructions`.
