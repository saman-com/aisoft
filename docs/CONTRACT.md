# CONTRACT.md – PR-Agent Output Contract

This document defines the exact format that PR-Agent must produce and that the local driver must parse.

Any change to this contract requires updating both:
- `.pr_agent.toml` (the instructions given to PR-Agent)
- The local parser (Slice 2+)

---

## Required Output Structure

PR-Agent **must** end every review comment with these three sections, in this exact order, and with nothing after the last section:

```markdown
## Confidence Score
X/5

## Issues
- [P0/P1/P2] path/to/file:line — short description of the issue
- [P1] another/file.ts:88 — another issue

## Agent Prompt
A single, self-contained prompt that a coding agent can copy-paste.
It must include concrete file paths and clear instructions on how to fix all the issues listed above.
Do not add any text after this section.