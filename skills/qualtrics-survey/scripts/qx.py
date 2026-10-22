#!/usr/bin/env python3
"""Qualtrics v3 survey-definitions helpers with the guard rails the qualtrics-survey skill requires.

Credentials: QUALTRICS_API_KEY and QUALTRICS_BASE_URL from the environment, else ~/.Renviron.

Import from a script:   sys.path.insert(0, "<this skill folder>/scripts"); import qx
CLI:
  python3 qx.py snapshot   SID [--label L] [--dir DIR]        # draft definition -> DIR/SID/definition_<ts>_<label>.json
  python3 qx.py published  SID [--dir DIR]                    # last PUBLISHED definition -> disk (what respondents see)
  python3 qx.py diff       SID OLD.json [--published]         # what changed since a snapshot
  python3 qx.py invariants SID [--published] [--timers] [--force-ok QID ...] [--allow TEXT ...]
  python3 qx.py list       SID [--published]                  # blocks in flow order, questions with tag/type/page
  python3 qx.py flow       SID [--published]                  # the flow in words (branches, ends)
  python3 qx.py publish    SID "description" [--keep-active|--deactivate]
  python3 qx.py activate   SID [--wait 45] | deactivate SID
  python3 qx.py export     SID [--dir DIR]                    # numeric CSV export; prints row count and path
  python3 qx.py delete-qa  SID --prefix QA_ [--pid-field PROLIFIC_PID] [--apply]

Rules this module enforces:
  * put_q() never drops QuestionJS unless the caller passes js=DROP (omitting the key is how Qualtrics deletes JS).
  * post_q() requires an export tag (stale tags collide).
  * delete_q() moves an orphan into Trash first (DELETE is 404 for orphans).
  * untrash() removes from the Trash block FIRST (a trashed question never renders).
  * publish() re-checks isActive afterwards (publishing can flip an inactive survey active).
  * set_active(True) waits for the JFE edge cache before returning (default 45 s).
  * delete_by_prefix() only ever deletes rows whose id starts with YOUR prefix.
  * insert_flow()/move_flow() edit a nested flow in place with collision-free FlowIDs; put_flow() raises Properties.Count.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import pathlib
import re
import sys
import time
import zipfile
from collections import Counter
from datetime import datetime

import requests

KEEP = object()   # put_q: leave QuestionJS exactly as fetched
DROP = object()   # put_q: delete QuestionJS (omit the key)


# ------------------------------------------------------------------ auth / API
def _renviron_creds() -> dict:
    creds = {}
    p = pathlib.Path("~/.Renviron").expanduser()
    if p.exists():
        for line in p.read_text().splitlines():
            m = re.match(r"^(QUALTRICS_\w+)\s*=\s*['\"]?([^'\"\s]+)", line)
            if m:
                creds[m.group(1)] = m.group(2).strip()
    return creds


_CREDS = _renviron_creds()
BASE_HOST = (os.environ.get("QUALTRICS_BASE_URL") or _CREDS.get("QUALTRICS_BASE_URL") or "")
BASE_HOST = BASE_HOST.replace("https://", "").rstrip("/")
API_KEY = os.environ.get("QUALTRICS_API_KEY") or _CREDS.get("QUALTRICS_API_KEY") or ""
API = f"https://{BASE_HOST}/API/v3"
HEADERS = {"X-API-TOKEN": API_KEY, "Content-Type": "application/json"}
RATE = 0.15


def _require_creds() -> None:
    if not BASE_HOST or not API_KEY:
        raise SystemExit("QUALTRICS_BASE_URL / QUALTRICS_API_KEY not set (env or ~/.Renviron)")


def api(method: str, path: str, payload=None, raw: bool = False, timeout: int = 120):
    """One API call with retry on 429. Returns the 'result' object (or the Response with raw=True)."""
    _require_creds()
    url = f"{API}{path}"
    for attempt in range(5):
        r = requests.request(method, url, headers=HEADERS, json=payload, timeout=timeout)
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        if not r.ok:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        time.sleep(RATE)
        return r if raw else r.json().get("result")
    raise RuntimeError(f"{method} {path}: rate-limited repeatedly")


# ------------------------------------------------------------------ definitions
def get_def(sid: str) -> dict:
    """The DRAFT definition (what the builder shows). Respondents see the published one."""
    return api("GET", f"/survey-definitions/{sid}")


def get_published_def(sid: str) -> tuple[dict, dict] | None:
    """The last published definition and its version metadata; None if the versions list is empty."""
    vs = api("GET", f"/survey-definitions/{sid}/versions") or {}
    elems = vs.get("elements") or []
    pub = [e for e in elems if (e.get("metadata") or e).get("published")]
    if not pub:
        return None
    latest = max(pub, key=lambda e: int((e.get("metadata") or e).get("versionNumber") or 0))
    meta = latest.get("metadata") or latest
    d = api("GET", f"/survey-definitions/{sid}/versions/{meta.get('versionID') or meta.get('id')}")
    return d, meta


def get_meta(sid: str) -> dict:
    return api("GET", f"/surveys/{sid}")


def get_q(sid: str, qid: str) -> dict:
    return api("GET", f"/survey-definitions/{sid}/questions/{qid}")


def _clean_q_payload(q: dict) -> dict:
    return {k: v for k, v in q.items() if k not in ("QuestionID", "SurveyID")}


def put_q(sid: str, qid: str, mutate=None, js=KEEP) -> dict:
    """Fresh GET -> mutate(q) in place -> PUT the full object.
    js=KEEP carries the fetched QuestionJS; js=DROP omits the key (deletes the JS); a string replaces it."""
    q = get_q(sid, qid)
    if mutate:
        mutate(q)
    payload = _clean_q_payload(q)
    if js is DROP:
        payload.pop("QuestionJS", None)
    elif js is not KEEP:
        payload["QuestionJS"] = js
    elif not payload.get("QuestionJS"):
        payload.pop("QuestionJS", None)       # false/"" -> omit (null is rejected)
    api("PUT", f"/survey-definitions/{sid}/questions/{qid}", payload)
    return get_q(sid, qid)                    # verify by fresh GET


def set_js(sid: str, qid: str, js: str) -> dict:
    return put_q(sid, qid, js=js)


def drop_js(sid: str, qid: str) -> dict:
    return put_q(sid, qid, js=DROP)


def set_force(sid: str, qid: str, mode: str = "RequestResponse") -> dict:
    """mode: 'ON' (hard, inline validation, no modal) | 'RequestResponse' (soft modal) | 'OFF'."""
    def mut(q):
        v = q.get("Validation")
        if not isinstance(v, dict):
            v = {}
        s = v.get("Settings") if isinstance(v.get("Settings"), dict) else {}
        s["ForceResponse"] = mode
        s.setdefault("Type", "None")
        v["Settings"] = s
        q["Validation"] = v
    return put_q(sid, qid, mut)


def retag(sid: str, qid: str, tag: str) -> dict:
    return put_q(sid, qid, lambda q: q.__setitem__("DataExportTag", tag))


def post_q(sid: str, block_id: str, payload: dict, tag: str) -> str:
    """POST a new question into a block. The export tag is mandatory (copied tags collide)."""
    if not tag:
        raise ValueError("post_q: an export tag is required")
    p = dict(payload)
    p["DataExportTag"] = tag
    p.pop("QuestionID", None)
    p.pop("SurveyID", None)
    r = api("POST", f"/survey-definitions/{sid}/questions?blockId={block_id}", p)
    return r["QuestionID"]


def delete_q(sid: str, qid: str) -> bool:
    """DELETE a question; an orphan (in no block) is moved into Trash first because DELETE is 404 for orphans."""
    d = get_def(sid)
    in_block = any(e.get("QuestionID") == qid for b in d["Blocks"].values() for e in b.get("BlockElements", []))
    if not in_block:
        tb = trash_block_id(d)
        if tb:
            blk = d["Blocks"][tb]
            blk.setdefault("BlockElements", []).append({"Type": "Question", "QuestionID": qid})
            put_block(sid, tb, blk)
    r = api("DELETE", f"/survey-definitions/{sid}/questions/{qid}", raw=True)
    return r.ok


def timer_payload(tag: str) -> dict:
    return {"QuestionType": "Timing", "Selector": "PageTimer", "DataExportTag": tag,
            "QuestionText": "Timing", "QuestionDescription": "Timing",
            "Configuration": {"MinSeconds": "0", "MaxSeconds": "0"}, "Language": []}


# ------------------------------------------------------------------ blocks
def get_block(sid: str, bid: str) -> dict:
    return api("GET", f"/survey-definitions/{sid}/blocks/{bid}")


def put_block(sid: str, bid: str, blk: dict) -> None:
    payload = {k: v for k, v in blk.items() if k not in ("ID", "SurveyID")}
    api("PUT", f"/survey-definitions/{sid}/blocks/{bid}", payload)


def create_block(sid: str, desc: str, elements: list[dict], options: dict | None = None) -> str:
    """POST an empty block (BlockElements are rejected on POST), then PUT the elements. Auto-appended to the flow end."""
    opts = options or {"BlockLocking": "false", "RandomizeQuestions": "false", "BlockVisibility": "Expanded"}
    r = api("POST", f"/survey-definitions/{sid}/blocks", {"Type": "Standard", "Description": desc, "Options": opts})
    bid = r["BlockID"]
    blk = get_block(sid, bid)
    blk["BlockElements"] = elements
    blk["Options"] = opts
    put_block(sid, bid, blk)
    return bid


def trash_block_id(defn: dict) -> str | None:
    for bid, b in defn["Blocks"].items():
        if b.get("Type") == "Trash":
            return bid
    return None


def find_block(defn: dict, desc: str) -> str | None:
    for bid, b in defn["Blocks"].items():
        if b.get("Description") == desc and b.get("Type") != "Trash":
            return bid
    return None


def untrash(sid: str, qids: list[str]) -> list[str]:
    """Remove questions from the Trash block FIRST (a trashed question never renders, whatever else references it)."""
    d = get_def(sid)
    tb = trash_block_id(d)
    if not tb:
        return []
    blk = d["Blocks"][tb]
    before = [e for e in blk.get("BlockElements", [])]
    blk["BlockElements"] = [e for e in before if e.get("QuestionID") not in set(qids)]
    removed = [e["QuestionID"] for e in before if e.get("QuestionID") in set(qids)]
    if removed:
        put_block(sid, tb, blk)
    return removed


def live_qids(defn: dict) -> set[str]:
    out = set()
    for b in defn["Blocks"].values():
        if b.get("Type") == "Trash":
            continue
        for e in b.get("BlockElements", []):
            if e.get("Type") == "Question" and e.get("QuestionID"):
                out.add(e["QuestionID"])
    return out


def orphan_qids(defn: dict) -> set[str]:
    in_any = {e.get("QuestionID") for b in defn["Blocks"].values() for e in b.get("BlockElements", []) if e.get("QuestionID")}
    return set(defn["Questions"]) - in_any


def pages(blk: dict) -> list[list[str]]:
    """Question ids per page of a block (split on Page Break elements)."""
    out, cur = [], []
    for e in blk.get("BlockElements", []):
        if e.get("Type") == "Page Break":
            if cur:
                out.append(cur)
            cur = []
        elif e.get("QuestionID"):
            cur.append(e["QuestionID"])
    if cur:
        out.append(cur)
    return out


# ------------------------------------------------------------------ flow
def get_flow(sid: str) -> dict:
    return api("GET", f"/survey-definitions/{sid}/flow")


def put_flow(sid: str, flow: dict) -> None:
    """PUT the whole flow; Properties.Count is raised to cover the highest FlowID index."""
    n = max_flow_id(flow.get("Flow", []))
    flow.setdefault("Properties", {})["Count"] = max(int(flow["Properties"].get("Count", 0) or 0), n + 1)
    api("PUT", f"/survey-definitions/{sid}/flow", flow)


def walk_flow(elems):
    for e in elems:
        yield e
        if isinstance(e.get("Flow"), list):
            yield from walk_flow(e["Flow"])


def max_flow_id(elems) -> int:
    n = 0
    for e in walk_flow(elems):
        m = re.match(r"FL_(\d+)$", str(e.get("FlowID", "")))
        if m:
            n = max(n, int(m.group(1)))
    return n


def flow_block_ids(elems) -> list[str]:
    return [e["ID"] for e in walk_flow(elems) if e.get("Type") in ("Block", "Standard") and e.get("ID")]


def find_flow(flow: dict, fid: str) -> tuple[list, int, dict] | None:
    """(parent list, index, element) of a FlowID anywhere in the nested tree, else None."""
    def rec(elems):
        for i, e in enumerate(elems):
            if e.get("FlowID") == fid:
                return elems, i, e
            if isinstance(e.get("Flow"), list):
                hit = rec(e["Flow"])
                if hit:
                    return hit
        return None
    return rec(flow.get("Flow", []))


def new_flow_id(flow: dict) -> str:
    """Next FlowID above both the highest id in the tree and Properties.Count."""
    n = max(max_flow_id(flow.get("Flow", [])), int((flow.get("Properties") or {}).get("Count", 0) or 0))
    return f"FL_{n + 1}"


_WHERE = ("end", "beginning", "before", "after", "inside", "inside_first")


def _place(flow: dict, elem: dict, where: str, ref: str | None) -> None:
    root = flow.setdefault("Flow", [])
    if where == "end":
        root.append(elem)
    elif where == "beginning":
        root.insert(0, elem)
    else:
        parent, i, target = find_flow(flow, ref)
        if where == "before":
            parent.insert(i, elem)
        elif where == "after":
            parent.insert(i + 1, elem)
        else:
            kids = target.setdefault("Flow", [])
            if not isinstance(kids, list):
                raise ValueError(f"{ref} has a non-list Flow")
            kids.insert(0, elem) if where == "inside_first" else kids.append(elem)


def _check_where(flow: dict, where: str, ref: str | None) -> None:
    if where not in _WHERE:
        raise ValueError(f"where must be one of {'|'.join(_WHERE)}, not {where!r}")
    if where in ("end", "beginning"):
        return
    if not ref:
        raise ValueError(f"ref (a FlowID) is required for where={where!r}")
    if find_flow(flow, ref) is None:
        raise ValueError(f"{ref} is not in the flow")


def insert_flow(flow: dict, elem: dict, where: str = "end", ref: str | None = None) -> str:
    """Insert elem into the flow tree in place and return its FlowID.

    where: end | beginning | before | after | inside | inside_first — the last four relative to ref, a FlowID
    anywhere in the tree (inside = last child of ref). elem and any nested children without a FlowID get fresh,
    collision-free ids; supplied ids must be FL_<n> and unused. Follow with put_flow() (raises Properties.Count)
    and a fresh get_flow()."""
    _check_where(flow, where, ref)
    used = {e.get("FlowID") for e in walk_flow(flow.get("Flow", []))}
    nodes = [elem, *(walk_flow(elem["Flow"]) if isinstance(elem.get("Flow"), list) else [])]
    for node in nodes:                                   # validate supplied ids first
        fid = node.get("FlowID")
        if fid is None:
            continue
        if not re.match(r"FL_\d+$", str(fid)):
            raise ValueError(f"FlowID {fid!r} must look like FL_<number>")
        if fid in used:
            raise ValueError(f"FlowID {fid} already exists in the flow or is repeated in the inserted subtree")
        used.add(fid)
    n = int(new_flow_id(flow)[3:]) - 1
    for node in nodes:                                   # then allocate the missing ones
        if node.get("FlowID") is None:
            n += 1
            while f"FL_{n}" in used:
                n += 1
            node["FlowID"] = f"FL_{n}"
            used.add(node["FlowID"])
    _place(flow, elem, where, ref)
    return elem["FlowID"]


def remove_flow(flow: dict, fid: str) -> dict | None:
    """Detach and return the element with this FlowID (anywhere in the tree), or None if absent."""
    hit = find_flow(flow, fid)
    if hit is None:
        return None
    parent, i, _ = hit
    return parent.pop(i)


def move_flow(flow: dict, fid: str, where: str, ref: str | None = None) -> None:
    """Move an existing element (FlowID kept) to a new position; where/ref as in insert_flow()."""
    hit = find_flow(flow, fid)
    if hit is None:
        raise ValueError(f"{fid} is not in the flow")
    elem = hit[2]
    _check_where(flow, where, ref)
    if ref is not None:
        if ref == fid:
            raise ValueError("cannot place an element relative to itself")
        if isinstance(elem.get("Flow"), list) and any(e.get("FlowID") == ref for e in walk_flow(elem["Flow"])):
            raise ValueError(f"{ref} is inside {fid}")
    remove_flow(flow, fid)
    _place(flow, elem, where, ref)


def ed_declared(flow: dict) -> dict[str, dict]:
    """field -> its EmbeddedData flow element (first declaration wins)."""
    out = {}
    for e in walk_flow(flow.get("Flow", [])):
        if e.get("Type") == "EmbeddedData":
            for f in e.get("EmbeddedData", []):
                out.setdefault(f.get("Field"), e)
    return out


def q_cond(qid: str, choice, op: str = "Selected", conj: str | None = None, row=None) -> dict:
    loc = f"q://{qid}/SelectableChoice/{row}/{choice}" if row is not None else f"q://{qid}/SelectableChoice/{choice}"
    c = {"LogicType": "Question", "QuestionID": qid, "QuestionIsInLoop": "no", "ChoiceLocator": loc,
         "Operator": op, "QuestionIDFromLocator": qid, "LeftOperand": loc, "Type": "Expression"}
    if conj:
        c["Conjuction"] = conj        # Qualtrics' spelling
    return c


def ed_cond(field: str, value: str, op: str = "EqualTo", conj: str | None = None) -> dict:
    c = {"LogicType": "EmbeddedField", "LeftOperand": f"e://Field/{field}", "Operator": op,
         "RightOperand": value, "Type": "Expression"}
    if conj:
        c["Conjuction"] = conj
    return c


def branch(fid: str, conds: list[dict], children: list[dict], desc: str = "") -> dict:
    logic = {str(i): c for i, c in enumerate(conds)}
    logic["Type"] = "If"
    return {"Type": "Branch", "FlowID": fid, "Description": desc,
            "BranchLogic": {"0": logic, "Type": "BooleanExpression"}, "Flow": children}


def block_ref(fid: str, bid: str) -> dict:
    return {"Type": "Standard", "FlowID": fid, "ID": bid}


def ed_field(field: str, value: str | None = None) -> dict:
    if value is None:
        return {"Description": field, "Type": "Recipient", "Field": field, "VariableType": "String"}
    return {"Description": field, "Type": "Custom", "Field": field, "VariableType": "String", "Value": value}


def ed_element(fid: str, fields: list[dict]) -> dict:
    return {"Type": "EmbeddedData", "FlowID": fid, "EmbeddedData": fields}


def end_redirect(fid: str, url: str, flag: str = "Complete") -> dict:
    return {"Type": "EndSurvey", "FlowID": fid, "Options": {"Advanced": "true", "SurveyTermination": "Redirect",
                                                            "EOSRedirectURL": url, "ResponseFlag": flag}}


def flow_text(defn: dict) -> list[str]:
    Q, B = defn["Questions"], defn["Blocks"]

    def cond_text(c):
        loc = c.get("LeftOperand", "")
        m = re.search(r"q://(QID\d+)/SelectableChoice/(\d+)(?:/(\d+))?", loc)
        if m:
            q, a, b = m.groups()
            tag = Q.get(q, {}).get("DataExportTag", q)
            return (f"{tag} row {a} = {b}" if b else f"{tag} = {a}") + f" {c.get('Operator')}"
        return f"{loc} {c.get('Operator')} {c.get('RightOperand', '')}".strip()

    out = []

    def rec(flow, depth):
        for e in flow:
            t = e.get("Type")
            pad = "  " * depth
            if t in ("Block", "Standard"):
                out.append(pad + f"[{B.get(e.get('ID'), {}).get('Description', e.get('ID'))}]")
            elif t == "Branch":
                c = e.get("BranchLogic", {}).get("0", {})
                ks = sorted((k for k in c if k != "Type"), key=lambda k: int(k))
                parts = []
                for k in ks:
                    conj = c[k].get("Conjuction")
                    parts.append((f" {conj} " if conj else "") + cond_text(c[k]))
                out.append(pad + "IF " + "".join(parts))
                rec(e.get("Flow", []), depth + 1)
            elif t == "EmbeddedData":
                out.append(pad + f"ED {[f.get('Field') for f in e.get('EmbeddedData', [])][:8]}{'…' if len(e.get('EmbeddedData', [])) > 8 else ''}")
            elif t == "BlockRandomizer":
                out.append(pad + f"RANDOMIZER subset={e.get('SubSet')} even={e.get('EvenPresentation')}")
                rec(e.get("Flow", []), depth + 1)
            elif t == "EndSurvey":
                o = e.get("Options") or {}
                out.append(pad + f"END ({o.get('SurveyTermination', 'default')}"
                           + (f" -> {o['EOSRedirectURL'][:60]}" if o.get("EOSRedirectURL") else "")
                           + (f", flag {o['ResponseFlag']}" if o.get("ResponseFlag") else "") + ")")
            else:
                out.append(pad + f"{t}")
    rec(defn["SurveyFlow"]["Flow"], 0)
    return out


# ------------------------------------------------------------------ options / publish / activation
def get_opts(sid: str) -> dict:
    return api("GET", f"/survey-definitions/{sid}/options")


def put_opts(sid: str, opts: dict) -> None:
    api("PUT", f"/survey-definitions/{sid}/options", opts)


def publish(sid: str, desc: str, keep_active: bool | None = None) -> int:
    """POST a published version, then re-check isActive. keep_active=None restores the pre-publish state."""
    was = bool(get_meta(sid).get("isActive"))
    r = api("POST", f"/survey-definitions/{sid}/versions", {"Description": desc, "Published": True})
    ver = (r or {}).get("metadata", {}).get("versionNumber") or (r or {}).get("versionNumber")
    time.sleep(1.0)
    want = was if keep_active is None else keep_active
    now = bool(get_meta(sid).get("isActive"))
    if now != want:
        set_active(sid, want, wait=0)
        print(f"  [{sid}] publish left isActive={now}; set back to {want}")
    print(f"  [{sid}] published version {ver}; isActive={bool(get_meta(sid).get('isActive'))}")
    return ver


def set_active(sid: str, active: bool, wait: int = 45) -> None:
    """Set isActive and verify. After activation, wait for the JFE edge cache (default 45 s) before opening the survey."""
    requests.put(f"{API}/surveys/{sid}", headers=HEADERS, json={"isActive": active}, timeout=60)
    time.sleep(1.0)
    if bool(get_meta(sid).get("isActive")) != active:
        raise RuntimeError(f"{sid}: isActive did not become {active}")
    if active and wait:
        time.sleep(wait)


# ------------------------------------------------------------------ snapshot / diff
def snapshot(sid: str, label: str = "pre", out_dir: pathlib.Path | str | None = None, defn: dict | None = None) -> pathlib.Path:
    d = pathlib.Path(out_dir or "backups") / sid
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"definition_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{label}.json"
    p.write_text(json.dumps(defn or get_def(sid), indent=1, ensure_ascii=False))
    return p


def clean(t) -> str:
    import html as _html
    t = re.sub(r"<br\s*/?>", "\n", str(t or ""), flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def diff_defs(old: dict, new: dict) -> list[str]:
    """Human-readable differences: questions added/removed/changed, blocks, flow, options."""
    out = []
    oq, nq = old.get("Questions", {}), new.get("Questions", {})
    for q in sorted(set(nq) - set(oq), key=lambda x: int(x[3:]) if x[3:].isdigit() else 0):
        out.append(f"+ question {q} ({nq[q].get('DataExportTag')}): {clean(nq[q].get('QuestionText'))[:80]}")
    for q in sorted(set(oq) - set(nq), key=lambda x: int(x[3:]) if x[3:].isdigit() else 0):
        out.append(f"- question {q} ({oq[q].get('DataExportTag')})")
    keys = ("QuestionText", "DataExportTag", "Choices", "ChoiceOrder", "Answers", "AnswerOrder", "Validation",
            "DisplayLogic", "QuestionJS", "RecodeValues", "AnalyzeChoices", "Selector", "SubSelector", "Randomization")
    for q in sorted(set(oq) & set(nq), key=lambda x: int(x[3:]) if x[3:].isdigit() else 0):
        ch = [k for k in keys if json.dumps(oq[q].get(k), sort_keys=True) != json.dumps(nq[q].get(k), sort_keys=True)]
        if ch:
            out.append(f"~ question {q} ({nq[q].get('DataExportTag')}): {', '.join(ch)}")
    ob, nb = old.get("Blocks", {}), new.get("Blocks", {})
    for b in set(nb) - set(ob):
        out.append(f"+ block {b} '{nb[b].get('Description')}'")
    for b in set(ob) - set(nb):
        out.append(f"- block {b} '{ob[b].get('Description')}'")
    for b in set(ob) & set(nb):
        for k in ("Description", "BlockElements", "Options"):
            if json.dumps(ob[b].get(k), sort_keys=True) != json.dumps(nb[b].get(k), sort_keys=True):
                out.append(f"~ block {b} '{nb[b].get('Description')}': {k}")
    if json.dumps(old.get("SurveyFlow"), sort_keys=True) != json.dumps(new.get("SurveyFlow"), sort_keys=True):
        out.append("~ SurveyFlow changed")
    oo, no = old.get("SurveyOptions", {}) or {}, new.get("SurveyOptions", {}) or {}
    for k in sorted(set(oo) | set(no)):
        if json.dumps(oo.get(k), sort_keys=True) != json.dumps(no.get(k), sort_keys=True):
            out.append(f"~ option {k}")
    return out


# ------------------------------------------------------------------ invariants
def invariants(defn: dict, timers: bool = False, force_ok: set[str] = frozenset(), allow: set[str] = frozenset()) -> list[str]:
    """Static checks that catch the documented silent failures. Returns violation strings (empty = clean)."""
    Q, B = defn["Questions"], defn["Blocks"]
    flow = defn["SurveyFlow"]["Flow"]
    v = []
    fb = flow_block_ids(flow)
    live = live_qids(defn)
    c = Counter(fb)
    for b, n in c.items():
        if n > 1:
            v.append(f"block {b} referenced {n}x in flow")
        if b not in B:
            v.append(f"flow references unknown block {b}")
    for b, blk in B.items():
        if blk.get("Type") != "Trash" and b not in c:
            v.append(f"block {b} '{blk.get('Description')}' not in flow")
    tags = Counter(Q[q].get("DataExportTag") for q in live if q in Q)
    for t, n in tags.items():
        if n > 1:
            v.append(f"duplicate export tag {t} x{n}")
    tb = trash_block_id(defn)
    trash = {e.get("QuestionID") for e in (B[tb].get("BlockElements", []) if tb else []) if e.get("Type") == "Question"}
    for q in sorted(trash & live):
        v.append(f"{q} is in Trash AND a live block (never renders)")
    for q in sorted(orphan_qids(defn)):
        v.append(f"{q} ({Q[q].get('DataExportTag')}) is an orphan: in no block (invisible in the builder)")
    if timers:
        for b in fb:
            blk = B.get(b, {})
            for pg in pages(blk):
                tm = [q for q in pg if Q.get(q, {}).get("QuestionType") == "Timing"]
                content = [q for q in pg if q not in tm]
                if len(tm) != 1:
                    v.append(f"block '{blk.get('Description')}' page {pg[:3]}: {len(tm)} timers")
                if not content:
                    v.append(f"block '{blk.get('Description')}' page {pg}: timer-only page")
    for el in walk_flow(flow):
        if el.get("Type") == "Branch":
            for qid in sorted(set(re.findall(r"QID\d+", json.dumps(el.get("BranchLogic", {}))))):
                if qid not in live:
                    v.append(f"branch {el.get('FlowID')} references non-live {qid} (always false / always NotSelected)")
    for q in sorted(live):
        dl = Q.get(q, {}).get("DisplayLogic")
        if dl:
            for r in sorted(set(re.findall(r"QID\d+", json.dumps(dl)))):
                if r not in live:
                    v.append(f"display logic on {q} references non-live {r}")
            if dl.get("inPage") is False:
                same_page = set()
                for b in fb:
                    for pg in pages(B[b]):
                        if q in pg:
                            same_page = set(pg)
                for r in set(re.findall(r"QID\d+", json.dumps(dl))):
                    if r in same_page and r != q:
                        v.append(f"display logic on {q} reads same-page {r}: page will auto-split (or never show in-page)")
    for q in sorted(live):
        qq = Q.get(q, {})
        vv = qq.get("Validation")
        st = (vv.get("Settings") if isinstance(vv, dict) else None) or {}
        if isinstance(st, dict) and st.get("ForceResponse") == "ON" and q not in force_ok:
            v.append(f"{q} ({qq.get('DataExportTag')}) ForceResponse ON")
        ac = qq.get("AnalyzeChoices")
        if isinstance(ac, dict) and any(str(x).lower() == "no" for x in ac.values()):
            v.append(f"{q} ({qq.get('DataExportTag')}) AnalyzeChoices=No on {[k for k, x in ac.items() if str(x).lower() == 'no']}: those answers are DROPPED from the data")
    for b in fb:
        opts = B.get(b, {}).get("Options") or {}
        lo = opts.get("LoopingOptions") if isinstance(opts, dict) else None
        if isinstance(lo, dict) and isinstance(lo.get("Static"), dict):
            keys = sorted(lo["Static"], key=lambda k: int(k) if str(k).isdigit() else 0)
            if keys != [str(i) for i in range(1, len(keys) + 1)]:
                v.append(f"loop block {b}: non-contiguous row keys {keys[:6]}… (export prefixes follow the keys)")
    return [x for x in v if not any(a in x for a in allow)]


# ------------------------------------------------------------------ responses
def export_responses(sid: str, out_dir: pathlib.Path | str | None = None, use_labels: bool = False) -> tuple[list[dict], pathlib.Path]:
    """Full CSV export (numeric codes by default). Returns rows (dicts keyed by column) and the csv path.
    The two Qualtrics metadata header rows are dropped from `rows` but kept in the file."""
    r = api("POST", f"/surveys/{sid}/export-responses", {"format": "csv", "useLabels": use_labels, "breakoutSets": False})
    pid = r["progressId"]
    fid = None
    for _ in range(200):
        p = api("GET", f"/surveys/{sid}/export-responses/{pid}")
        if p.get("status") == "complete":
            fid = p["fileId"]
            break
        if p.get("status") == "failed":
            raise RuntimeError("export failed")
        time.sleep(1.5)
    if fid is None:
        raise RuntimeError("export timed out")
    rr = requests.get(f"{API}/surveys/{sid}/export-responses/{fid}/file", headers=HEADERS, timeout=120)
    rr.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(rr.content))
    data = z.read(z.namelist()[0]).decode("utf-8-sig")
    d = pathlib.Path(out_dir or "exports")
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{sid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{'_labels' if use_labels else ''}.csv"
    path.write_text(data)
    lines = list(csv.reader(io.StringIO(data)))
    header = lines[0]
    rows = [dict(zip(header, ln)) for ln in lines[3:]]
    return rows, path


def delete_response(sid: str, rid: str) -> None:
    r = requests.delete(f"{API}/surveys/{sid}/responses/{rid}", headers=HEADERS,
                        json={"decrementQuotas": True}, timeout=60)
    time.sleep(RATE)
    if r.status_code not in (200, 202, 204):
        raise RuntimeError(f"DELETE response {rid} -> {r.status_code}: {r.text[:200]}")


def delete_by_prefix(sid: str, prefix: str, pid_field: str = "PROLIFIC_PID", apply: bool = False,
                     out_dir=None) -> list[tuple[str, str]]:
    """Delete (or list, apply=False) responses whose id field starts with YOUR prefix. Never touches blank-id rows."""
    if not prefix:
        raise ValueError("a non-empty prefix is required")
    rows, _ = export_responses(sid, out_dir)
    hits = [(r["ResponseId"], r.get(pid_field, "")) for r in rows if str(r.get(pid_field, "")).startswith(prefix)]
    if apply:
        for rid, _pid in hits:
            delete_response(sid, rid)
    return hits


# ------------------------------------------------------------------ CLI
def _defn_for(a) -> dict:
    if getattr(a, "published", False):
        got = get_published_def(a.sid)
        if not got:
            raise SystemExit("no published version listed; use the draft (omit --published)")
        d, meta = got
        print(f"# published version {meta.get('versionNumber')} ({meta.get('published') or meta.get('publishEvents', [{}])[-1].get('date', '')})")
        return d
    return get_def(a.sid)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("snapshot"); p.add_argument("sid"); p.add_argument("--label", default="pre"); p.add_argument("--dir", default="backups")
    p = sub.add_parser("published"); p.add_argument("sid"); p.add_argument("--dir", default="backups")
    p = sub.add_parser("diff"); p.add_argument("sid"); p.add_argument("old"); p.add_argument("--published", action="store_true")
    p = sub.add_parser("invariants"); p.add_argument("sid"); p.add_argument("--published", action="store_true")
    p.add_argument("--timers", action="store_true"); p.add_argument("--force-ok", nargs="*", default=[]); p.add_argument("--allow", nargs="*", default=[])
    p = sub.add_parser("list"); p.add_argument("sid"); p.add_argument("--published", action="store_true")
    p = sub.add_parser("flow"); p.add_argument("sid"); p.add_argument("--published", action="store_true")
    p = sub.add_parser("publish"); p.add_argument("sid"); p.add_argument("desc")
    g = p.add_mutually_exclusive_group(); g.add_argument("--keep-active", action="store_true"); g.add_argument("--deactivate", action="store_true")
    p = sub.add_parser("activate"); p.add_argument("sid"); p.add_argument("--wait", type=int, default=45)
    p = sub.add_parser("deactivate"); p.add_argument("sid")
    p = sub.add_parser("export"); p.add_argument("sid"); p.add_argument("--dir", default="exports"); p.add_argument("--labels", action="store_true")
    p = sub.add_parser("delete-qa"); p.add_argument("sid"); p.add_argument("--prefix", required=True)
    p.add_argument("--pid-field", default="PROLIFIC_PID"); p.add_argument("--apply", action="store_true"); p.add_argument("--dir", default="exports")
    a = ap.parse_args()

    if a.cmd == "snapshot":
        print(snapshot(a.sid, a.label, a.dir))
    elif a.cmd == "published":
        got = get_published_def(a.sid)
        if not got:
            raise SystemExit("no published version listed")
        d, meta = got
        print(snapshot(a.sid, f"published_v{meta.get('versionNumber')}", a.dir, defn=d))
    elif a.cmd == "diff":
        old = json.loads(pathlib.Path(a.old).read_text())
        lines = diff_defs(old, _defn_for(a))
        print("\n".join(lines) if lines else "no differences")
    elif a.cmd == "invariants":
        d = _defn_for(a)
        v = invariants(d, timers=a.timers, force_ok=set(a.force_ok), allow=set(a.allow))
        print(f"== {a.sid} invariants: {'OK' if not v else str(len(v)) + ' violation(s)'}")
        for x in v:
            print("   -", x)
        return 0 if not v else 1
    elif a.cmd == "list":
        d = _defn_for(a)
        Q, B = d["Questions"], d["Blocks"]
        print(f"# {d.get('SurveyName')}  live questions: {len(live_qids(d))}  orphans: {len(orphan_qids(d))}")
        for b in flow_block_ids(d["SurveyFlow"]["Flow"]):
            blk = B[b]
            loop = " [loop]" if (blk.get("Options") or {}).get("Looping") else ""
            print(f"\n[{b}] {blk.get('Description')}{loop}")
            for i, pg in enumerate(pages(blk), 1):
                for q in pg:
                    qq = Q.get(q, {})
                    print(f"  p{i} {q:8s} {str(qq.get('DataExportTag')):18s} {qq.get('QuestionType')}/{qq.get('Selector', '')}  {clean(qq.get('QuestionText'))[:70]}")
    elif a.cmd == "flow":
        print("\n".join(flow_text(_defn_for(a))))
    elif a.cmd == "publish":
        publish(a.sid, a.desc, keep_active=True if a.keep_active else (False if a.deactivate else None))
    elif a.cmd == "activate":
        set_active(a.sid, True, wait=a.wait); print("active")
    elif a.cmd == "deactivate":
        set_active(a.sid, False); print("inactive")
    elif a.cmd == "export":
        rows, path = export_responses(a.sid, a.dir, use_labels=a.labels)
        print(f"{len(rows)} rows -> {path}")
    elif a.cmd == "delete-qa":
        hits = delete_by_prefix(a.sid, a.prefix, a.pid_field, apply=a.apply, out_dir=a.dir)
        for rid, pid in hits:
            print(f"  {'deleted' if a.apply else 'would delete'} {rid} ({pid})")
        print(f"{len(hits)} row(s){'' if a.apply else ' (dry run; add --apply)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
