# Reviewing traces: path logs, rules file, reviewer dispatch, triage, report

Contents: 1 The path log · 2 The rules file · 3 Reviewer prompt and dispatch · 4 Triage · 5 Independent second derivation · 6 Report template

## 1. The path log

`python3 scripts/path_logs.py --definition … --personas DIR --walks DIR --checks checks.json --out DIR/path_logs` writes one Markdown file per persona plus `index.md`:

- header: survey, persona brief, expected path, scripted answers, walk end, screenshot path;
- the automated check: response id, finished/flag, answers matched, loop items, soft prompts, findings, what was OK, walker notes;
- one section per page **in the order shown**: export tags on the page, the exact visible text ("Seen"), what was answered with labels resolved, screenshot name; soft prompts and the end page appear as their own sections.

Capture must be on during the walk (`--capture DIR`) or the "Seen" blocks are empty and the reviewer has nothing to quote.

## 2. The rules file

A short Markdown statement of what the survey is **agreed** to do as of a date: screener and gate semantics with the exact answer texts, routing per gate answer, wording rules (domain phrase, banned names, required examples), answer requirements (what may force, what may prompt), endings per outcome, and a "by design / harness" list (placeholder completion codes, the automation browser being blocked at the partner redirect, modules that intentionally go to everyone).

Two failure modes, both seen:

- **Rules stricter than the design.** "Never-users get no AI questions" when the design sends the literacy items to everyone → every never-user trace gets a false "high". Write rules from the signed design document, and list the known exceptions.
- **Rules silent on the delta.** The reviewer then judges the new page against nothing. Add one line per change in the stage under review.

## 3. Reviewer prompt and dispatch

`python3 scripts/make_review_prompts.py --rules rules.md --logs DIR/path_logs --personas DIR --definition DIR/definition_SID.json --out DIR/reviews` renders one prompt per persona (paths relative to the current directory — run it from the directory the reviewer will be given). The prompt asks for: route expected vs shown; per-page content against the rules and against the respondent's own earlier answers (unfilled piping, typos, duplicated options, wrong-arm wording); recorded data; respondent confusion; a verdict line; a findings table with severity, page, quote, what is wrong, defect vs artefact.

Dispatch options:

- **Codex CLI**, read-only, parallel: add `--run codex --jobs 4` (uses `codex exec -s read-only --ephemeral -o <out>`). One review per trace, ~36 traces in a few minutes.
- **Claude subagents**: one `Agent` per prompt file, general-purpose, with the prompt text as the task and the instruction "analysis only; do not modify any file; write your answer as your final message". Save the final message to `reviews/<persona>.md`.
- **Gemini** via its CLI the same way.

Tell every reviewer explicitly that it must not edit project files: a review subagent given write access has added decision-log entries and notes on its own.

Use a reviewer that did **not** write the personas or the checker. Same-model circularity is acceptable when the context is fresh and the inputs are only the files listed.

## 4. Triage

Every reviewer finding gets one class before anyone edits the survey:

| Class | Meaning | Example | Action |
|---|---|---|---|
| Survey defect — routing | A page shown/missing/out of order relative to the flow or the design | non-user reaches the loop | fix the flow, re-walk that route |
| Survey defect — data loss | An answer or derived field not stored or stored wrongly | "I don't know" never exported (`AnalyzeChoices=No`); label truncated at 40 chars | fix, re-walk, check pilots for the same flag |
| Survey defect — wording | Grammar, piping, duplicated options, wrong-arm phrase | "How often you do the following" | fix in the wording pass; keep tested items stable |
| Survey defect — consistency | Text presumes something the respondent denied | "I prefer my friends not know how much I use AI" shown to a non-user with no N/A option | design decision: add an option, gate, or accept and document |
| By design | Matches the signed design; the rules file was wrong or silent | literacy shown to never-users | fix the rules file; add to the by-design table |
| Harness artefact | Caused by the walker or the test setup | Prolific blocks the headless browser; walker default relationship; placeholder code | fix the walker if it hides real behaviour; otherwise note |
| Persona artefact | The script contradicts itself | AI-only picker after "never used AI" | fix the persona; re-walk only if it changed the route |
| Spec error | The design itself is inconsistent | reminder text asserts an answer a blank checklist never gave | escalate to the researcher; do not "fix" silently |

Severity: blocker (data loss or wrong routing on a fielded path), high (routing/consistency on a common path), medium (wording that misleads), low (typo), note.

## 5. Independent second derivation

For the release audit, add one reviewer per survey that re-derives **every** expected export cell from the catalog and the raw walk logs — not from the checker's output — and compares with the export, including: matrix-by-position, multi-select comma lists, loop columns, dropdown codes, embedded fields written by JS, arm separation (every column of the other arm blank), free text byte-for-byte, no PII columns. It found nothing the checker missed on a clean survey and confirmed the checker's coverage numbers; that confirmation is the point.

## 6. Report template

```
# <Survey set> — simulated-respondent audit, <date>
Surveys and published versions. Method in three sentences (catalog → personas → walks → export check → independent review).

## Verdict
What behaves as designed on every path. Defects found, fixed, verified, published (table: #, defect, where, cause, fix, versions).

## Coverage
Table: survey | personas | routes exercised. Then "Not exercised: …".

## What was checked per walk and found clean
Ending and flags · routing · answers · telemetry · loop · derived fields · arm separation · concurrency.

## Export conventions confirmed (for data prep)

## Reviewer verdicts
Per survey: clean / defects, with the harness artefacts and notes separated.

## Findings by class
1 wording · 2 consistency (questions that presume …) · 3 older phrases · 4 open decisions · 5 by design or caused by the test (table: finding | why it is not a defect).

## Per-persona tables (machine checks, final pass)

## Files
Scripts, personas, walks, checks, path logs, reviews, screenshots (kept out of git), logs.

## Cleanup
Rows deleted (ids), rows kept and why.
```
