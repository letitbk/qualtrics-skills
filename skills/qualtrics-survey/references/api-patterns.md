# API patterns: endpoints and payloads

Base: `https://{QUALTRICS_BASE_URL}/API/v3`, header `X-API-TOKEN`. Everything below is wrapped by `scripts/qx.py` (`api()`, `get_def`, `put_q`, `post_q`, `delete_q`, `put_flow`, `publish`, `export_responses`, …). Rate limit: sleep ~0.15 s between calls; back off on 429.

Contents: 1 Read · 2 Questions · 3 Blocks · 4 Flow, branches, embedded data · 5 Options · 6 Copy · 7 Publish, activate · 8 Responses

## 1. Read

```bash
GET /survey-definitions/{sid}                 # the DRAFT (Questions, Blocks, SurveyFlow, SurveyOptions)
GET /survey-definitions/{sid}/questions/{qid}
GET /survey-definitions/{sid}/flow
GET /survey-definitions/{sid}/options
GET /survey-definitions/{sid}/versions        # published versions (elements[].metadata)
GET /survey-definitions/{sid}/versions/{ver}  # a published definition
GET /surveys/{sid}                            # isActive, name, lastModifiedDate, (unreliable) responseCounts
```

Live questions = questions that are members of a non-Trash block. Use `qx.live_qids(defn)`; the `Questions` dict also holds orphans and trashed questions.

## 2. Questions

Create: `POST /survey-definitions/{sid}/questions?blockId={bid}`. Update: `PUT .../questions/{qid}` with the **full** object. Delete: `DELETE .../questions/{qid}` (404 for orphans — trash first).

Multiple choice, single answer:
```json
{"QuestionText": "…", "QuestionType": "MC", "Selector": "SAVR", "SubSelector": "TX",
 "DataExportTag": "Q_gate",
 "Choices": {"1": {"Display": "Yes"}, "2": {"Display": "No"}}, "ChoiceOrder": [1, 2],
 "Validation": {"Settings": {"ForceResponse": "OFF", "Type": "None"}}, "Language": []}
```
Multi-select: `"Selector": "MAVR"` (same shape; horizontal `MAHR`, columns `MACOL`). Dropdown: `"Selector": "DL"`. A choice with a text box: `"Choices": {"9": {"Display": "Other", "TextEntry": "true"}}`.

Matrix / Likert:
```json
{"QuestionType": "Matrix", "Selector": "Likert", "SubSelector": "SingleAnswer",
 "DataExportTag": "Q_grid",
 "Choices": {"1": {"Display": "Item 1"}, "2": {"Display": "Item 2"}}, "ChoiceOrder": [1, 2],
 "Answers": {"1": {"Display": "Strongly disagree"}, "5": {"Display": "Strongly agree"}}, "AnswerOrder": [1, 5]}
```
Rows are `Choices`, columns are `Answers`; rows export by position in `ChoiceOrder`.

Selector / SubSelector pairs the API accepts (a mismatch is an opaque 400). The Matrix pairs and the RO/CS defaults are taken from the yrvelez/qualtrics-mcp-server helpers, not from fielding here:

| QuestionType | Selector | SubSelector |
|---|---|---|
| Matrix | `Likert`, `Profile` | `SingleAnswer`, `MultipleAnswer`, `DL` (a dropdown per row) |
| Matrix | `Bipolar`, `MaxDiff` | `SingleAnswer` |
| Matrix | `RO` (rank the rows) | `DND`, `TX` |
| Matrix | `TE` (a text box per cell) | `Short`, `Medium`, `Long`, `Essay` |
| Matrix | `CS` (constant sum per row) | `WOTB`, `WTB` |
| TE | `SL`, `ML`, `ESTB` (essay) | not set |
| RO (rank order) | `DND` (drag and drop; the only variant the Simple layout renders), `SB`, `TX` (classic layout only) | not set |
| CS (constant sum) | `VRTL` (text boxes; renders in the Simple layout), `HBAR`, `HSLIDER` (classic layout only) | not set |

Text entry (tag required): `{"QuestionType": "TE", "Selector": "ML", "DataExportTag": "Q_open", "Validation": {…}}` (`SL` single line).

Timing question (one per page):
```json
{"QuestionType": "Timing", "Selector": "PageTimer", "DataExportTag": "T_page",
 "QuestionText": "Timing", "Configuration": {"MinSeconds": "0", "MaxSeconds": "0"}, "Language": []}
```

Descriptive text: `{"QuestionType": "DB", "Selector": "TB", "QuestionText": "<p>…</p>", "DataExportTag": "D_intro"}`.

JS lives in `"QuestionJS": "Qualtrics.SurveyEngine.addOnload(function(){…});"`. To change only the JS: fresh GET → set `QuestionJS` → PUT full object. To remove: PUT with the key omitted. To keep: carry the fetched value.

Display logic on a question (previous-page driver, own page):
```json
"DisplayLogic": {"0": {"0": {"LogicType": "Question", "QuestionID": "QID45", "QuestionIsInLoop": "no",
   "ChoiceLocator": "q://QID45/SelectableChoice/1", "Operator": "Selected",
   "QuestionIDFromLocator": "QID45", "LeftOperand": "q://QID45/SelectableChoice/1",
   "Type": "Expression", "Description": "If QID45 Choice 1 Is Selected"}, "Type": "If"},
 "Type": "BooleanExpression", "inPage": false}
```
Matrix cell locator: `q://QID45/SelectableChoice/{row}/{answer}`. Text-entry emptiness: `q://QID45/ChoiceTextEntryValue` with `Operator: "Empty"|"NotEmpty"`. Second and later conditions carry `"Conjuction": "And"|"Or"`.

Recode exported values: `"RecodeValues": {"8": "1", "9": "2"}` (applies to choices and matrix answers). Never set `"AnalyzeChoices": {"id": "No"}` unless you mean to drop that answer from the data.

## 3. Blocks

```bash
GET  /survey-definitions/{sid}/blocks/{bid}
POST /survey-definitions/{sid}/blocks            # body WITHOUT BlockElements; then PUT
PUT  /survey-definitions/{sid}/blocks/{bid}      # full object: Type, Description, BlockElements, Options
DELETE /survey-definitions/{sid}/blocks/{bid}    # orphans the questions
```
```json
{"Type": "Standard", "Description": "AI use basic",
 "Options": {"BlockLocking": "false", "RandomizeQuestions": "false", "BlockVisibility": "Expanded"},
 "BlockElements": [{"Type": "Question", "QuestionID": "QID1"}, {"Type": "Question", "QuestionID": "T1"},
                   {"Type": "Page Break"}, {"Type": "Question", "QuestionID": "QID2"}]}
```
The Trash block has `"Type": "Trash"`; untrash by removing the element from its `BlockElements`. Loop & Merge rows: `Options.LoopingOptions.Static = {"1": {"1": "item text", "2": "1"}, …}` with `Options.Looping: true`.

## 4. Flow, branches, embedded data

`GET /flow` → `{"Type": "Root", "FlowID": "FL_1", "Flow": [...], "Properties": {"Count": N}}`. PUT the same object back with `Properties.Count` ≥ the largest FlowID index you use.

Elements:
```json
{"Type": "EmbeddedData", "FlowID": "FL_2", "EmbeddedData": [
   {"Description": "PROLIFIC_PID", "Type": "Recipient", "Field": "PROLIFIC_PID", "VariableType": "String"},
   {"Description": "wave", "Type": "Custom", "Field": "wave", "VariableType": "String", "Value": "1"}]}

{"Type": "Block", "FlowID": "FL_3", "ID": "BL_abc"}
{"Type": "Standard", "FlowID": "FL_4", "ID": "BL_def"}

{"Type": "Branch", "FlowID": "FL_5", "Description": "AI users",
 "BranchLogic": {"0": {"0": {"LogicType": "Question", "QuestionID": "QID56", "QuestionIsInLoop": "no",
     "ChoiceLocator": "q://QID56/SelectableChoice/1", "Operator": "Selected",
     "QuestionIDFromLocator": "QID56", "LeftOperand": "q://QID56/SelectableChoice/1", "Type": "Expression"},
   "1": {"LogicType": "EmbeddedField", "LeftOperand": "e://Field/wave", "Operator": "EqualTo",
     "RightOperand": "1", "Type": "Expression", "Conjuction": "And"},
   "Type": "If"}, "Type": "BooleanExpression"},
 "Flow": [{"Type": "Block", "FlowID": "FL_6", "ID": "BL_ghi"}]}

{"Type": "EndSurvey", "FlowID": "FL_7", "Options": {"Advanced": "true", "SurveyTermination": "Redirect",
   "EOSRedirectURL": "https://app.prolific.com/submissions/complete?cc=${e://Field/completion_code}",
   "ResponseFlag": "Complete"}}

{"Type": "BlockRandomizer", "FlowID": "FL_8", "SubSet": 1, "EvenPresentation": true,
 "Flow": [{"Type": "Block", "FlowID": "FL_9", "ID": "BL_a"}, {"Type": "Block", "FlowID": "FL_10", "ID": "BL_b"}]}
```
Rules: conditions on JS-set embedded data do not fire (branch on answers); flat And/Or chains evaluate left to right (nest for grouping); declare embedded fields in the first flow element, before any block; every live block must appear exactly once.

Edit a nested tree in place instead of re-assembling it: `qx.insert_flow(flow, elem, where, ref)` with `where` = `end` | `beginning` | `before` | `after` | `inside` | `inside_first` and `ref` a FlowID anywhere in the tree (`elem` and any nested children without a FlowID get collision-free ids); `qx.move_flow(flow, fid, where, ref)` keeps the FlowID and refuses a `ref` inside the moved element; `qx.remove_flow(flow, fid)`; `qx.find_flow(flow, fid)` → (parent list, index, element). Then `put_flow` and a fresh `get_flow`.

Screen-out with a partner flag: `"ResponseFlag": "Screened"`; the export column `Q_TerminateFlag` carries it.

## 5. Options

`GET /options` → modify → `PUT /options` with the whole object. Useful keys: `Header`, `Footer` (external `<script src>` only), `BackButton`, `ConfirmStart`, `ProgressBarDisplay`, `PartialData`, `AnonymizeResponse`, `BallotBoxStuffingPrevention` (string), `RecaptchaV3` (string), `DetectDuplicateRespondents` (bool), `Skin`, `SkinType`, `SurveyTermination`, `EOSRedirectURL`.

Layout for classic JS: `"Skin": {"brandingId": <account brand id>, "templateId": "*2014"}, "SkinType": "templated"` — copy the values from a fielded survey in the same account.

## 6. Copy

```python
requests.post(f"{API}/surveys", headers={**HEADERS, "X-COPY-SOURCE": src_sid,
              "X-COPY-DESTINATION-OWNER": owner_user_id}, json={"projectName": "…"})
```
Owner must be the token's user (`UR_…`) or the copy is 403 for the token; share afterwards with `POST /surveys/{sid}/permissions/collaborations`. The copy keeps QIDs, tags, block ids and FlowIDs, which is what makes QID-based diffs against the parent possible.

## 7. Publish, activate, snapshot

```python
r = api("POST", f"/survey-definitions/{sid}/versions", {"Description": "s3 gate fix", "Published": True})
ver = r["metadata"]["versionNumber"]            # sometimes r["versionNumber"]
meta = api("GET", f"/surveys/{sid}")            # re-check isActive: publishing may have flipped it
requests.put(f"{API}/surveys/{sid}", headers=HEADERS, json={"isActive": False})
```
Activation for a walk: `PUT isActive: true` → wait 45 s → walk → `PUT isActive: false` in a `finally`. Snapshot before writes: `GET /survey-definitions/{sid}` → `backups/{sid}/definition_{timestamp}_{label}.json`.

## 8. Responses

Export (numeric codes, no breakout sets):
```python
r = api("POST", f"/surveys/{sid}/export-responses", {"format": "csv", "useLabels": False, "breakoutSets": False})
# poll GET /surveys/{sid}/export-responses/{progressId} until status == "complete" → fileId
# GET /surveys/{sid}/export-responses/{fileId}/file → zip with one CSV; rows 2-3 are header rows
```
Label export (`useLabels: True`) is for humans; analysis uses codes plus the definition.

Delete one response: `DELETE /surveys/{sid}/responses/{rid}` with body `{"decrementQuotas": true}`. Delete test rows by your own PID prefix only; in a partner-run survey, blank-PID rows belong to the partner's testers.

Single response (JSON, includes display-order fields): `GET /surveys/{sid}/responses/{rid}`.
