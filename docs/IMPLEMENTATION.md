---

# Tracer-Bullet Implementation Plan  
**Pi Agent + PR-Agent Autonomous Review Loop**

---

## Slice 0 – Foundation & Contracts (Prerequisite)
**Goal:** Establish the shared contracts and repository structure so every later slice has a stable target.

**What it delivers:**
- Repository has the correct files and documented output contract
- No runtime behavior yet

**Tickets that block this slice:**
| ID | Ticket | Description |
|----|--------|-------------|
| T0.1 | Create `.pr_agent.toml` with the exact output contract | Force `## Confidence Score`, `## Issues`, `## Agent Prompt` format |
| T0.2 | Document the parsing contract | Write a short `CONTRACT.md` that defines how the score and Agent Prompt must be extracted |
| T0.3 | Create empty GitHub Action skeleton | `.github/workflows/pr-agent.yml` that does nothing yet (or just echoes) |
| T0.4 | Add required secrets placeholders | Document `OPENROUTER_API_KEY` in README / docs |

**Exit criteria:** An AI agent (or human) can open the repo and clearly see the required output format and file locations.

**Blocks:** All later slices

---

## Slice 1 – PR-Agent Review Only (Cloud Side)
**Goal:** Prove that opening/updating a PR automatically produces a structured review with a score out of 5.

**What it delivers:**
- Any PR triggers PR-Agent via GitHub Actions + OpenRouter
- Review comment always contains the three required sections
- Human can manually read the score

**Tickets that block this slice:**
| ID | Ticket | Description |
|----|--------|-------------|
| T1.1 | Implement real GitHub Action | Wire `the-pr-agent/pr-agent` with OpenRouter |
| T1.2 | Configure model + flags | Set `config.model`, `require_score_review=true`, etc. |
| T1.3 | Force structured output | Put the strict `extra_instructions` into `.pr_agent.toml` |
| T1.4 | Manual validation | Open a test PR and confirm the comment contains Score / Issues / Agent Prompt |

**Exit criteria:**  
Opening or pushing to a PR reliably produces a comment that matches the contract. Score is visible and human-readable.

**Blocks:** Slice 2 and Slice 3

---

## Slice 2 – Local Parser (Read-Only)
**Goal:** Prove that a local script can reliably extract Score + Agent Prompt from the PR-Agent comment.

**What it delivers:**
- A local script (bash or Python) that, given a PR number, prints:
  - Current Confidence Score
  - The full Agent Prompt
- No code changes yet

**Tickets that block this slice:**
| ID | Ticket | Description |
|----|--------|-------------|
| T2.1 | Choose driver language | Decide Bash vs Python (recommend Python for robustness) |
| T2.2 | Implement PR detection | Use `gh` to find the open PR for the current branch |
| T2.3 | Implement comment polling | Wait for the latest PR-Agent bot comment |
| T2.4 | Implement robust parser | Extract `## Confidence Score`, `## Issues`, `## Agent Prompt` |
| T2.5 | Add clear logging | Script prints what it found and any parse failures |

**Exit criteria:**  
Running the script against a PR that already has a Slice-1 review correctly prints the score and the Agent Prompt.

**Blocks:** Slice 3

---

## Slice 3 – Single Fix Cycle (One Iteration)
**Goal:** Prove the core feedback loop works once: read review → give prompt to Pi → Pi fixes → push → new review appears.

**What it delivers:**
- Local driver can:
  1. Read current score & Agent Prompt
  2. Feed the Agent Prompt to Pi (non-interactively)
  3. Commit + push the changes
  4. Wait for the new PR-Agent review
- Only one iteration (no loop yet)

**Tickets that block this slice:**
| ID | Ticket | Description |
|----|--------|-------------|
| T3.1 | Pi non-interactive invocation | Figure out the exact command to run Pi with a prompt (`pi -p "..."` or equivalent) |
| T3.2 | Safe commit & push | Driver commits with a clear message and pushes |
| T3.3 | Wait for re-review | After push, poll until a new/updated PR-Agent comment appears |
| T3.4 | End-to-end manual test | Run one full cycle on a real PR and verify the score changes or issues are reduced |

**Exit criteria:**  
One complete “review → fix → push → new review” cycle works end-to-end without manual copy-paste.

**Blocks:** Slice 4

---

## Slice 4 – Full Loop with Guardrails (Max 5 Iterations)
**Goal:** Turn the single cycle into a controlled loop that stops at score 5/5 or after 5 attempts.

**What it delivers:**
- Driver runs the full loop:
  - Up to 5 iterations
  - Stops cleanly on score == 5
  - Stops cleanly on max iterations
  - Clear terminal status at every step
- Human is notified when the PR is ready (or when the limit is hit)

**Tickets that block this slice:**
| ID | Ticket | Description |
|----|--------|-------------|
| T4.1 | Add iteration counter & limits | Hard max of 5 |
| T4.2 | Decision logic | `if score == 5 → success; else if iterations < 5 → fix; else → stop` |
| T4.3 | Repeated-issue detection (light) | Optional early stop if the same issues keep appearing |
| T4.4 | Final status reporting | Clear message: “Ready for merge (5/5)” or “Stopped after 5 iterations – human needed” |
| T4.5 | Full loop test | Run the complete loop on at least two different PRs |

**Exit criteria:**  
The driver can take a PR from score < 5 to either 5/5 or the iteration limit without human intervention (except the final merge).

**Blocks:** Slice 5

---

## Slice 5 – Pi Creates the PR (Full Autonomy Start)
**Goal:** Let Pi also create the initial PR so the whole flow can start from “Pi finishes coding”.

**What it delivers:**
- Pi (or the driver) can create the PR if one does not exist
- After creation, the normal review + fix loop begins automatically

**Tickets that block this slice:**
| ID | Ticket | Description |
|----|--------|-------------|
| T5.1 | PR creation logic | `gh pr create` with sensible title/body. Refuses when a PR is already open |
| T5.2 | Idempotency | If an open PR exists for the branch, reuse it (`ensure_pull_request`); create only when none is open |
| T5.3 | Integration test | From a clean branch → Pi finishes work → PR is created → loop starts |

**Exit criteria:**  
A developer can say “finish this feature and open a PR” and the system takes it all the way to a scored review (and optional fixes).

**Blocks:** None (this is the final core slice)

---

## Optional / Later Slices (Not required for MVP)

| Slice | Name | Purpose |
|-------|------|---------|
| 6 | Better observability | Logging, dry-run mode, desktop notifications |
| 7 | Cost & model controls | Easy model switching, budget warnings |
| 8 | Multi-repo / team ready | Shared config, organization-level secrets |

---

## Dependency Graph (Summary)

```
Slice 0 (Contracts)
    ↓
Slice 1 (PR-Agent works)
    ↓
Slice 2 (Local parser works)
    ↓
Slice 3 (One fix cycle works)
    ↓
Slice 4 (Full 5-iteration loop)
    ↓
Slice 5 (Pi can also create the PR)
```

---

## Recommended Execution Order for an AI Agent

1. **Do Slice 0 + Slice 1 first** (can be done in one session).  
   Validate by opening a real PR.

2. **Then Slice 2** – pure local parsing. Easy to test against existing reviews.

3. **Then Slice 3** – the first real feedback loop. Highest learning value.

4. **Then Slice 4** – turn it into a proper loop with safety limits.

5. **Finally Slice 5** – close the autonomy loop.

This order guarantees that every slice produces a working, testable vertical path before more complexity is added.

---