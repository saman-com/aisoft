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
```

### Section rules

| Section | Header | Body |
|---|---|---|
| 1 | `## Confidence Score` | A single line `X/5` where `X` is an integer 0–5. |
| 2 | `## Issues` | Zero or more Markdown bullet lines, each starting with `- [P0]`, `- [P1]` or `- [P2]`. |
| 3 | `## Agent Prompt` | Free-form text, everything until the end of the comment. May be several paragraphs. |

Anything before the first header is allowed (PR-Agent may print a summary); the
parser only cares about the three sections.

---

## Parsing rules

The parser scans the comment for the three headers **in the order above** and
extracts the text between each header and the next one (the Agent Prompt runs to
the end of the comment).

1. **Locate headers.** A header matches only when the line starts with `##`
   followed by the exact title, e.g. `## Confidence Score`. A header that
   appears inside a fenced code block is ignored.
2. **Confidence Score.** From the section body take the first integer that is
   followed by `/5`. Accept optional whitespace and an optional leading
   `Score:`/`Confidence:` label. The result must be an integer in 0–5.
3. **Issues.** Keep every non-empty line that starts with `-` (after trimming).
   Strip the bullet marker. When the section is empty, or contains only a
   line such as `None` / `No issues`, treat the list as empty.
4. **Agent Prompt.** Take the section body verbatim, trimmed of leading and
   trailing blank lines. Do not rewrite, wrap or re-indent it — it is fed to
   Pi as-is.

---

## Failure rules

If any of the three sections is missing, malformed, or out of order the parser
**must fail loudly**. It must not guess a default score and must not hand Pi a
partial prompt.

- Missing or duplicated header → error `missing section: <name>`.
- Score not parseable as an integer in 0–5 → error `invalid confidence score: <raw text>`.
- Sections present but out of the required order → error `sections out of order`.
- Agent Prompt empty after trimming → error `empty agent prompt`.

Every error must include the PR number (or comment id) and the offending text so
the failure can be debugged without re-reading the comment.

Sections are matched case-sensitively. A comment whose score is 5/5 but whose
Issues/Agent Prompt sections are missing is still a contract violation: the
loop needs the prompt text, and a silent pass would hide a broken workflow.

---

## Worked example

Input comment (tail):

```markdown
## Review Summary
Two problems in the driver.

## Confidence Score
3/5

## Issues
- [P0] driver.py:42 — polling loop never times out
- [P1] driver.py:88 — commit message missing iteration number

## Agent Prompt
Fix driver.py. Add a configurable timeout to the polling loop at line 42 and
include the iteration number in the commit message built at line 88.
```

Parsed result:

```json
{
  "score": 3,
  "issues": [
    "[P0] driver.py:42 — polling loop never times out",
    "[P1] driver.py:88 — commit message missing iteration number"
  ],
  "agent_prompt": "Fix driver.py. Add a configurable timeout to the polling loop at line 42 and\ninclude the iteration number in the commit message built at line 88."
}
```