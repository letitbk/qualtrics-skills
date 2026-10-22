---
name: qualtrics-survey
description: Use when building, editing, auditing, or fielding a Qualtrics survey through the v3 API or QuestionJS — creating or moving questions and blocks, survey flow branches and embedded data, display logic, Loop & Merge, publishing and activation, response export and test-row cleanup — and when a survey misbehaves silently (a question never renders, a branch is always false, an answer is not stored, JS does not fire, an automation browser sees "AlreadyTaken").
---

# Qualtrics Survey Management (API + QuestionJS)

## Overview

The Qualtrics API accepts almost anything and returns 200; the survey engine then does something else. Two rules cover most of what goes wrong. Every write is fresh GET → mutate → PUT the full object → fresh GET to verify. Every change that affects what respondents see or what gets stored is proven by a recorded test response whose **export** is inspected, never by looking at the page.

## When to use

- Building or changing a survey by API (questions, blocks, flow, embedded data, options, Loop & Merge, JS).
- Auditing a survey someone else built or copied (provenance, stale tags, trashed questions, dead branches).
- Preparing to field: publish, activate, redirects, test rows, cleanup.
- Debugging: "question never shows", "everyone screened out", "answer missing from export", "JS works in preview only", "survey never renders for the walker".

Not for: analysing exported data (use the project's analysis pipeline) or for walking a survey with scripted respondents end to end (REQUIRED SUB-SKILL for that: `survey-persona-audit`).

## Setup

```bash
export QUALTRICS_API_KEY='...'
export QUALTRICS_BASE_URL='yourorg.qualtrics.com'   # datacenter host, no https://
```
`scripts/qx.py` also reads both from `~/.Renviron`. The survey ID comes from the request or the project's `CLAUDE.md` (`SV_...`). Run `python3 scripts/qx.py --help` for the CLI (snapshot, published, diff, invariants, list, flow, publish, activate, export, delete-qa).

## The build contract

Do these in order for every change. Skipping a step is how the documented failures happened.

1. **Snapshot first.** Write the full draft definition to disk before any write, and diff the fresh draft against the last snapshot before touching anything — collaborators edit in the builder between sessions.
2. **Write safely.** Fresh GET → mutate → PUT the whole object. A partial PUT is a 400; a PUT without the fetched `QuestionJS` silently deletes the question's JS (omit the key only when you mean to delete it).
3. **Verify by fresh GET.** The PUT status proves nothing: flow end-of-survey options, options PUTs and question edits all return 200 without applying.
4. **Run invariants** (`python3 scripts/qx.py invariants SV_...`) and clear every line: duplicate export tags, Trash∩live, dangling QID references in branches and display logic, one timer per page, ForceResponse, `AnalyzeChoices=No` (data loss), orphans, loop row keys.
5. **Publish and re-check active.** Nothing is served until `POST /versions {Published:true}`; publishing can flip an inactive survey active. `qx.py publish` re-checks `isActive` for you.
6. **Prove it in the export.** Record a test response (walker or by hand with a tagged PID), export, check the stored values, then delete the test rows by PID prefix.

## Quick reference — the facts that cost the most

| Symptom | Cause | Fix |
|---|---|---|
| Draft looks right, respondents see the bug | `GET /survey-definitions` is the **draft**; builder edits autosave there and go live only on publish | Read the published version via `/versions`; publish after every change |
| Survey went live by itself | Publish flips `isActive` on | Re-check `GET /surveys/{id}` after every publish; deactivate |
| Walker gets "not currently active" right after activation | JFE edge cache | Wait ~45 s after `isActive=true` |
| Question referenced by a live block or branch never renders | It is still a member of the **Trash** block | Remove from Trash `BlockElements` first, then add to a live block |
| `DELETE /questions/{qid}` → 404 | Question is an orphan (in no block) | Put it in Trash, then delete |
| Everyone screened out after a builder session | Rebuilt question got a new QID; the branch still points at the trashed one, and `NotSelected` on a trashed question is always true | Check every branch/DL QID against live questions |
| Branch on embedded data never fires | JS-set ED is not visible to flow evaluation | Branch on question answers; or include the block and let its JS self-hide |
| "A And B Or C" fires wrongly | Flat chains evaluate left to right, no grouping | Nest branches |
| Dependent question on the same page never shows | Same-page display logic is evaluated at page load | Give it its own page (empty pages auto-skip) |
| An answer option is never in the export | `AnalyzeChoices {"id":"No"}` drops the answer from the record | Remove the flag; grep every definition before fielding |
| Matrix columns disagree with choice ids | Matrix rows export by **position** in `ChoiceOrder`; multi-select by choice id | Build a position↔id crosswalk after any row deletion |
| Loop & Merge columns look like item order | Column prefix is the loop **row key**, not presentation order | Record `${lm://CurrentLoopNumber}` to embedded data |
| Classic QuestionJS silently does nothing | New API-created surveys use the "Simple" layout (no `.QuestionOuter`, no `QR~` ids) | Set Skin `templateId "*2014"`, `SkinType "templated"` |
| Document click listener never sees Next | New JFE runtime stops propagation of `#NextButton` | Register in capture phase |
| Automation browser sees a bare "JavaScript is required" shell | `/start` XHR returns `ErrorCode: "AlreadyTaken"` (ballot-box stuffing / duplicate detection, often inherited by copies) | Check the `/start` body first; turn the option off for QA |
| Export tags collide or renumber | Copies and restores reuse tags; "Auto-number questions" renumbers all | Duplicate-tag check on every copied survey; retag by QID |

Full detail, by area: `references/qualtrics-quirks.md`.

## References

- `references/qualtrics-quirks.md` — draft vs published, trash and orphans, tags, flow logic, display logic and pages, timers, Loop & Merge, export conventions, data-loss flags, options, copies and provenance.
- `references/api-patterns.md` — endpoints and payloads: questions, blocks, flow/branch/embedded data, options, copy, publish, export, delete.
- `references/javascript.md` — QuestionJS scope and the JFE runtime, safe DOM patterns, embedded-data telemetry, footer scripts, external logging.
- `references/verification.md` — the recorded-walk standard, what to assert in the export, invariants, test-row hygiene.

## Common mistakes

- Treating a PUT's 200 as applied. Verify with a fresh GET, every time.
- Checking the page instead of the export. Display-only checks passed while a JS-customised dropdown stored nothing for 116 respondents.
- Trusting a column's presence. Declared embedded data always exports; assert **values**.
- Carrying an answer key or a tag map across a survey copy. Copies renumber tags and can swap item text under the same ids.
- Deleting test rows by "blank PID" in a partner-run survey — the partner's testers have blank PIDs too. Delete by your own prefix only.
