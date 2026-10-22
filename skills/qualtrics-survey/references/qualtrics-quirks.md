# Qualtrics quirks, by area

Every entry here was learned by fielding or by a broken test response. Each one names the observable symptom, the cause, and what to do instead. Where a behaviour was seen in one account only, it says so.

Contents: 1 Draft vs published · 2 Publish and activation · 3 Writes and verification · 4 Trash, orphans, deletion · 5 Export tags · 6 Survey flow logic · 7 Display logic and pages · 8 Timing questions · 9 Loop & Merge · 10 Validation and data-loss flags · 11 Export conventions · 12 Options and layout · 13 Copies, provenance, accounts

## 1. Draft vs published

- `GET /survey-definitions/{sid}` returns the **draft**. Respondents get the last **published** version. Builder edits autosave into the draft and only go live on Publish, so a bug can sit in the published version while the draft looks fixed.
- Published definition: `GET /survey-definitions/{sid}/versions` lists versions (`elements[].metadata.published`, `publishEvents`); `GET /survey-definitions/{sid}/versions/{versionID}` returns that definition under the same keys. For some surveys the list comes back empty; the version number is always in the publish response.
- `lastModifiedDate` moves on publish and on activation too; a changed timestamp with an empty diff is normal.
- Resumed respondent sessions keep the definition they started with. Only new sessions get a new version.

## 2. Publish and activation

- Nothing you change by API is served until `POST /survey-definitions/{sid}/versions {"Description": "...", "Published": true}`. This applies to questions, flow, options (header/footer), everything.
- Publishing can set `isActive = true` on a survey that was inactive (reproducible on some surveys, not others). After every publish: `GET /surveys/{sid}`, and `PUT /surveys/{sid} {"isActive": false}` if it flipped. On a survey that is already active, publishing leaves it active.
- After `PUT isActive: true` the JFE serves "this survey is not currently active" for roughly 30–45 s (edge cache). Wait before opening it.
- `GET /surveys/{sid}` → `responseCounts.auditable` does not match the export. Export to know how many rows there are.
- Preview links for an inactive survey redirect to the login page unless the viewer is signed in. Not a build failure.

## 3. Writes and verification

- Question PUT needs the whole object (`QuestionText`, `QuestionType`, `Selector`, `Choices`, `ChoiceOrder`, `Validation`, …). Partial → 400.
- `"QuestionJS": null` is rejected. To delete JS, PUT the object with the `QuestionJS` key **omitted**. To keep JS, carry the fetched value through; forgetting it deletes the script silently. `QuestionJS` can be `false` when empty; read it as `q.get("QuestionJS") or ""`.
- Options PUT needs the whole options object (`BackButton` and friends are required).
- Flow PUT: `Properties.Count` must cover the highest FlowID index. The second and later conditions of a branch use the key spelled `"Conjuction"`. URL-captured embedded fields are `Type: "Recipient"` with no `Value`; constants and defaults are `Type: "Custom"` with `Value` (`Type: "Recipient"` strips a default value).
- A flow PUT that sets an EndSurvey element to `{"SurveyTermination": "DefaultMessage"}` returns 200 and keeps the old `DisplayMessage`/`EOSMessage`. To replace a library end message, POST a new message to the library and repoint `EOSMessage`. Verify every flow edit by a fresh GET.
- After a flow PUT that omits a live block, Qualtrics silently appends that block to the **end** of the flow. New blocks (POST /blocks) are also auto-appended to the flow end. Fetch the flow after all block POSTs, then PUT the flow you mean.
- `POST /blocks` rejects `BlockElements`: create an empty block, then PUT it with elements. New blocks need `Options {BlockLocking, RandomizeQuestions, BlockVisibility}` matching the working blocks or they behave oddly.
- POSTing questions into a block with advanced randomization appends their QIDs to the end of `FixedOrder`; rewrite `FixedOrder` (including the `{~SubSet~}` marker) after adding and check the page-break intent by fresh GET.
- Question updates return 200 before propagation. Verify by fetching the question (and, for fielded surveys, by a new respondent session), not by the status code.
- Snapshot the full draft to disk before every write session. Collaborators edit in the builder between sessions; diff the fresh draft against the last snapshot before touching anything.

## 4. Trash, orphans, deletion

- A question that is a member of the **Trash** block ("Trash / Unused Questions", `Type: "Trash"`) never renders, however many live blocks or branches reference it — a question can be in two blocks at once via the API. To restore a pruned question: remove it from the Trash block's `BlockElements` **first**, then add it to a live block.
- `DELETE /survey-definitions/{sid}/blocks/{bid}` removes the block and its flow reference but **orphans** the member questions (still in `Questions`, in no block, invisible in the builder). Orphans still carry tags, JS and display logic.
- `DELETE /survey-definitions/{sid}/questions/{qid}` returns **404 for an orphan** even though GET returns it. Put the orphan into the Trash block (PUT its `BlockElements`), then DELETE.
- To drop a block completely: rescue any keeper question into another block, delete the block, trash-then-delete each remaining member, verify with a fresh definition.
- "Cut" items are usually still in the survey: orphaned or trashed, including whole modules. Trashed copies of a module can be byte-identical to a live copy elsewhere (md5-compare texts, choices, JS) — restoring is then move-and-untrash, not rebuild — but they can also predate later fixes, so diff before trusting them.

## 5. Export tags (DataExportTag)

- Duplicate tags among live questions are common after copying blocks or surveys and after restoring pruned questions (a restored question comes back with a tag that another question has since taken). Columns may not collide literally (multi-select suffixes differ) but the families read as one question. Run a duplicate-tag check on every copied or edited survey and retag (`Q19b` style).
- "Auto-number questions" in the builder renumbers **every** tag. Restore tags from the last good snapshot by QID. Trashed questions can hold the tag you want to restore; retag them first.
- Copies renumber the tags of shared questions. The QID is the join key across copies; keep a tag crosswalk.
- Timing questions are not in the response codebook (`qualtRics::column_map` has none); map timer columns positionally — flow-ordered Timing QIDs against timer column bases in column order — and verify by adjacency to the content columns.
- Two Timing questions on one definition "page" means two runtime pages.

## 6. Survey flow logic

- Flat `And`/`Or` chains in `BranchLogic` evaluate **left to right with no grouping**: `A And B Or C` = `(A ∧ B) ∨ C`. For "AND of an OR-chain", nest an outer branch (AND conditions) around an inner pure-OR branch. Verified empirically: the flat version fired both arms.
- A branch condition on **embedded data set by JavaScript** is not evaluated against that value: at best the server lags one page behind the client, and in the tested account `EmbeddedField` conditions never evaluated true against JS-set fields, even pages later. Gate on question answers (same-submit answers evaluate correctly), or always include the block and have its JS self-hide and auto-click Next when there is nothing to show.
- A branch keyed to a deleted question evaluates false forever, silently. After any pruning, sweep branches for references to removed QIDs.
- Rebuilding a question in the builder gives it a **new QID**; branches keep pointing at the old, now trashed, one. `NotSelected` on a trashed question is always true — one builder session screened everyone out. After a builder session, check every branch and display-logic QID against the live question set.
- A hand-built `BlockRandomizer` via flow PUT works (`SubSet`, `EvenPresentation`, children of `Type: "Block"`, loop blocks included). When it "does not work", check for `AlreadyTaken` (§12) before blaming the JSON.
- Flow branch on embedded data works with `LogicType: "EmbeddedField"`, `LeftOperand: "e://Field/x"`, `Operator: EqualTo` — for values set by the URL or by flow elements, not by JS.
- Mixed conditions on one question's display logic — a previous-page answer AND a same-page answer — work.

## 7. Display logic and pages

- Standard (not in-page) display logic can insert a page break; in-page logic has condition and question-type limits. Plan conditional questions as flow branches around whole blocks, or give each conditional question its own page.
- Display logic that reads an answer on the **same page**: with `inPage: false` Qualtrics auto-splits the page into two screens (two timers, two page-submit records); with in-page evaluation the question is evaluated at page load, before the driver is answered, and never shows. Either way, put the dependent question on its own page (empty pages auto-skip).
- A page whose only visible question is hidden by display logic or JS is skipped entirely, even if an invisible Timing question remains — the timer records nothing.
- A carried-forward question (e.g., a race follow-up using `DynamicChoices` + `SelectedChoicesCount`) must have its locator strings rewritten to the new source QID before POST, and belongs on its own page.
- A page built by API can lack a Timing question. After adding or moving pages, audit every flow page for exactly one timer (clone an existing timer with `POST /questions?blockId=` and reposition the block elements).
- A page with only a Timing question ("timer-only page") shows as a blank page to respondents.

## 8. Timing questions and paradata

- Qualtrics page timers drop long tab suspensions inconsistently (some multi-minute absences counted, others dropped). Total `Duration (in seconds)` and embedded-data telemetry keep them.
- A slow page transition is booked to the **next** page's timer, while JS `addOnload`-based telemetry starts at render; a large +/− pair on consecutive pages that cancels is a slow load, not a JS problem.
- `First Click` is 0 whenever `Click Count` is 0, which is frequent on radio pages; "time to first tap" is only trustworthy with your own telemetry.
- `Duration (in seconds)` is one second below `EndDate − StartDate` (whole-second timestamps).
- The privacy-minimal alternative to an external logger: per-page counts and timestamps written to embedded data by JS on each page's Timing question (see `javascript.md`).

## 9. Loop & Merge

- Static loop rows live in `Blocks[b].Options.LoopingOptions.Static` (not under a `LoopAndMerge` key). Row **keys** need not be contiguous (a copy can be keyed 1, 3, 5, …).
- Export columns are `{rowkey}_{tag}` (e.g., `7_Q15`). The prefix is the loop row key, i.e., item identity, **not** presentation position. Loop & Merge does not export display order.
- `${lm://CurrentLoopNumber}` **is** the presentation position and is resolved inside QuestionJS and piped text; write `it_<pos>` = row field to embedded data from the loop page's JS to recover order. Declare the fields in the flow first.
- Renumbering the `Static` map rewrites the whole map; verify the item texts after the PUT.
- Columns starting with a digit (`1_Q15`) break default column-name cleaning in R (`read.csv(check.names = FALSE)`).
- Loop pages carry the row key in the DOM id (`{iter}_QIDnnn`); CSS `#`-selectors fail on the leading digit — use `[id="..."]`.

## 10. Validation and data-loss flags

- `AnalyzeChoices: {"<id>": "No"}` ("exclude from analysis" on an answer or choice) makes Qualtrics **drop that answer from the response record**, not just from reports. A literacy "I don't know" was never stored across three fielded waves because of it. Grep every definition for `AnalyzeChoices` with a `"No"` value before fielding.
- `ForceResponse: "ON"` blocks Next with inline validation text and no modal. `"RequestResponse"` shows a "Response Requested … Continue Without Answering" modal. `"OFF"` is silent.
- Rows a script hides in a matrix still count as unanswered for a request/force prompt. Turn the prompt off on questions whose rows are hidden by JS.
- Inputs that a script moves out of their `.QuestionOuter` are invisible to the engine: no export column value, and a request/force on them prompts every respondent. Store those answers to embedded data on page submit instead, and keep the prompt off.
- Posting a copied question payload (from another survey's GET) can carry `RequestResponse` on everything; check `Validation` after bulk copies.
- Code sets are often **not** in scale order (e.g., codes 1, 10–14 for a frequency item, "Never" = 11 after "a few times a month" = 12). Never average raw codes; recode explicitly from the definition's `Choices`.

## 11. Export conventions (CSV, numeric values, `breakoutSets: false`)

- Rows 2–3 of the CSV are label and `ImportId` header rows; drop them.
- Multi-select: one column, selected choice ids comma-joined in choice order (`Q10 = "1,4,5,9"`). With breakout sets, columns are keyed by **choice id** (`Q9_1, Q9_4`).
- Matrix rows export by **position** in `ChoiceOrder` (`Q65_1..4` for row ids 1, 6, 7, 12). Deleting rows makes ids non-contiguous while columns stay positional; any JS that keys embedded data by choice id then disagrees with the columns. Build a position↔id↔label crosswalk from a real response.
- Randomized matrices also export `_DO_*` display-order columns. Loop & Merge does not.
- `RecodeValues` apply to matrix answer ids and to choices.
- Dropdowns export codes; labels only exist in the definition.
- Free text round-trips verbatim (apostrophes, no truncation).
- Declared embedded-data fields always export as columns, filled or not. Presence of a column proves nothing; check values.
- Multi-select "Other" text lands in `{tag}_{id}_TEXT`.
- Partial responses export only after the partial-data window closes; a QA walk that did not finish may not be visible for days.
- Sub-second truncation: `Duration` vs timestamps differ by 1 s.

## 12. Options and layout

- New API-created surveys default to the **"Simple" layout** (Skin `templateId "*simple"`, `SkinType "component"`), whose DOM has no `.QuestionOuter`, no `QR~QID` ids, no `li` choices — all classic QuestionJS silently does nothing. PUT options `Skin {brandingId <your brand>, templateId "*2014"}` and `SkinType "templated"` to match a fielded survey.
- Option value types are mixed in one payload: `BallotBoxStuffingPrevention` and `RecaptchaV3` are strings (`"false"`), `DetectDuplicateRespondents` is a boolean.
- `BallotBoxStuffingPrevention` / `DetectDuplicateRespondents` (copies inherit them ON) refuse an automation browser that has completed the survey before: the `/start` XHR body says `ErrorCode: "AlreadyTaken"` and the page is a bare "JavaScript is required" shell. The survey is fine. Check the `/start` body before any other debugging; `curl`-ing `/jfe/form` and grepping for `Q_CFG` is not a validity test.
- `ConfirmStart: true` shows a "Page Loaded / Click the button to continue" stub whose button is the ordinary `#NextButton`; it contains a hidden `#EndOfSurvey` div. Treat it as page 0.
- Header/Footer strip inline `<script>`; only `<script src="…">` survives. Use the Footer. Publish after changing it.
- Anonymize responses ON removes IP/location columns from the export (confirm in a test export).
- The stale-session dialog and every other Qualtrics dialog set `body.Inactive`; read the dialog text before diagnosing.

## 13. Copies, provenance, accounts

- Copy a survey by API: `POST /surveys` with headers `X-COPY-SOURCE: <sid>` and `X-COPY-DESTINATION-OWNER: <userId>` and body `{"projectName": "..."}`; readable about 5 s later. The copy keeps QIDs, tags, block ids, FlowIDs, JS, loop rows and options.
- The API token's user can differ from the builder login that owns the surveys. A copy owned by the other account is 403 for the token; copy to the token's user, then `POST /surveys/{sid}/permissions/collaborations` to share.
- Provenance of an unknown copy: compare **block-ID sets** with candidate parents (block ids survive copying), then diff live question sets and texts to separate "copy" from "copy + edits". Copy chains explain wrong-domain wording that survived in a child.
- An answer key or item map does not carry across a copy: items get swapped or reworded under the same choice ids. Re-derive from the fielded definition; impossible-looking correct rates are the tell.
- The response API's "versions" endpoint wraps elements in a `metadata` key, not flat fields.
- A survey copy can arrive with the parent's test rows and settings (redirects, consent text, duplicate prevention). Review the delta against the verified parent snapshot first; a clean question-level diff means prior walk verification carries over and the review reduces to the delta.
