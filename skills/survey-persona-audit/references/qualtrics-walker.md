# The Qualtrics walker and checker: encoded rules, mapping, limits, hooks

Contents: 1 Worked run · 2 Rules the walker encodes · 3 Persona value → DOM · 4 Limits · 5 Check hooks · 6 Test ids, activation, cleanup

## 1. Worked run (one survey, one stage)

```bash
S=<this skill folder>/scripts                     # e.g. ~/.claude/skills/survey-persona-audit/scripts
SID=SV_xxxxxxxx; D=audit/s3                       # one directory per stage; keep it in the repo, screenshots out of git
python3 $S/catalog.py $SID --out $D --published    # catalog_<SID>.json + definition_<SID>.json
# … personas written into $D/personas/<name>.json (subagents; see persona-authoring.md) …
python3 $S/validate_personas.py --catalog $D/catalog_$SID.json --personas $D/personas
for p in $D/personas/*.json; do n=$(basename $p .json)
  python3 $S/walk.py $SID --catalog $D/catalog_$SID.json --persona $p --pid QA_s3_$n \
      --query "STUDY_ID=QA&SESSION_ID=QA" --out $D/walks/$n.json --capture $D/capture/$n
done                                                # or 3 in parallel with xargs -P 3; add --activate if inactive
python3 $S/check.py $SID --definition $D/definition_$SID.json --personas $D/personas --walks $D/walks \
    --out $D/checks.json --export-dir $D/exports [--extra $D/hooks.py] [--fields ai_user cell alter_1]
python3 $S/path_logs.py --definition $D/definition_$SID.json --personas $D/personas --walks $D/walks \
    --checks $D/checks.json --out $D/path_logs --capture-root $D/capture
python3 $S/make_review_prompts.py --rules $D/rules.md --logs $D/path_logs --personas $D/personas \
    --definition $D/definition_$SID.json --out $D/reviews [--run codex --jobs 4]
python3 $S/cleanup.py $SID --prefix QA_s3_ --apply   # dry run without --apply
```

A 48-page walk takes about three minutes headless. Three parallel walks show no cross-session bleed; walk partner-hosted surveys one at a time.

## 2. Rules the walker encodes (each one cost a broken walk once)

- Activation → wait 45 s (JFE edge cache) → navigate; `--activate` does this and always deactivates in `finally`.
- The `ConfirmStart` stub is page 0; its button is `#NextButton`; it contains a hidden `#EndOfSurvey`, so the end test requires `#EndOfSurvey` **visible** and no Next button, or a URL starting with a redirect prefix (`--redirect-prefix`, default Prolific app + auth hosts), or an end-text marker (`--end-text`).
- Visible questions = `.QuestionOuter` with `offsetParent !== null`; Loop & Merge ids are `{rowkey}_QIDnn`, selected with `[id="…"]`, and the persona's loop dict is keyed by that row key.
- Radio and checkbox inputs are never `:visible`; the walker clicks the `li`/`td`/`label` wrapper. Matrix rows are `tr:has(input)`, answer by `input[value=…]`. Sliders get a real drag on the handle. Dropdowns select by label, index, or index 1.
- Text boxes attached to a choice are filled only when that choice is **selected** — typing into an unselected "Other" box auto-selects it and changes the multi-select export (seen as `1,4,13` for a scripted `1,4`).
- Timing, descriptive-text and browser-Meta questions are skipped.
- Before scanning a page: poll ~2 s for a `body.Inactive` modal. "Response Requested" → click "Continue Without Answering", log `{note, after: [QID]}`. Any other modal → screenshot and abort with its text.
- After Next: wait until the visible id list **changes** (or end/redirect); "Next absent" alone is not a change, it is the transition. Then re-read until the id list is stable across two reads and differs from the submitted page (the previous page is briefly still shown). No change → capture `.ValidationError` text + screenshot and abort: a hard force-response shows inline text and no modal, and a naive loop hangs for the full timeout.
- Element timeout 8 s, navigation timeout 60 s set separately (`set_default_timeout` also shortens `goto`).
- At the final redirect the execution context is destroyed: the id scan returns `[]` and the end check reads the URL; when there is no Next button and no end yet, the walker waits up to 6 s for the redirect or end page.
- A walk that ends in `ERROR:` still writes its log; read `end` first. The export is the arbiter for whether the response completed.

## 3. Persona value → DOM

| Persona value | Walker action |
|---|---|
| int/str (single choice) | click wrapper of `input[type=radio][id$="~<id>"]` (fallback `[value=<id>]`, then first) |
| list (multi-select) | click each `input[type=checkbox][id$="~<id>"]`; `MISSING` logged for ids not on the page |
| `{"label": …}` (dropdown) | `select_option(label=…)`; on miss, index 1 and `label-miss` logged |
| int (dropdown) | `select_option(index=…)` |
| dict / int (matrix) | per visible row: value by row id, `"*"`, or middle column; unlisted rows without `"*"` stay blank |
| dict (loop) | value by loop row key or `"*"` for the current page |
| str (text) | `fill` into textarea or `input[type=text]` |
| float 0–1 (slider) | drag the handle to that fraction of the track; a hidden value box gets `3.5` if still empty |
| `"SKIP"` | nothing; expect a soft prompt if the question requests a response |
| none | first option / middle column / `text_default` / 0.6 |

Actions are logged as `QID:kind[value]`; `path_logs.py` resolves them to labels.

## 4. Limits (say so in the report)

- The flow evaluator lists **all** children of a `BlockRandomizer` as expected; randomized subsets show as "expected blocks not visited" — confirm by the definition and allow.
- Branches on embedded data set by JavaScript cannot be evaluated (they do not fire in Qualtrics either); branches on URL/flow-set fields use the persona's `embedded` map.
- JS-built widgets need `--handlers`; without them the walker fills what it can see (names into every roster slot), which can trip page guards — script `"SKIP"` or write a handler.
- Two questions on one page where one's display logic reads the other are auto-split by Qualtrics into two screens; the walker handles it, the page count differs from the definition.
- Partial (unfinished) rows are not exported until the partial-data window closes; a crashed walk leaves an invisible row.
- Prolific blocks the headless browser at the completion redirect; the response is already recorded. AmeriSpeak asks for a PIN for a fake panel id. Both are expected ends, not defects; stub the host with `page.route()` when the partner must not receive test hits.

## 5. Check hooks

`check.py --extra hooks.py`:

```python
def extra_checks(defn, persona, row, walk, answers):
    """answers: qid -> persona value. Return (findings, oks) as lists of strings."""
    f, ok = [], []
    codes = {...}                                  # timing QID -> page code, from your build manifest
    visited = {q for e in walk["log"] for i in e.get("qids", []) for q in [i.split("_")[-1]]}
    for q, code in codes.items():
        if q in visited and not row.get(f"ps_{code}"):
            f.append(f"telemetry ps_{code} empty although page visited")
    it = [row.get(f"it_gaid_{i}") for i in range(1, 23)]
    if any(it) and sorted(int(x) for x in it if x) != list(range(1, 23)):   # sort NUMERICALLY
        f.append("it_gaid_* is not a permutation of 1..22")
    for k, nm in enumerate(persona.get("roster", [])[:7], 1):
        if (row.get(f"alter_{k}") or "") != nm:
            f.append(f"alter_{k}={row.get(f'alter_{k}')!r}, persona {nm!r}")
    return f, ok
```

Assert values, never presence: declared embedded fields always export as columns.

## 6. Test ids, activation, cleanup

- `--pid QA_<stage>_<persona>` goes into the URL parameter the survey captures (`--pid-param`, default `PROLIFIC_PID`; for a partner survey use its parameter, e.g. `--pid-param p`). The checker matches export rows by that id; `cleanup.py --prefix QA_<stage>_` deletes exactly those rows and never blank-id rows.
- Turn `BallotBoxStuffingPrevention` / `DetectDuplicateRespondents` off for QA (copies inherit them on) and back on before launch; each walk uses a fresh browser context anyway.
- Activate only for the walks; confirm afterwards in the export that no real respondent started in the window. On a survey that is active for testers, walk without `--activate`.
- Keep `walks/`, `checks.json`, `path_logs/`, `reviews/` in the repo; keep `capture/` (screenshots, tens of MB) and `exports/` (may contain real rows) out of git.
