# CONTRACT.md – PR-Agent Output Contract

This document defines the exact comment format the loop parses.

Any change to this contract requires updating all three:

- `scripts/render_contract_comment.py` (produces the comment)
- `.pr_agent.toml` (the instructions given to PR-Agent)
- The parser (Slice 2+)

---

## Who renders the comment

The three sections are **not** produced by the model. PR-Agent assembles its
review comment in Python (`convert_to_markdown_v2` in `pr_agent/algo/utils.py`),
which reshapes the model's output into a GFM table, so `##` headings requested
via `extra_instructions` never reach GitHub.

The workflow therefore takes the structured review PR-Agent emits, renders the
contract comment from it, and posts that:

```
.pr_agent.toml      -> model emits score + key_issues_to_review + agent_prompt
push_outputs (file) -> pr-agent-output/review.jsonl
scripts/render_contract_comment.py -> contract-comment.md
gh pr comment       -> the only review comment on the PR
```

The model's own prose stays out of the comment, which makes the format a
property of code we test rather than of model behaviour. PR-Agent's review is
published as a check run instead of a comment (`github.publish_as_check_run`),
because switching it off entirely would also switch off `push_outputs`.

---

## Required Output Structure

Every contract comment consists of these three sections, in this order, with
nothing after the last one:

```markdown
## Confidence Score
X/5

## Issues
- path/to/file.py:42-50 — Missing timeout: The poll loop never times out.
- another/file.ts:88 — Off-by-one: The loop skips the last element.

## Agent Prompt
A single, self-contained prompt that a coding agent can copy-paste.
It must include concrete file paths and clear instructions on how to fix all the issues listed above.
```

### Section rules

| Section | Header | Body |
|---|---|---|
| 1 | `## Confidence Score` | A single line `X/5` where `X` is an integer 0–5. |
| 2 | `## Issues` | Zero or more bullets, or the single line `None`. |
| 3 | `## Agent Prompt` | Free-form text, everything until the end of the comment. May be several paragraphs. |

An issue bullet is `- <location> — <issue header>: <one-sentence description>`,
where the location is `file`, `file:line`, or `file:start-end`. A finding with
no usable line number falls back to the bare path.

### Score scale

The model scores a PR from 0 to 100, which is PR-Agent's schema. The renderer
converts that to fifths, so the loop only ever compares one scale:

| PR-Agent score | Contract score |
|---|---|
| 90–100 | 5/5 |
| 70–89 | 4/5 |
| 50–69 | 3/5 |
| 30–49 | 2/5 |
| 10–29 | 1/5 |
| 0–9 | 0/5 |

A score outside 0–100 is a contract violation, not a clamp.

### Agent Prompt

PR-Agent's review schema has no `agent_prompt` field, so the renderer asks the
model for one through `extra_instructions`. When the model omits it, the
renderer builds a deterministic prompt from the findings rather than failing:
model-side omissions are expected, and a stalled loop is worse than a derived
prompt. It fails only when there is neither a prompt nor any finding, since
then there is nothing to hand an agent.

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