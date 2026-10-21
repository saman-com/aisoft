# TASKS.md – Pi Agent + PR-Agent Review Loop

**Goal:** Build an end-to-end system where Pi writes code → creates a PR → PR-Agent reviews it (score out of 5) → Pi fixes issues in a loop (max 5 times) → human merges when score is 5/5.

**How to use this file with an AI agent:**
1. Tell the agent: “Work only on the next open task in TASKS.md. Mark it done when finished. Do not start the next slice until the current slice is complete.”
2. After each task, the agent should update the checkbox and add a short note if needed.
3. Always validate the exit criteria of a slice before moving to the next one.

---

## Progress Overview

- [x] **Slice 0** – Foundation & Contracts
- [ ] **Slice 1** – PR-Agent Review Only
- [ ] **Slice 2** – Local Parser (Read-Only)
- [ ] **Slice 3** – Single Fix Cycle
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

- [ ] **T1.4** Manual validation  
  Open a test PR and confirm the comment matches the contract. Attach example output.

**Slice 1 complete when:** A real PR produces a correctly structured review comment.

---

## Slice 2 – Local Parser (Read-Only)
**Exit criteria:** A local script can reliably extract the Confidence Score and Agent Prompt from an existing PR-Agent comment.

- [ ] **T2.1** Decide driver language (recommend Python)

- [ ] **T2.2** Implement PR detection (`gh` CLI)

- [ ] **T2.3** Implement comment polling / fetching

- [ ] **T2.4** Implement robust parser for the three sections

- [ ] **T2.5** Add clear logging of what was parsed

**Slice 2 complete when:** Running the script against a PR that already has a review correctly prints the score and the Agent Prompt.

---

## Slice 3 – Single Fix Cycle
**Exit criteria:** One full cycle works: read review → give prompt to Pi → Pi fixes → commit + push → new review appears.

- [ ] **T3.1** Determine Pi non-interactive invocation command

- [ ] **T3.2** Safe commit & push logic

- [ ] **T3.3** Wait for the re-review after push

- [ ] **T3.4** End-to-end single-cycle test on a real PR

**Slice 3 complete when:** One complete fix cycle runs without manual copy-paste.

---

## Slice 4 – Full Loop with Guardrails
**Exit criteria:** Driver can run up to 5 iterations and stops cleanly on score 5/5 or when the limit is reached.

- [ ] **T4.1** Add iteration counter and hard limit of 5

- [ ] **T4.2** Implement decision logic (score == 5 → stop, else fix, else give up)

- [ ] **T4.3** (Optional) Light repeated-issue detection

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