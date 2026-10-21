---

### Slice 0 – Foundation & Contracts

**Issue: [T0.1] Create `.pr_agent.toml` with the exact output contract**
```
## Description
Create the file `.pr_agent.toml` in the repository root that forces PR-Agent to always output the three required sections:

- ## Confidence Score
- ## Issues
- ## Agent Prompt

## Acceptance Criteria
- [ ] File exists at repo root
- [ ] Contains `require_score_review = true`
- [ ] Contains strict `extra_instructions` that enforce the exact output format defined in the design document
- [ ] Model is set via OpenRouter (placeholder is fine)

## Slice
Slice 0 – Foundation & Contracts
```

**Issue: [T0.2] Document the parsing contract**
```
## Description
Create a short `CONTRACT.md` (or section in README) that clearly defines how a local script must extract:

1. Confidence Score (X/5)
2. Issues list
3. Agent Prompt

## Acceptance Criteria
- [ ] Document exists
- [ ] Shows the exact expected Markdown structure
- [ ] Describes parsing rules (what to do if a section is missing)

## Slice
Slice 0 – Foundation & Contracts
```

**Issue: [T0.3] Create empty GitHub Action skeleton**
```
## Description
Create `.github/workflows/pr-agent.yml` as a minimal skeleton. It does not need to call PR-Agent yet.

## Acceptance Criteria
- [ ] Workflow file exists
- [ ] Triggers on `pull_request` (opened, synchronize, reopened)
- [ ] Has a placeholder job that succeeds
- [ ] Comments explain where the real PR-Agent step will go

## Slice
Slice 0 – Foundation & Contracts
```

**Issue: [T0.4] Document required secrets**
```
## Description
Document that the repository needs the secret `OPENROUTER_API_KEY`.

## Acceptance Criteria
- [ ] README or docs mention the required secret
- [ ] Clear instruction on where to add it (Settings → Secrets and variables → Actions)

## Slice
Slice 0 – Foundation & Contracts
```

---

### Slice 1 – PR-Agent Review Only

**Issue: [T1.1] Implement real GitHub Action with PR-Agent**
```
## Description
Replace the skeleton workflow with a working call to `the-pr-agent/pr-agent` using OpenRouter.

## Acceptance Criteria
- [ ] Uses `the-pr-agent/pr-agent@main` (or a pinned version)
- [ ] Passes `OPENROUTER__KEY` from secrets
- [ ] Passes `GITHUB_TOKEN`
- [ ] Sets `github_action_config.auto_review: "true"`

## Slice
Slice 1 – PR-Agent Review Only
```

**Issue: [T1.2] Configure model and review flags**
```
## Description
Configure the model and enable the required review sections via environment variables or `.pr_agent.toml`.

## Acceptance Criteria
- [ ] Model is set (e.g. `openrouter/anthropic/claude-sonnet-4` or chosen model)
- [ ] `pr_reviewer.require_score_review` is true
- [ ] `require_risk_assessment` and `require_merge_recommendation` are enabled

## Slice
Slice 1 – PR-Agent Review Only
```

**Issue: [T1.3] Force structured output format**
```
## Description
Ensure the `extra_instructions` in `.pr_agent.toml` force the exact three-section format every time.

## Acceptance Criteria
- [ ] Review comments always end with:
  - ## Confidence Score
  - ## Issues
  - ## Agent Prompt
- [ ] No extra text appears after the Agent Prompt section

## Slice
Slice 1 – PR-Agent Review Only
```

**Issue: [T1.4] Manual validation of structured review**
```
## Description
Open a test PR and verify that PR-Agent produces a correctly structured comment.

## Acceptance Criteria
- [ ] Test PR is created
- [ ] Review comment appears
- [ ] Score is present and readable (X/5)
- [ ] Issues list and Agent Prompt are present
- [ ] Screenshot or copy of the comment is attached to this issue

## Slice
Slice 1 – PR-Agent Review Only
```

---

### Slice 2 – Local Parser (Read-Only)

**Issue: [T2.1] Decide driver language**
```
## Description
Choose the language for the local driver script (Bash or Python). Recommendation: Python for robust parsing.

## Acceptance Criteria
- [ ] Decision is recorded (in this issue or in docs)
- [ ] Rationale is briefly noted

## Slice
Slice 2 – Local Parser
```

**Issue: [T2.2] Implement PR detection**
```
## Description
Driver must detect the open PR associated with the current git branch using the `gh` CLI.

## Acceptance Criteria
- [ ] Running the script on a branch with an open PR correctly identifies the PR number and URL
- [ ] Clear error if no PR exists

## Slice
Slice 2 – Local Parser
```

**Issue: [T2.3] Implement comment polling**
```
## Description
Driver must wait for (or fetch) the latest review comment posted by PR-Agent.

## Acceptance Criteria
- [ ] Can retrieve the most recent PR-Agent comment
- [ ] Handles the case when the comment has not appeared yet (polling or clear message)

## Slice
Slice 2 – Local Parser
```

**Issue: [T2.4] Implement robust parser**
```
## Description
Parse the three required sections from the PR-Agent comment.

## Acceptance Criteria
- [ ] Correctly extracts Confidence Score as an integer 0–5
- [ ] Extracts the full Issues list
- [ ] Extracts the full Agent Prompt text
- [ ] Fails gracefully with a clear message if any section is missing

## Slice
Slice 2 – Local Parser
```

**Issue: [T2.5] Add clear logging**
```
## Description
The driver must print what it found (score, number of issues, length of prompt, etc.).

## Acceptance Criteria
- [ ] Running the script produces human-readable output showing the parsed values
- [ ] Parse failures are obvious

## Slice
Slice 2 – Local Parser
```

---

### Slice 3 – Single Fix Cycle

**Issue: [T3.1] Pi non-interactive invocation**
```
## Description
Determine and document the exact command to run Pi non-interactively with a given prompt.

## Acceptance Criteria
- [ ] Command is documented
- [ ] Can be called from the driver script
- [ ] Pi receives the full Agent Prompt and can edit files

## Slice
Slice 3 – Single Fix Cycle
```

**Issue: [T3.2] Safe commit & push**
```
## Description
After Pi finishes, the driver must stage, commit, and push the changes.

## Acceptance Criteria
- [ ] Uses a clear commit message (e.g. "fix: address PR-Agent feedback (iteration 1)")
- [ ] Pushes to the same branch
- [ ] Does not force-push

## Slice
Slice 3 – Single Fix Cycle
```

**Issue: [T3.3] Wait for re-review**
```
## Description
After pushing, the driver must wait until a new or updated PR-Agent review comment appears.

## Acceptance Criteria
- [ ] Polls with a reasonable interval
- [ ] Has a timeout
- [ ] Detects that a new review has been posted

## Slice
Slice 3 – Single Fix Cycle
```

**Issue: [T3.4] End-to-end single-cycle test**
```
## Description
Run one complete cycle on a real PR: read review → Pi fixes → push → new review appears.

## Acceptance Criteria
- [ ] Full cycle completes without manual copy-paste
- [ ] New review comment is generated after the push
- [ ] Results (before/after score) are recorded in this issue

## Slice
Slice 3 – Single Fix Cycle
```

---

### Slice 4 – Full Loop with Guardrails

**Issue: [T4.1] Add iteration counter and hard limit**
```
## Description
Implement a maximum of 5 iterations.

## Acceptance Criteria
- [ ] Loop never exceeds 5 iterations
- [ ] Iteration number is visible in logs and commit messages

## Slice
Slice 4 – Full Loop
```

**Issue: [T4.2] Decision logic**
```
## Description
Implement the core decision:

- score == 5 → success, stop
- score < 5 and iterations < 5 → fix and continue
- iterations == 5 → stop and hand to human

## Acceptance Criteria
- [ ] Logic matches the design document
- [ ] Clear messages for each exit path

## Slice
Slice 4 – Full Loop
```

**Issue: [T4.3] Light repeated-issue detection (optional but recommended)**
```
## Description
Optionally detect when the same issues keep reappearing and stop early.

## Acceptance Criteria
- [ ] Basic detection is implemented or explicitly deferred
- [ ] Behavior is documented

## Slice
Slice 4 – Full Loop
```

**Issue: [T4.4] Final status reporting**
```
## Description
When the loop ends, print a clear final status.

## Acceptance Criteria
- [ ] Message indicates either “Ready for merge (5/5)” or “Stopped after N iterations – human review needed”
- [ ] PR URL is shown

## Slice
Slice 4 – Full Loop
```

**Issue: [T4.5] Full loop integration test**
```
## Description
Run the complete 5-iteration-capable loop on at least two different PRs.

## Acceptance Criteria
- [ ] At least one PR reaches score 5/5
- [ ] At least one test hits the iteration limit cleanly
- [ ] Results are recorded

## Slice
Slice 4 – Full Loop
```

---

### Slice 5 – Pi Creates the PR

**Issue: [T5.1] PR creation logic**
```
## Description
Driver (or Pi) can create a pull request if one does not already exist for the current branch.

## Acceptance Criteria
- [ ] Uses `gh pr create` (or equivalent)
- [ ] Sensible title and body are generated
- [ ] Works from a feature branch

## Slice
Slice 5 – Pi Creates the PR
```

**Issue: [T5.2] Idempotent PR handling**
```
## Description
If a PR already exists for the branch, reuse it instead of creating a duplicate.

## Acceptance Criteria
- [ ] Detects existing PR
- [ ] Continues with the existing PR number
- [ ] No duplicate PRs are created

## Slice
Slice 5 – Pi Creates the PR
```

**Issue: [T5.3] Full start-to-finish integration test**
```
## Description
From a clean feature branch: Pi finishes work → PR is created → review loop runs → reaches terminal state.

## Acceptance Criteria
- [ ] Complete flow works without manual PR creation
- [ ] Final state is either 5/5 or max iterations reached
- [ ] Results are documented in this issue

## Slice
Slice 5 – Pi Creates the PR
```

---
