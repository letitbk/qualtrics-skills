---
name: survey-persona-audit
description: Use when a survey (Qualtrics or similar) needs systematic pre-fielding validation — every route, gate answer, skip, screen-out and ending exercised by scripted respondents and checked in the exported data — or when a builder session, copy, or wording change needs proof that routing, piping and stored values still behave; also when asked to "simulate respondents", "walk the survey", "test every path", or to have a second agent review survey traces.
---

# Survey Persona Audit

## Overview

Scripted respondents (personas), one per route, are walked through the live survey by a browser walker that records what each one saw and answered. The exported data is then checked cell by cell against the script, and an independent agent re-reads every trace against a written rules file. The export is the arbiter; the page is never the evidence.

Three things make this work: coverage is designed from the **flow**, not from a wish list; every expectation is derived twice (by the checker and by a reviewer that did not write the personas); and every finding is triaged as survey defect, by design, or harness artefact before anyone edits the survey.

## When to use

- Before fielding, after any builder session, copy, restore, or wording pass on a survey with branching, Loop & Merge, piping, custom JS or multiple arms.
- When a reported bug ("I never saw the prompt", "everyone screened out") needs reproduction on a stated path.
- When you need a defensible record of what each kind of respondent saw (IRB, partner hand-off, preregistration).

Not for a single-question change on an otherwise verified survey (one recorded walk per the `qualtrics-survey` skill's verification standard is enough), and not for estimating response distributions (personas are scripts, not samples).

## The pipeline (each step gates the next)

1. **Catalog** the *published* definition: `python3 scripts/catalog.py SID --out DIR --published`. Persona authors and reviewers work from this file and the saved definition, never from memory of the builder.
2. **Design coverage** from the catalog's `flow`: one persona per branch outcome, per answer of every gate (including blank and skipped), decline, screen-out, each ending, each arm, empty and full list inputs, "exactly one" and "none" cases for anything that samples or sweeps, and a skipper that leaves every soft-prompted question blank. Write the coverage table first (`references/persona-authoring.md`).
3. **Author personas** — by subagents, one route each, from the catalog and the coverage row, using the brief in `references/persona-authoring.md`. Derive variants from one fully specified base persona so only route-deciding answers change.
4. **Validate** ids and shapes before any browser opens: `python3 scripts/validate_personas.py --catalog … --personas DIR`.
5. **Walk** each persona with capture: `python3 scripts/walk.py SID --catalog … --persona … --pid QA_<stage>_<name> --out walks/<name>.json --capture capture/<name> [--activate]`. Test ids carry a prefix you own. Activate only for the walks and deactivate in a `finally`; walk partner-hosted surveys one at a time and stub the partner host if it must not receive test traffic.
6. **Check the export**: `python3 scripts/check.py SID --definition … --personas DIR --walks DIR --out checks.json`. Route vs flow evaluator, every intended answer in its cell, skipped and off-path questions blank, loop items, soft prompts, walker errors. Project-specific derived fields go in an `--extra hooks.py`.
7. **Build traces and review independently**: `python3 scripts/path_logs.py …` then `python3 scripts/make_review_prompts.py --rules rules.md …` and send each prompt to a reviewer that did not write the personas (Codex CLI via `--run codex`, or one Claude subagent per trace, read-only). The rules file states the *agreed* design as of a date; a rule stricter than the design produces false "high" findings.
8. **Triage, fix, re-walk the delta, clean up.** Classify every finding (`references/reviewing-traces.md`), fix survey defects through the build contract of the `qualtrics-survey` skill, re-walk the affected routes, then `python3 scripts/cleanup.py SID --prefix QA_<stage>_ --apply` and record the deleted response ids.

Write the report last: verdict, coverage table, per-path results, findings by class, by-design table, file index (template in `references/reviewing-traces.md`).

## Agent roles

| Role | Gets | Must | Must not |
|---|---|---|---|
| Persona author (subagent, one route) | catalog, coverage row, output path, persona schema | use ids from the catalog, keep answers consistent with the route, state `expected_path` | invent labels, edit other files, assume branch semantics without reading `flow` |
| Walker (script) | persona, catalog, survey id | record every page's visible text, answers and screenshot | be the evidence — the export is |
| Checker (script + hooks) | definition, personas, walks, export | derive expectations from the definition | trust column presence; string-sort permutations |
| Reviewer (other model or fresh subagent) | rules file, trace, persona, definition | cite page and quote; separate defect from artefact; stay read-only | edit the repo, decision log or notes (tell it so explicitly) |
| Second deriver (optional, one per survey) | catalog, raw walk logs, export | re-derive every expected cell independently of the checker | reuse the checker's output as its own |

## Quick reference

| Situation | Do |
|---|---|
| Walk hangs after Next | Hard force-response: the walker aborts with `.ValidationError` text and a screenshot; give the persona an answer or allow that question in the design |
| "Response Requested" modal | Expected on soft-prompted blanks; the walker continues and logs `after: [QID]` |
| Bare "JavaScript is required" page | `/start` returned `AlreadyTaken` (duplicate prevention on) — turn it off for QA, fresh context per walk |
| Row missing from export | Walk did not finish, or partial-data window not closed; read `walks/<name>.json` `end` |
| `answers_ok < answers_checked` | Read the cell diff: harness (walker default, text box auto-selected a choice) vs survey (recode, positional matrix, dropped answer) |
| Block visited but "not expected" | Branch on JS-set embedded data, randomizer, or a flow the evaluator cannot see — verify in the definition before calling it a defect |
| Reviewer marks "high" on a by-design page | Fix the rules file, not the survey; add the case to the by-design table |

## Common mistakes

- Expectations from the wish list instead of the flow (a screened-out respondent never reaches the id page; its blank is correct).
- Reusing an answer key, tag map or persona set across a survey copy: copies renumber tags and can swap item text under the same ids. Re-catalog.
- Letting the walker's defaults stand in for design decisions (a mini-roster relationship defaulted to "spouse" for every walk).
- Reviewer agents that edit project files unless told not to.
- One persona per "type of person" instead of per **route**; routes are what break.
- Declaring victory on the draft: catalog and definition must come from the published version.
- Deleting "blank-id" rows in a partner-run survey.

## References

- `references/persona-authoring.md` — catalog contents, persona schema, coverage design, the subagent brief, custom-widget handlers.
- `references/reviewing-traces.md` — path-log format, rules file, reviewer prompt and dispatch, triage taxonomy, report template.
- `references/qualtrics-walker.md` — the walker's encoded rules, value→DOM mapping, limits, hooks for checks.
