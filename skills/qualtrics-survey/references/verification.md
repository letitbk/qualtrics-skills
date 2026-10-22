# Verification: the recorded-walk standard

Contents: 1 The standard · 2 Static invariants · 3 What to assert in the export · 4 Test-row hygiene · 5 Browser walks on the JFE · 6 Copies and reviews

## 1. The standard

A change is verified when a **recorded test response** — taken through the live (or temporarily activated) published survey — has been exported and its stored values inspected against what the change was supposed to produce. Nothing weaker counts:

- Not a visual preview. A build-time "verified live" check that looked at the page passed while the relocated dropdowns stored nothing for every respondent of a pretest.
- Not the API's 200. Flow and option PUTs return 200 without applying.
- Not the draft. Respondents see the published version.
- Not a column's presence. Declared embedded fields always export; assert values.

Scope the walk to what changed when the rest of the survey is already covered by earlier walks (a clean QID-level diff against the verified snapshot means the earlier verification carries over).

For systematic coverage — every branch, every gate answer, blank and skipped answers, decline, and an independent second reader — use the `survey-persona-audit` skill; this file is the minimum for a single change.

## 2. Static invariants (run before any walk)

`python3 scripts/qx.py invariants SV_…` reports, for the draft (add `--published` for the served version):

- every live block referenced exactly once in the flow; no non-Trash block outside the flow;
- no duplicate `DataExportTag` among live questions;
- no question in Trash **and** a live block (never renders);
- no orphan questions (in no block; carry tags/JS/logic, invisible in the builder);
- exactly one Timing question per page, no timer-only page (with `--timers`);
- every QID referenced by a branch or by display logic is live;
- `ForceResponse: "ON"` only on the questions you allow (`--force-ok QID…`);
- no `AnalyzeChoices … "No"` (answer dropped from the data);
- Loop & Merge row keys contiguous from 1 (or the export prefixes will not be 1..n).

Add project-specific invariants next to it (e.g., declared telemetry codes ↔ carrier questions, the gate page precedes the module page).

## 3. What to assert in the export

Per test response, from the numeric export:

- `Finished = 1`, `Progress = 100`, the expected `Q_TerminateFlag` (Complete / Screened) and the expected ending (redirect URL vs end-of-survey message).
- Routing: the set of blocks visited (from the walk log or from page telemetry) equals what the flow implies for the answers given.
- Every intended answer in its cell: single choice code, multi-select comma list, matrix `{tag}_{position}`, loop `{rowkey}_{tag}`, dropdown code, text verbatim.
- Skipped questions blank; questions off the path blank; the other arm's columns blank in a multi-arm survey.
- Embedded data derived by JS equals what the answers imply (ids, labels, counts); permutation fields are permutations (sort numerically — a string sort of 1..22 gives a false failure).
- Page telemetry non-empty for visited pages, empty for unvisited ones (a self-advancing page writes telemetry without being seen — that is expected).
- Nothing personally identifying leaked into unexpected columns; no IP/location columns when anonymized.

Expectations follow the **flow**, not the wish list: a respondent screened out on page 1 legitimately has an empty participant-id question if that question sits after the screener, while the URL-captured field is present.

## 4. Test-row hygiene

- Every test response carries a recognisable id in the URL parameter the survey captures (`?PROLIFIC_PID=QA_<stage>_<persona>`); for a partner-hosted survey, put the tag in whatever id parameter the partner uses (`?p=QA_…`).
- Delete test rows by **your own prefix**. Never delete "rows with a blank id" in a survey the partner also tests — their tester rows have blank ids too. Never delete anything in a survey that is fielding.
- Partial (unfinished) test rows appear in the export only after the partial-data window; a walk that crashed leaves a row you cannot see yet.
- Log the response ids you delete.
- Activate only for the walk and deactivate in a `finally`; confirm in the export that no real respondent started in the window.

## 5. Browser walks on the JFE (Playwright)

The tested recipes; the full walker is `survey-persona-audit/scripts/walk.py`.

- Activation → wait 45 s → navigate. Fresh browser context per walk; for a same-tab second run clear JS-visible cookies and add `?fresh=N` (httpOnly session cookies cannot be cleared from the page).
- `ConfirmStart` stub = page 0; its button is `#NextButton`; it contains a hidden `#EndOfSurvey`. End detection needs `#EndOfSurvey` **visible** and `#NextButton` absent, or the redirect URL.
- Visible questions: `.QuestionOuter` elements with `offsetParent !== null`. Loop pages use ids `{iter}_QIDnn`; select with `[id="…"]`, not `#…`.
- Click the wrapper (`li`, `td`, `label`) of a radio/checkbox; wait for inputs with `state="attached"`, they are never `:visible`. Matrix rows: `tr:has(input[type=radio])`, answer `input[value="k"]`. Sliders: mouse-down on the handle, move, up.
- Before scanning a page: poll up to ~2 s for a `body.Inactive` modal. "Response Requested" → click "Continue Without Answering" and log it. Any other modal → screenshot and abort with its text.
- After clicking Next: wait until the visible question-id list **changes**, or `#EndOfSurvey` is visible with no Next, or the URL is a redirect. "Next button absent" is true during the transition and must not count. Then re-read until the id list is stable across two reads (the previous page is briefly still shown). If ids do not change: capture `.ValidationError` text and a screenshot, abort — a hard ForceResponse shows inline text, no modal, and a naive loop hangs for the full timeout.
- Set the element timeout short (≈8 s) and the navigation timeout separately (`set_default_navigation_timeout`); `set_default_timeout` also shortens `goto`.
- At the final redirect the page context is destroyed; treat "execution context was destroyed" in the scan as "no questions" and let the end check read the URL. Stub partner hosts with `page.route()` when the partner must not receive test traffic; the final URL still carries the piped query.
- Automation browser sees a bare shell → read the `/start` XHR body (`AlreadyTaken` = duplicate prevention on; not a survey bug).
- Parallel headless walks (3 at a time) show no cross-session bleed; buffered stdout from process pools appears only at exit.

## 6. Copies and reviews

For a copy of a verified survey: diff the full definition against the verified parent snapshot first. Identical questions ⇒ earlier walk verification carries over and the review reduces to the delta plus hand-off context (consent text, redirects, duplicate-prevention settings, stray test rows, export-tag renumbering). Everything in the delta gets a recorded walk.
