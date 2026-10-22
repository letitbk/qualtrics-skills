# Persona authoring: catalog, schema, coverage, subagent brief

Contents: 1 The catalog · 2 Persona schema · 3 Designing coverage · 4 Briefing persona-writer subagents · 5 Validation · 6 Custom widgets (handlers)

## 1. The catalog

`python3 scripts/catalog.py SID --out DIR --published` writes `catalog_SID.json` and `definition_SID.json` from the last **published** version (the draft can differ). The catalog has:

- `flow`: the survey flow in words — blocks in order, `IF <tag> = <choice id> Selected And/Or …` for branches (flat chains, evaluated left to right as Qualtrics does), randomizers, `END (…)` elements with redirect and flag.
- `questions`: one entry per live, answerable question in flow order: `tag`, `qid`, `block`, `page`, `type` (`MC/SAVR`, `MC/MAVR`, `MC/DL`, `Matrix/Likert`, `TE/SL`, `Slider/HSLIDER`, …), `text`, `force` (`ON` / `RequestResponse` / `OFF`), `choices` (id → label, in display order), `answers` (matrix columns), `recode`, `shown_only_if` (question display logic in words), `answer_format`, `loop_items` (row key → item text for Loop & Merge), `text_entry_choices`.
- `notes`: the value conventions.

Give authors the catalog, not the raw definition: it is a tenth of the size and states the answer format per question, which is what they get wrong otherwise.

## 2. Persona schema

```json
{
 "name": "fu2_not_domain",
 "brief": "Uses AI at work only; misses the domain box; reminder answer 2.",
 "expected_path": "checklist without AI -> reminder 2 -> non-domain follow-up, trust, patterns; no loop",
 "answers": {
  "Q2": 1, "ELIG": 1,
  "SCR": [1, 6], "SCR_FU": 2,
  "Q21": 6,
  "GRID": {"*": 4, "3": 2},
  "TOPICS": {"1": 3, "*": 1},
  "MAJOR": {"label": "Social Sciences"},
  "GPA": 0.7,
  "OPEN": "I mostly use it for summaries.",
  "ROSTER_1": "SKIP"
 },
 "embedded": {"wave": "1"},
 "text_default": "qa text"
}
```

Value formats (also in the catalog's `answer_format`):

| Question type | Value |
|---|---|
| single choice (`MC/SAVR`, `MC/SAHR`, …) | choice id as int or str |
| multi-select (`MC/MAVR`, `MC/MAHR`, `MC/MACOL`) | list of choice ids |
| dropdown (`MC/DL`) | `{"label": "<exact label>"}` (or an int option index) |
| matrix | `{"<row id>": <answer id>, "*": <answer id for unlisted rows>}` or one int for all rows; rows not listed and no `*` stay blank |
| Loop & Merge question | `{"<loop row key>": <answer id>, "*": <answer id>}` — one page per row |
| text (`TE`) | string |
| slider | number 0.0–1.0 (position on the track) |
| any | `"SKIP"` leaves it blank (expect a soft prompt if `force` is `RequestResponse`; a hard `ON` will stall the walk) |

`embedded` supplies values for branches that test embedded data set by the URL or the flow (the checker's evaluator reads it; the walker does not set it — pass such values in the URL `--query`). Questions not listed get the walker default: first option, middle matrix column, `text_default`, slider 0.6. Keys may be export tags or QIDs.

Keep answers **consistent with the route**: a persona that says "never used AI" and then picks "a generative AI tool" in a later picker produces reviewer findings that are persona artefacts, not defects. Derive variants from one fully specified base persona and change only the route-deciding answers.

## 3. Designing coverage

Build the coverage table from `flow` before writing any persona. One row per:

- every branch condition true and false (each gate answer, including the ones that lead nowhere new);
- every screen-out and consent decline; every `END` element;
- **blank** and **skipped** answers on gates (a skipped checklist is not "none of the above"; a blank reminder page must route somewhere sensible);
- each arm of a multi-arm survey, and one persona that could plausibly belong to both arms;
- list inputs at both extremes: empty roster (and the nudge/re-ask if there is one), full roster;
- "none", "exactly one", "several" for anything that samples, pipes or sweeps (a sweep with no rows should self-advance);
- a skipper that leaves every soft-prompted question blank (tests the prompts and that routing does not depend on them);
- randomizer arms, when the randomizer is real (a hard-coded constant that looks like an arm is worth reading the source);
- the "Other (please specify)" text boxes on the main multi-selects.

Template:

| # | Persona | Route it tests | Gate answers | List inputs | Expected ending |
|---|---|---|---|---|---|
| 1 | ticked_domain | main route, full battery | checklist: domain ticked | roster 5 names | complete redirect |
| 2 | fu2_not_domain | reminder answer 2 | checklist: no; reminder: 2 | 2 names | complete redirect |
| 3 | screener_blank | both gates skipped | blank, blank | blank | complete redirect |
| 4 | decline | consent refused | — | — | screened, return message |

Aim for 8–13 personas per survey. More personas of the same route add nothing; one missing route is where the defect is.

## 4. Briefing persona-writer subagents

Dispatch one subagent per route (or per 2–3 related routes). The brief:

```
You write ONE scripted respondent for a survey test. Output only the JSON file at <path>.

Inputs:
- Catalog: <DIR>/catalog_SID.json — `flow` is the routing; `questions` has, per question, the export tag,
  the answer ids and labels, and `answer_format`. Use tags as keys and ids as values (labels only inside
  {"label": ...} for dropdowns). "SKIP" leaves a question blank.
- Route to cover: <coverage row, verbatim>. Expected ending: <…>.
- Base persona (optional): <path> — copy its answers and change only what the route needs.

Rules:
- Every question on the route gets an answer unless the route is about skipping it.
- Read the branch conditions in `flow` for the route's gates; do not assume what a gate does from its label.
- Matrix and loop answers are dicts with "*" for the rest; multi-selects are lists.
- Names, free text and numbers must be plausible for the brief and internally consistent with earlier answers.
- Write `brief` (one sentence, who this is) and `expected_path` (blocks in order, in words).
- Do not modify any other file. Do not run the survey.
```

Observed reliability: subagents given the catalog with `answer_format` per question get ids and shapes right; their systematic error is branch semantics (e.g., assuming a "why AI" page shows whenever AI was chosen, when the flow shows it only for AI-and-no-person). The validator catches shapes; the checker's routing comparison catches the semantics.

## 5. Validation

`python3 scripts/validate_personas.py --catalog … --personas DIR` reports unknown tags, ids not in the catalog, wrong shapes (list vs dict vs scalar), dropdown labels not found, bad loop keys, slider range. It cannot judge consistency between answers — that is what the reviewer's "contradicts what this respondent said earlier" check is for.

## 6. Custom widgets (handlers)

JS-built inputs (rosters that move dropdowns next to name boxes, pickers with piped labels, sortable lists) need a handler module passed to the walker with `--handlers my_handlers.py`:

```python
# my_handlers.py — called for every visible question before the default answering
def answer(walk, dom_id, qid, tag, outer, value):
    """Return a list of action strings to log, or None to fall through to the default."""
    if tag == "ROSTER_1":
        names = walk.persona.get("roster", [])
        rels = walk.persona.get("roster_rel", [])
        texts = walk.page.locator(".QuestionOuter input[type=text]")
        selects = walk.page.locator(".QuestionOuter select")
        acts = []
        for k, nm in enumerate(names):
            texts.nth(k).fill(nm); acts.append(f"roster{k+1}={nm}")
            if k < len(rels):
                selects.nth(k).select_option(label=rels[k]); acts.append(f"rel{k+1}={rels[k]}")
        return acts
    if tag.startswith("ROSTER_") or tag.startswith("REL_"):
        return []          # handled by the first slot
    return None
```

Store such answers in the persona under your own keys (`roster`, `roster_rel`) and check them in the export with a `check.py --extra hooks.py` module (`extra_checks(defn, persona, row, walk, answers) -> (findings, oks)`), because the engine does not record JS-relocated inputs — their data of record is embedded data.
