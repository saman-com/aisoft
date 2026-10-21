# TASKS.md – Pi Agent + PR-Agent Review Loop

**Goal:** Build an end-to-end system where Pi writes code → creates a PR → PR-Agent reviews it (score out of 5) → Pi fixes issues in a loop (max 5 times) → human merges when score is 5/5.

**How to use this file with an AI agent:**
1. Tell the agent: “Work only on the next open task in TASKS.md. Mark it done when finished. Do not start the next slice until the current slice is complete.”
2. After each task, the agent should update the checkbox and add a short note if needed.
3. Always validate the exit criteria of a slice before moving to the next one.

---

## Progress Overview

- [x] **Slice 0** – Foundation & Contracts
- [x] **Slice 1** – PR-Agent Review Only
- [x] **Slice 2** – Local Parser (Read-Only)
- [x] **Slice 3** – Single Fix Cycle
- [ ] **Slice 4** – Full Loop (max 5 iterations)
- [ ] **Slice 5** – Pi Creates the PR

---

## Slice 0 – Foundation & Contracts
**Exit criteria:** Repo has the correct files and a clear output contract. No runtime behavior required yet.

- [x] **T0.1** Create `.pr_agent.toml` with the exact output contract  
  Force `## Confidence Score`, `## Issues`, `## Agent Prompt` format.

- [x] **T0.2** Document the parsing contract  
  Create `CONTRACT.md` (or a section in README) that defines how to extract Score / Issues / Agent Prompt.

- [x] **T0.3** Create empty GitHub Action skeleton  
  `.github/workflows/pr-agent.yml` that triggers on pull_request but does nothing yet.

- [x] **T0.4** Document required secrets  
  Note that `OPENROUTER_API_KEY` must be added in GitHub Secrets.

**Slice 0 complete when:** All four tasks above are checked and the files exist.

---

## Slice 1 – PR-Agent Review Only
**Exit criteria:** Opening or pushing to a PR automatically produces a structured review comment containing Score / Issues / Agent Prompt.

- [x] **T1.1** Implement real GitHub Action with PR-Agent + OpenRouter

- [x] **T1.2** Configure model and review flags  
  (`require_score_review`, risk assessment, merge recommendation)

- [x] **T1.3** Force structured output format
  (PR-Agent builds its comment in Python, so the contract sections are rendered
  by `scripts/render_contract_comment.py` from the structured review and posted
  by the workflow — see CONTRACT.md)

- [x] **T1.4** Manual validation  
  Open a test PR and confirm the comment matches the contract. Attach example output.

**Slice 1 complete when:** A real PR produces a correctly structured review comment.

---

## Slice 2 – Local Parser (Read-Only)
**Exit criteria:** A local script can reliably extract the Confidence Score and Agent Prompt from an existing PR-Agent comment.

- [x] **T2.1** Decide driver language (recommend Python)
  Decision: Python 3, standard library only. Rationale in issue #9.

- [x] **T2.2** Implement PR detection (`gh` CLI)
  (`scripts/driver_pr.py` — `NoPullRequest` and `GhFailed` are distinct on purpose)

- [x] **T2.3** Implement comment polling / fetching
  (`scripts/driver_comments.py` — waits for a comment *newer than* the one already read)

- [x] **T2.4** Implement robust parser
  (`scripts/parse_contract_comment.py` — strict, never defaults a score) for the three sections

- [x] **T2.5** Add clear logging
  (`scripts/driver_log.py` and `scripts/driver.py` — failures print as ERROR, never as a score) of what was parsed

**Slice 2 complete when:** Running the script against a PR that already has a review correctly prints the score and the Agent Prompt.

---

## Slice 3 – Single Fix Cycle
**Exit criteria:** One full cycle works: read review → give prompt to Pi → Pi fixes → commit + push → new review appears.

- [x] **T3.1** Determine Pi non-interactive invocation command
  `pi --print --no-session --approve < prompt.md` — `scripts/driver_pi.py`, documented in `AGENT.md`

- [x] **T3.2** Safe commit & push logic
  `scripts/driver_git.py` — explicit refspec, no force, remote ref verified after push;
  refuses master and a branch Pi switched to; `NothingToPush` for a no-op

- [x] **T3.3** Wait for the re-review after push
  `scripts/driver_comments.py::wait_for_contract_comment` — 600 s timeout, 15 s interval,
  freshness by body so an in-place rewrite counts as new; the old review never counts

- [x] **T3.4** End-to-end single-cycle test on a real PR
  - `scripts/driver_cycle.py` wires the Slice 2 parser, `driver_pi`, `driver_git` and
    the T3.3 wait into one cycle: read review, Pi, commit+push, wait for a newer
    review, parse it
  - verified on PR #29 (workflow run 39675907534): 4/5 with one issue read, Pi
    fixed it, the push landed, and a rewritten review arrived as 4/5 with a
    different issue - no manual copy-paste at any step
  - the same run exposed three real defects, each fixed with a failing test first:
    a single-branch clone never creates the tracking ref for a newly pushed
    branch (the read-back aborted a push that had landed), an empty `updated_at`
    read as a rewrite, and the exit-code ordering that keeps a missing review at
    exit 2

**Slice 3 complete when:** One complete fix cycle runs without manual copy-paste.

---

## Slice 4 – Full Loop with Guardrails
**Exit criteria:** Driver can run up to 5 iterations and stops cleanly on score 5/5 or when the limit is reached.

- [x] **T4.1** Add iteration counter and hard limit of 5
  `scripts/driver_iteration.py` holds `MAX_ITERATIONS = 5` in one place, and
  `check_iteration` refuses attempt 6 (and 0) *before* the PR is read or Pi runs, so
  a sixth push cannot land. Logs show `iteration N/5`. `driver_cycle.run_cycle`
  calls it first.

- [x] **T4.2** Implement decision logic (score == 5 → stop, else fix, else give up)
  Success exit is `score == 5` **or** `score >= 4` with no P0/P1 issues in the
  Issues section, since the reviewer plateaus at 4/5 while listing only
  low-confidence findings (see PR #27). `scripts/driver_decision.py::decide`
  returns `SUCCESS` / `CONTINUE` / `HUMAN` (plus `REPEATED` from T4.3) and
  prints one log line per path;
  `Review.is_clean` carries the rule so there is no second copy of it. Severity is
  read from `[P0]`/`[P1]`/`[P2]` tags, and an untagged or unknown tag blocks.
  Optional follow-up: tighten `extra_instructions` so only confirmed issues affect
  the score.

  _Known gap: the renderer emits no severity tags, because PR-Agent's schema has
  none. The 4/5-with-only-nits exit is therefore unreachable on a real PR until
  the contract gains a severity. Recorded in `tests/test_severity_roundtrip.py`._

- [x] **T4.3** (Optional) Light repeated-issue detection
  Implemented, not deferred. `Review.blocking_fingerprint` in
  `scripts/parse_contract_comment.py` reduces each P0/P1 finding to a sorted
  tuple of normalised `path:message` keys; `decide(..., previous=...)` in
  `scripts/driver_decision.py` returns `Outcome.REPEATED` when a new review's
  fingerprint equals the fingerprint of the review Pi was given, and the cycle
  passes that previous review on every attempt after the first. Order, duplicate
  bullets, line numbers, severity tags, header wording, case and trailing
  punctuation are all normalised away, so a moved line is still the same
  finding. A different file or a different message is not, so real progress
  keeps the remaining attempts. Success is decided first, and an empty
  fingerprint never counts as a repeat, so a clean review and a pair of
  issue-free low scores both stay out of the rule.

- [ ] **T4.4** Final status reporting (“Ready for merge” or “Human needed”)

- [ ] **T4.5** Full loop integration test on at least two PRs

**Slice 4 complete when:** The loop can take a PR from <5 to either 5/5 or the iteration limit without human intervention (except final merge).

---

## Slice 5 – Pi Creates the PR
**Exit criteria:** Pi (or the driver) can also create the initial PR so the whole flow can start from “Pi finished coding”.

- [ ] **T5.1** PR creation logic (`gh pr create`)

- [ ] **T5.2** Idempotent handling (reuse existing PR if present)

- [ ] **T5.3** Full start-to-finish integration test

**Slice 5 complete when:** From a clean feature branch the system can create the PR and run the full review/fix loop.

---

## Notes for the AI Agent

- Work on **only one task** at a time.
- After finishing a task, check the box and write a one-line note if useful.
- Never start a new slice until the previous slice’s exit criteria are met.
- When in doubt, re-read the design document and the CONTRACT.
- Prefer small, testable changes over big rewrites.