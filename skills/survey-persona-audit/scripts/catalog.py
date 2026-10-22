#!/usr/bin/env python3
"""Question catalog for persona authors and reviewers, generated from the live definition.

    python3 catalog.py SID --out DIR [--published] [--definition saved.json]
    -> DIR/catalog_SID.json   (flow in words + one entry per live question: tag, qid, block, page, type,
                               text, choices, answers, recode, display logic in words, answer_format,
                               loop items, force-response setting)
       DIR/definition_SID.json (the definition the catalog was built from — hand the same file to check/review)

Persona authors write answers keyed by export TAG using the answer_format shown per question; values are
choice/answer IDS, never labels (dropdowns take {"label": "..."}); "SKIP" leaves a question blank.
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _api as A  # noqa: E402


def cond_text(c, Q):
    loc = c.get("LeftOperand", "")
    m = re.search(r"q://(QID\d+)/SelectableChoice/(\d+)(?:/(\d+))?", loc)
    if m:
        q, a, b = m.groups()
        tag = Q.get(q, {}).get("DataExportTag", q)
        return (f"{tag} row {a} = {b}" if b else f"{tag} = {a}") + f" {c.get('Operator')}"
    m = re.search(r"q://(QID\d+)/ChoiceTextEntryValue", loc)
    if m:
        return f"{Q.get(m.group(1), {}).get('DataExportTag', m.group(1))} text {c.get('Operator')}"
    return f"{loc} {c.get('Operator')} {c.get('RightOperand', '')}".strip()


def logic_text(logic, Q):
    c = logic.get("0", {})
    ks = sorted((k for k in c if k != "Type"), key=lambda k: int(k))
    parts = []
    for k in ks:
        conj = c[k].get("Conjuction")
        parts.append((f" {conj} " if conj else "") + cond_text(c[k], Q))
    return "".join(parts)


def flow_text(flow, d, depth=0, out=None):
    out = [] if out is None else out
    Q, B = d["Questions"], d["Blocks"]
    for e in flow:
        t = e.get("Type")
        pad = "  " * depth
        if t in ("Block", "Standard"):
            out.append(pad + f"[{B[e['ID']]['Description']}]")
        elif t == "Branch":
            out.append(pad + "IF " + logic_text(e.get("BranchLogic", {}), Q))
            flow_text(e.get("Flow", []), d, depth + 1, out)
        elif t == "BlockRandomizer":
            out.append(pad + f"RANDOMIZER (show {e.get('SubSet')} of the following)")
            flow_text(e.get("Flow", []), d, depth + 1, out)
        elif t == "EndSurvey":
            o = e.get("Options") or {}
            out.append(pad + f"END ({o.get('SurveyTermination', 'default')}"
                       + (f" -> {o['EOSRedirectURL'][:60]}" if o.get("EOSRedirectURL") else "")
                       + (f", flag {o['ResponseFlag']}" if o.get("ResponseFlag") else "") + ")")
        elif t == "EmbeddedData":
            fields = [f.get("Field") for f in e.get("EmbeddedData", [])]
            out.append(pad + f"EMBEDDED DATA {fields[:6]}{'…' if len(fields) > 6 else ''}")
    return out


def build(d: dict, sid: str) -> dict:
    Q, B = d["Questions"], d["Blocks"]
    live = A.live_qids(d)
    items = []
    for bid in A.flow_block_ids(d["SurveyFlow"]["Flow"]):
        blk = B[bid]
        loop = (blk.get("Options") or {}).get("LoopingOptions") if (blk.get("Options") or {}).get("Looping") else None
        for pi, pg in enumerate(A.pages(blk), 1):
            for q in pg:
                if q not in Q or q not in live:
                    continue
                qq = Q[q]
                t = qq["QuestionType"]
                if t in ("Timing", "Meta"):
                    continue
                it = {"tag": qq["DataExportTag"], "qid": q, "block": blk["Description"], "page": pi,
                      "type": f"{t}/{qq.get('Selector', '')}", "text": A.clean(qq.get("QuestionText"))[:500]}
                if t == "DB":
                    it["note"] = "text only, no answer"
                    items.append(it)
                    continue
                v = qq.get("Validation")
                st = (v.get("Settings") if isinstance(v, dict) else None) or {}
                it["force"] = st.get("ForceResponse") if isinstance(st, dict) else None
                if qq.get("Choices"):
                    order = [str(c) for c in (qq.get("ChoiceOrder") or list(qq["Choices"]))]
                    it["choices"] = {c: A.clean(qq["Choices"][c].get("Display")) for c in order if c in qq["Choices"]}
                    te = [c for c in order if str(qq["Choices"].get(c, {}).get("TextEntry", "")).lower() == "true"]
                    if te:
                        it["text_entry_choices"] = te
                if qq.get("Answers"):
                    aorder = [str(a) for a in (qq.get("AnswerOrder") or list(qq["Answers"]))]
                    it["answers"] = {a: A.clean(qq["Answers"][a].get("Display")) for a in aorder if a in qq["Answers"]}
                if qq.get("RecodeValues"):
                    it["recode"] = qq["RecodeValues"]
                if qq.get("DisplayLogic"):
                    it["shown_only_if"] = logic_text(qq["DisplayLogic"], Q)
                if qq.get("Randomization"):
                    it["randomized"] = True
                sel = qq.get("Selector")
                if t == "MC":
                    it["answer_format"] = ("list of choice ids" if sel in ("MAVR", "MAHR", "MACOL", "MSB")
                                           else '{"label": "exact dropdown label"}' if sel == "DL" else "single choice id (int)")
                elif t == "Matrix":
                    it["answer_format"] = '{"<row id>": <answer id>, "*": <answer id for unlisted rows>}; rows = choices, columns = answers'
                elif t == "TE":
                    it["answer_format"] = "string"
                elif t == "Slider":
                    it["answer_format"] = "number 0.0-1.0 (position on the track)"
                else:
                    it["answer_format"] = "walker default (first option) unless a handler covers this type"
                if loop and isinstance(loop.get("Static"), dict):
                    rows = loop["Static"]
                    it["loop_items"] = {k: A.clean(rows[k].get("1")) for k in sorted(rows, key=lambda x: int(x) if str(x).isdigit() else 0)}
                    it["answer_format"] = '{"<loop row key>": <answer id>, "*": <answer id for the rest>} — one page per loop row'
                items.append(it)
    return {"survey": d.get("SurveyName"), "sid": sid,
            "flow": flow_text(d["SurveyFlow"]["Flow"], d), "questions": items, "n_questions": len(items),
            "notes": ["Values are choice/answer ids, never labels (dropdowns take {\"label\": ...}).",
                      "\"SKIP\" leaves a question blank; unlisted questions get the walker default.",
                      "Multi-select answers are lists; matrices and loops are dicts keyed by row/loop key with \"*\" as the default.",
                      "shown_only_if is question-level display logic; block-level routing is in `flow`."]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sid")
    ap.add_argument("--out", required=True)
    ap.add_argument("--published", action="store_true", help="use the last published version (what respondents see)")
    ap.add_argument("--definition", help="use a saved definition JSON instead of fetching")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    d = A.load_def(a.sid, a.definition, a.published)
    cat = build(d, a.sid)
    (out / f"catalog_{a.sid}.json").write_text(json.dumps(cat, indent=1, ensure_ascii=False))
    (out / f"definition_{a.sid}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False))
    print(f"{cat['n_questions']} questions -> {out / f'catalog_{a.sid}.json'}; definition saved alongside")


if __name__ == "__main__":
    main()
