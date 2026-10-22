# JavaScript in Qualtrics: scope, runtime, patterns

Contents: 1 Where JS runs · 2 The JFE runtime · 3 State across pages and tabs · 4 Safe DOM patterns · 5 Writing embedded data · 6 Page telemetry to embedded data · 7 Footer scripts and external logging · 8 LLM calls from a survey page

## 1. Where JS runs

- `QuestionJS` is scoped to its question's page: `addOnload` runs when that page renders (on every visit of that page, including after AJAX transitions), `addOnReady` after the DOM is ready, `addOnPageSubmit` when Next is clicked on that page, `addOnUnload` after. A handler registered on page 1 does not exist on page 2.
- Consequence: cross-page instrumentation needs JS on **every** page — attach it to each page's Timing question (a page always has exactly one) — or a Footer `<script src>` that installs once and listens on `document`.
- Qualtrics wraps QuestionJS with `window.Q_CustomJSContextQID = "QIDxx";` before running it.
- Piped text resolves at render time, inside QuestionJS too: `"${e://Field/ResponseID}"` and `"${lm://CurrentLoopNumber}"` are literal strings by the time the script runs; an unset field is `""`.
- Everything in `Header`/`Footer` must be an external `<script src>`; inline scripts are stripped. Changes need a publish and reach new sessions only.
- Test JS on the classic ("templated", `*2014`) layout. The "Simple" layout of new API-created surveys has none of the classic ids and classes.

## 2. The JFE runtime (data-runtime-* engine)

- The runtime **stops propagation** of the `#NextButton` click. A document-level delegated listener in the bubble phase never sees it (radio and label clicks do bubble). Register with `addEventListener(type, fn, true)` (capture phase).
- A programmatic `el.click()` on the transiently disabled Next button is a silent no-op; wait until `!btn.disabled`.
- Any Qualtrics dialog — soft "Response Requested", "session outdated", inactive survey — sets `body.Inactive` and intercepts pointer events until dismissed. Read the dialog text before diagnosing.
- During an AJAX page transition `#NextButton` is briefly absent and the previous page's DOM is briefly still visible.
- Classic ids: question container `.QuestionOuter#QIDnn` (`{iter}_QIDnn` inside a loop); inputs `input[id="QR~QIDnn~choice"]`, matrix `input[name="QR~QIDnn~row"][value="answer"]`; choice list items `li`, matrix cells `td`. Raw inputs are never `:visible`; clicks go to the wrapper.
- Selectors: a substring match on `~1` also matches `~10`, `~11`, … — anchor exactly (`[id$="~1"]`, or split on `~` and compare).
- A native dropdown carries Qualtrics' own placeholder option whose value is `QR~QIDnnn~null` (not `""`). Detect `value === '' || /~null$/.test(value) || !text.trim()` and relabel it; injecting another placeholder produces two.
- Sliders need a real drag on the handle; a click on the track is ignored by the runtime.

## 3. State across pages and tabs

- `sessionStorage` set by QuestionJS survives a **completed** response in the same tab, so a "first visit" flag never resets: every later preview or test run looks like a second visit ("the stem is missing on page 1"). Reset such flags at survey start (consent page `addOnload`) or key them by `${e://Field/ResponseID}`.
- The Qualtrics preview renders the desktop and phone frames in **one tab sharing sessionStorage**; a per-tab flag fires "already seen" in the second frame. Tester screenshots showing one frame trimmed and the other full are this artefact.
- A `setTimeout` that clicks Next survives page changes: a click-then-Next within the delay advances the **next** page unanswered. Cancel it in `addOnPageSubmit` and check `document.contains(container)` before acting.

## 4. Safe DOM patterns

Rewrite matrix row labels from embedded data (after the DOM settles):
```javascript
Qualtrics.SurveyEngine.addOnReady(function () {
  var c = this.getQuestionContainer();
  var items = (Qualtrics.SurveyEngine.getEmbeddedData('block_A') || '').split(',');
  c.querySelectorAll('.ChoiceRow').forEach(function (row, i) {
    var lab = row.querySelector('.LabelWrapper');
    if (lab && items[i]) lab.textContent = Qualtrics.SurveyEngine.getEmbeddedData('item_' + items[i].trim()) || lab.textContent;
  });
});
```

Self-hide a page and auto-advance when a JS-computed condition leaves nothing to show (the flow cannot branch on JS-set data):
```javascript
Qualtrics.SurveyEngine.addOnReady(function () {
  var that = this, rows = that.getQuestionContainer().querySelectorAll('tr.ChoiceRow');
  var keep = JSON.parse(Qualtrics.SurveyEngine.getEmbeddedData('salient_ids') || '[]');
  var shown = 0;
  rows.forEach(function (tr) { var id = tr.id.split('~').pop(); if (keep.indexOf(id) < 0) tr.style.display = 'none'; else shown++; });
  if (!shown) {                                  // nothing to show: hide and advance
    that.getQuestionContainer().style.display = 'none';
    var t = setTimeout(function () { var b = document.querySelector('#NextButton'); if (b && !b.disabled && document.contains(that.getQuestionContainer())) b.click(); }, 300);
    Qualtrics.SurveyEngine.addOnPageSubmit(function () { clearTimeout(t); });
  }
});
```
Turn any request/force prompt off on such a question: hidden rows count as unanswered.

Guard the Next button on a page with custom inputs (JS-moved dropdowns the engine cannot validate):
```javascript
document.addEventListener('click', function (e) {
  var b = document.querySelector('#NextButton');
  if (!b || (e.target !== b && !b.contains(e.target))) return;
  var bad = /* names filled without a relationship */ [];
  if (bad.length) { e.preventDefault(); e.stopImmediatePropagation(); showError(bad); }
}, true);   // capture phase: the runtime stops propagation of this click
```

## 5. Writing embedded data

- Declare every field in the flow's first EmbeddedData element (declared fields always export). `Qualtrics.SurveyEngine.setEmbeddedData('k', v)` (legacy API) exports under the plain field name; read back with `getEmbeddedData`.
- Write in `addOnPageSubmit` (values are final there); a same-page write is visible to client `getEmbeddedData` immediately but never to a flow branch on that submit.
- Inputs your script relocates out of `.QuestionOuter` are not recorded by the engine — write them to embedded data on submit and treat those fields as the data of record. Keep string cuts generous (a 40-character `substring` truncated relationship labels for a whole wave).
- Key JS-written maps by the same ids the export uses, or ship a crosswalk: matrix columns are positional, choice ids are not.

## 6. Page telemetry to embedded data (privacy-minimal paradata)

Per page: attach to the page's Timing question; on load record the open time and install listeners; on page submit write counts only — `ps` page-open epoch, `b`/`bt`/`vt` blur count and epochs, `aw` ms away, `cp` copies, `pa` pastes, `ks` keydowns, `pc` pointer-downs, `ma` mouse-active seconds, `bk` backspaces, `ch` answer changes, `ip`/`ff` text-field inputs/focuses, `tm` a capped event timeline. Nothing leaves Qualtrics; no key values are read. Field names `{prefix}_{pagecode}`; a loop page keys the code by `${lm://CurrentLoopNumber}` (`ps_gaid_${lm://CurrentLoopNumber}`) and adds `it_gaid_<pos>` = the loop row field, which recovers Loop & Merge presentation order. Declare all fields (18 pages × 15 prefixes + 22 loop pages × 16 is fine; 2,000+ fields have worked). Keep an invariant: every declared `ps_*` code has a live carrier question and vice versa.

`ps_*` set for a page the walker never saw is the tell for a self-advancing page; `ps_*` empty for a visited page means the carrier JS is missing.

## 7. Footer scripts and external logging

Architecture that works: `Footer` = `<script src="https://host/logger.js?rid=${e://Field/ResponseID}"></script>`; the script installs once (`if (window._init) return; window._init = true;`), listens on `document` in **capture phase**, buffers events, and posts one payload per page on the Next click and on `visibilitychange → hidden`.

- `navigator.sendBeacon` returns true and never arrives from Qualtrics pages. Use `fetch(url, {method: 'POST', body, keepalive: true, headers: {'Content-Type': 'text/plain'}})`.
- `text/plain` avoids a CORS preflight; the server parses `await req.text()` then `JSON.parse`. Return `Access-Control-Allow-Origin` as one matched origin. Qualtrics pages send no CSP that blocks the fetch.
- Accept an empty participant id (direct access without the panel redirect has none).
- Per-page payload counts are the health check: many small blobs per response is right; a few large ones means only the exit flush fires (the Next click was never seen — bubble-phase listener).
- The same change-event stream doubles as a recovery net for JS-customised questions the engine failed to record.

## 8. LLM calls from a survey page

Call a proxy you control (never the vendor API with a key in the page): hide `#NextButton`, `fetch` the proxy with a timeout, write the result to embedded data, show Next again; on error write `api_error` and show Next with a fallback. Keep the proxy idempotent per `ResponseID` so a reload does not double-bill.
