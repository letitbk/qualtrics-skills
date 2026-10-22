#!/usr/bin/env python3
"""Compare each walked persona with the exported response: ending, routing vs the flow, every intended
answer in its export cell, skipped and off-path questions blank, loop items, walker errors, soft prompts.

    python3 check.py SID --catalog DIR/catalog_SID.json --definition DIR/definition_SID.json \
        --personas DIR --walks DIR --out DIR/checks.json [--export saved.csv | --export-dir DIR] \
        [--pid-field PROLIFIC_PID] [--extra hooks.py] [--fields ai_user cell alter_1 ...]

Rows are matched by the pid recorded in each walk file (walk.py --pid). Exit 1 if any persona has findings.
Hooks (optional): a Python file with `def extra_checks(defn, persona, row, walk, answers) -> (findings, oks)`
for project-specific embedded data (derived fields, telemetry, JS-written values).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _api as A  # noqa: E402


# ------------------------------------------------------------------ flow evaluation
def eval_cond(c, ans, ed):
    lo = c.get("LeftOperand", "")
    m = re.search(r"e://Field/(\w+)", lo)
    if m:
        v, want, op = str(ed.get(m.group(1), "")), str(c.get("RightOperand", "")), c.get("Operator")
        return {"EqualTo": v == want, "NotEqualTo": v != want, "Empty": v == "", "NotEmpty": v != ""}.get(op, None)
    t = re.search(r"q://(QID\d+)/ChoiceTextEntryValue$", lo)
    if t:
        v = ans.get(t.group(1))
        empty = not (isinstance(v, str) and v.strip()) or v == "SKIP"
        return empty if c.get("Operator") == "Empty" else not empty
    m = re.search(r"q://(QID\d+)/SelectableChoice/(\d+)(?:/(\d+))?", lo)
    if not m:
        return None
    q, a, b = m.groups()
    v = ans.get(q)
    if b:
        sel = isinstance(v, dict) and str(v.get(a, v.get("*"))) == b
    elif isinstance(v, list):
        sel = a in [str(x) for x in v]
    elif isinstance(v, dict):
        sel = False
    else:
        sel = v is not None and v != "SKIP" and str(v) == a
    return sel if c.get("Operator") == "Selected" else (not sel)


def eval_logic(logic, ans, ed) -> bool:
    """Flat And/Or chain, evaluated left to right without grouping — exactly as Qualtrics does."""
    c = logic.get("0", {})
    res = None
    for k in sorted((k for k in c if k != "Type"), key=lambda k: int(k)):
        r = eval_cond(c[k], ans, ed)
        r = False if r is None else r
        conj = c[k].get("Conjuction")
        res = r if res is None else (res and r if conj == "And" else res or r)
    return bool(res)


def expected_blocks(d, ans, ed) -> list[str]:
    B = d["Blocks"]
    out = []

    def rec(flow) -> bool:            # True = an EndSurvey was reached
        for e in flow:
            t = e.get("Type")
            if t in ("Block", "Standard"):
                out.append(B[e["ID"]]["Description"])
            elif t == "Branch":
                if eval_logic(e.get("BranchLogic", {}), ans, ed) and rec(e.get("Flow", [])):
                    return True
            elif t == "BlockRandomizer":
                if rec(e.get("Flow", [])):   # all children listed; the randomizer shows a subset
                    return True
            elif t == "EndSurvey":
                return True
        return False
    rec(d["SurveyFlow"]["Flow"])
    return out


def all_hidden(d, bdesc, ans, ed) -> bool:
    """A block whose every non-timing question is hidden by display logic is skipped by Qualtrics."""
    Q, B = d["Questions"], d["Blocks"]
    bid = next((k for k, v in B.items() if v.get("Description") == bdesc), None)
    if not bid:
        return False
    qs = [e["QuestionID"] for e in B[bid].get("BlockElements", []) if e.get("QuestionID") and Q[e["QuestionID"]]["QuestionType"] != "Timing"]
    return bool(qs) and all(Q[q].get("DisplayLogic") and not eval_logic(Q[q]["DisplayLogic"], ans, ed) for q in qs)


# ------------------------------------------------------------------ expectations
def resolve_answers(d, persona) -> dict:
    tm = A.tag_map(d)
    ans = {}
    for k, v in (persona.get("answers") or {}).items():
        q = k if k.startswith("QID") else tm.get(k)
        if q:
            ans[q] = v
    return ans


def expected_cells(d, ans) -> dict:
    """export column -> expected value for every answered question (numeric export, breakoutSets=False)."""
    Q = d["Questions"]
    exp = {}
    for q, v in ans.items():
        if q not in Q or v == "SKIP" or v is None:
            continue
        qq = Q[q]
        tag, t, sel = qq["DataExportTag"], qq["QuestionType"], qq.get("Selector")
        rec = qq.get("RecodeValues") or {}
        code = lambda c: str(rec.get(str(c), c))  # noqa: E731
        if t == "MC" and sel in ("MAVR", "MAHR", "MACOL", "MSB") and isinstance(v, list):
            order = [str(c) for c in (qq.get("ChoiceOrder") or list(qq.get("Choices", {})))]
            ids = [str(c) for c in v]
            exp[tag] = ",".join(code(c) for c in order if c in ids)
        elif t == "MC" and isinstance(v, dict) and "label" in v:
            lab = {A.clean(cc.get("Display")): c for c, cc in qq.get("Choices", {}).items()}
            if v["label"] in lab:
                exp[tag] = code(lab[v["label"]])
        elif t == "MC" and not isinstance(v, (dict, list)):
            exp[tag] = code(v)
        elif t == "Matrix" and isinstance(v, (dict, int)):
            order = [str(c) for c in (qq.get("ChoiceOrder") or list(qq.get("Choices", {})))]
            for pos, r in enumerate(order, 1):        # rows export by POSITION in ChoiceOrder
                a = v.get(str(r), v.get("*")) if isinstance(v, dict) else v
                if a is not None:
                    exp[f"{tag}_{pos}"] = code(a)
        elif t == "TE" and isinstance(v, str):
            exp[tag] = v.strip()
    return exp


def load_hooks(path):
    if not path:
        return None
    spec = importlib.util.spec_from_file_location("check_hooks", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ main check
def check_persona(d, persona, walk, row, hooks=None, fields=()) -> dict:
    Q = d["Questions"]
    qblock = A.qid_block(d)
    ed = dict(persona.get("embedded") or {})
    ans = resolve_answers(d, persona)
    R = {"name": persona.get("name"), "pid": (walk or {}).get("pid"), "findings": [], "ok": []}
    if not walk:
        R["findings"].append("no walk file")
        return R
    R["end"] = walk.get("end")
    if str(walk.get("end", "")).startswith("ERROR"):
        R["findings"].append(f"walk ended in error: {str(walk['end'])[:200]}")
    if row is None:
        R["findings"].append("no exported row for this pid (walk did not finish, or the partial-data window has not closed)")
        return R
    R["response_id"] = row.get("ResponseId")
    R["finished"] = row.get("Finished")
    R["flag"] = row.get("Q_TerminateFlag")
    if fields:                                             # derived/embedded fields reviewers want to see
        R["exported"] = {f: row.get(f, "<no column>") for f in fields}
    if row.get("Finished") != "1":
        R["findings"].append(f"Finished={row.get('Finished')!r}")
    # routing
    exp_blocks = expected_blocks(d, ans, ed)
    visited = []
    shown = set()
    for e in walk.get("log", []):
        for i in e.get("qids", []):
            q = re.search(r"QID\d+", i).group(0)
            shown.add(q)
            b = qblock.get(q)
            if b and (not visited or visited[-1] != b):
                visited.append(b)
    vset, eset = set(visited), set(exp_blocks)
    missing = [b for b in exp_blocks if b not in vset]
    hidden = [b for b in missing if all_hidden(d, b, ans, ed)]
    if hidden:
        R["ok"].append(f"skipped as expected (every question hidden): {hidden}")
    missing = [b for b in missing if b not in hidden]
    extra = [b for b in visited if b not in eset]
    if missing:
        R["findings"].append(f"expected blocks not visited: {missing}")
    if extra:
        R["findings"].append(f"visited blocks not expected by the flow: {extra}")
    if not missing and not extra:
        R["ok"].append(f"routing: {len(visited)} blocks as expected")
    R["blocks_visited"] = visited
    # answers on shown pages
    exp = expected_cells(d, {q: v for q, v in ans.items() if q in shown})
    mism, hit = [], 0
    for col, want in exp.items():
        got = row.get(col)
        if got is None:
            mism.append(f"{col}: column missing")
        elif str(got).strip() != want:
            mism.append(f"{col}: expected {want!r}, exported {got!r}")
        else:
            hit += 1
    R["answers_checked"], R["answers_ok"] = len(exp), hit
    if mism:
        R["findings"].append(f"{len(mism)} answer cell(s) differ: " + "; ".join(mism[:12]))
    # skipped and off-path questions must be blank
    def cols_of(tag):
        pat = re.compile(rf"^{re.escape(tag)}(_\d+|_\d+_TEXT|_TEXT)?$")
        return [c for c in row if pat.match(c)]
    for q, v in ans.items():
        if v == "SKIP" and q in Q:
            filled = [c for c in cols_of(Q[q]["DataExportTag"]) if row.get(c)]
            if filled:
                R["findings"].append(f"{Q[q]['DataExportTag']} skipped but exported {filled[:3]}")
    off = 0
    for q in A.live_qids(d):
        if q in shown or Q[q]["QuestionType"] in ("Timing", "DB", "Meta"):
            continue
        if qblock.get(q) in vset:
            continue                                   # same page family; hidden-by-DL questions handled by routing
        filled = [c for c in cols_of(Q[q]["DataExportTag"]) if row.get(c)]
        if filled:
            R["findings"].append(f"{Q[q]['DataExportTag']} never shown but exported {filled[:3]}")
        else:
            off += 1
    R["ok"].append(f"{off} off-path questions blank")
    # loop blocks
    loop_filled = 0
    for bid in A.flow_block_ids(d["SurveyFlow"]["Flow"]):
        blk = d["Blocks"][bid]
        opts = blk.get("Options") or {}
        if not opts.get("Looping") or not isinstance((opts.get("LoopingOptions") or {}).get("Static"), dict):
            continue
        keys = sorted(opts["LoopingOptions"]["Static"], key=lambda k: int(k) if str(k).isdigit() else 0)
        for e in blk.get("BlockElements", []):
            lq = e.get("QuestionID")
            if not lq or Q[lq]["QuestionType"] in ("Timing", "DB") or lq not in shown:
                continue
            ltag = Q[lq]["DataExportTag"]
            rec = Q[lq].get("RecodeValues") or {}
            v = ans.get(lq)
            n, bad = 0, []
            for k in keys:
                got = row.get(f"{k}_{ltag}")
                if got:
                    n += 1
                if isinstance(v, dict):
                    want = v.get(str(k), v.get("*"))
                    if want is not None and str(rec.get(str(want), want)) != str(got or ""):
                        bad.append(f"{k}_{ltag}: expected {want}, exported {got!r}")
            loop_filled += n
            if bad:
                R["findings"].append(f"loop {ltag}: {len(bad)} item(s) differ: " + "; ".join(bad[:6]))
            R["ok"].append(f"loop {ltag}: {n}/{len(keys)} items filled" + ("" if bad else ", values match"))
    R["loop_filled"] = loop_filled
    # walker notes
    prompts = [e for e in walk.get("log", []) if "note" in e and "prompt" in e["note"]]
    R["soft_prompts"] = [(e.get("after") or [None])[0] for e in prompts]
    errs = [a for e in walk.get("log", []) for a in e.get("acts", []) if ":ERR:" in a or ":MISSING" in a or ":unhandled" in a]
    if errs:
        R["walker_notes"] = errs[:8]
    if hooks is not None:
        f, o = hooks.extra_checks(d, persona, row, walk, ans)
        R["findings"] += list(f or [])
        R["ok"] += list(o or [])
    return R


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sid")
    ap.add_argument("--catalog", required=False)
    ap.add_argument("--definition", required=False, help="saved definition JSON (else fetched: draft or --published)")
    ap.add_argument("--published", action="store_true")
    ap.add_argument("--personas", required=True)
    ap.add_argument("--walks", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--export", default=None, help="an already downloaded numeric CSV export")
    ap.add_argument("--export-dir", default="exports")
    ap.add_argument("--pid-field", default="PROLIFIC_PID")
    ap.add_argument("--extra", default=None)
    ap.add_argument("--fields", nargs="*", default=[], help="export columns (embedded data) to record per persona for the path log")
    a = ap.parse_args()
    d = A.load_def(a.sid, a.definition, a.published)
    rows, path = (A.read_export(a.export), pathlib.Path(a.export)) if a.export else A.export_responses(a.sid, a.export_dir)
    byp = {}
    for r in rows:
        k = r.get(a.pid_field)
        if k:
            byp.setdefault(k, r)
    hooks = load_hooks(a.extra)
    report = {"sid": a.sid, "export": str(path), "n_rows": len(rows), "personas": []}
    pdir = pathlib.Path(a.personas)
    files = sorted(pdir.glob("*.json")) if pdir.is_dir() else [pdir]
    for pf in files:
        persona = json.loads(pf.read_text())
        name = persona.get("name") or pf.stem
        wf = pathlib.Path(a.walks) / f"{name}.json"
        walk = json.loads(wf.read_text()) if wf.exists() else None
        if isinstance(walk, list):
            walk = walk[0]
        row = byp.get(walk.get("pid")) if walk else None
        report["personas"].append(check_persona(d, persona, walk, row, hooks, fields=a.fields))
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    n_bad = 0
    print(f"##### {a.sid}: {len(report['personas'])} personas, export {path.name} ({len(rows)} rows)")
    for R in report["personas"]:
        print(f"  {str(R['name']):28s} end={str(R.get('end'))[:30]:30s} finished={R.get('finished')} flag={R.get('flag')} "
              f"answers {R.get('answers_ok')}/{R.get('answers_checked')} loop={R.get('loop_filled')} prompts={R.get('soft_prompts')}")
        for f in R["findings"]:
            print(f"      ! {f[:300]}")
        n_bad += bool(R["findings"])
    print(f"-> {out}")
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
