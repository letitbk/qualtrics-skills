"""Minimal Qualtrics v3 helpers for the survey-persona-audit scripts (self-contained; the qualtrics-survey
skill's scripts/qx.py has the full build toolkit). Credentials: QUALTRICS_API_KEY / QUALTRICS_BASE_URL
from the environment, else ~/.Renviron."""
from __future__ import annotations

import csv
import html as _html
import io
import json
import os
import pathlib
import re
import time
import zipfile
from datetime import datetime

import requests


def _renviron() -> dict:
    out = {}
    p = pathlib.Path("~/.Renviron").expanduser()
    if p.exists():
        for line in p.read_text().splitlines():
            m = re.match(r"^(QUALTRICS_\w+)\s*=\s*['\"]?([^'\"\s]+)", line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


_C = _renviron()
BASE_HOST = (os.environ.get("QUALTRICS_BASE_URL") or _C.get("QUALTRICS_BASE_URL") or "").replace("https://", "").rstrip("/")
API_KEY = os.environ.get("QUALTRICS_API_KEY") or _C.get("QUALTRICS_API_KEY") or ""
API = f"https://{BASE_HOST}/API/v3"
HEADERS = {"X-API-TOKEN": API_KEY, "Content-Type": "application/json"}
JFE = f"https://{BASE_HOST}/jfe/form/"


def api(method, path, payload=None, raw=False, timeout=120):
    if not BASE_HOST or not API_KEY:
        raise SystemExit("QUALTRICS_BASE_URL / QUALTRICS_API_KEY not set (env or ~/.Renviron)")
    for attempt in range(5):
        r = requests.request(method, f"{API}{path}", headers=HEADERS, json=payload, timeout=timeout)
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        if not r.ok:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        time.sleep(0.15)
        return r if raw else r.json().get("result")
    raise RuntimeError("rate-limited repeatedly")


def get_def(sid: str) -> dict:
    return api("GET", f"/survey-definitions/{sid}")


def get_published_def(sid: str) -> dict | None:
    vs = api("GET", f"/survey-definitions/{sid}/versions") or {}
    pub = [e for e in (vs.get("elements") or []) if (e.get("metadata") or e).get("published")]
    if not pub:
        return None
    m = max(pub, key=lambda e: int((e.get("metadata") or e).get("versionNumber") or 0))
    m = m.get("metadata") or m
    return api("GET", f"/survey-definitions/{sid}/versions/{m.get('versionID') or m.get('id')}")


def load_def(sid: str, path: str | None = None, published: bool = False) -> dict:
    """Definition from a saved JSON file, else the published version (--published), else the draft."""
    if path:
        return json.loads(pathlib.Path(path).read_text())
    if published:
        d = get_published_def(sid)
        if d:
            return d
        print("# no published version listed; using the draft")
    return get_def(sid)


def get_meta(sid: str) -> dict:
    return api("GET", f"/surveys/{sid}")


def set_active(sid: str, active: bool, wait: int = 45) -> None:
    requests.put(f"{API}/surveys/{sid}", headers=HEADERS, json={"isActive": active}, timeout=60)
    time.sleep(1.0)
    if bool(get_meta(sid).get("isActive")) != active:
        raise RuntimeError(f"{sid}: isActive did not become {active}")
    if active and wait:
        time.sleep(wait)


def export_responses(sid: str, out_dir=None) -> tuple[list[dict], pathlib.Path]:
    r = api("POST", f"/surveys/{sid}/export-responses", {"format": "csv", "useLabels": False, "breakoutSets": False})
    pid, fid = r["progressId"], None
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
    path = d / f"{sid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    path.write_text(data)
    return read_export(path), path


def read_export(path) -> list[dict]:
    lines = list(csv.reader(io.StringIO(pathlib.Path(path).read_text(encoding="utf-8-sig"))))
    header = lines[0]
    return [dict(zip(header, ln)) for ln in lines[3:]]


def delete_response(sid: str, rid: str) -> None:
    r = requests.delete(f"{API}/surveys/{sid}/responses/{rid}", headers=HEADERS, json={"decrementQuotas": True}, timeout=60)
    time.sleep(0.15)
    if r.status_code not in (200, 202, 204):
        raise RuntimeError(f"DELETE {rid} -> {r.status_code}: {r.text[:200]}")


# ----------------------------------------------------------------- definition helpers
def clean(t) -> str:
    t = re.sub(r"<br\s*/?>", "\n", str(t or ""), flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def walk_flow(elems):
    for e in elems:
        yield e
        if isinstance(e.get("Flow"), list):
            yield from walk_flow(e["Flow"])


def flow_block_ids(elems) -> list[str]:
    return [e["ID"] for e in walk_flow(elems) if e.get("Type") in ("Block", "Standard") and e.get("ID")]


def live_qids(defn: dict) -> set[str]:
    return {e["QuestionID"] for b in defn["Blocks"].values() if b.get("Type") != "Trash"
            for e in b.get("BlockElements", []) if e.get("Type") == "Question" and e.get("QuestionID")}


def pages(blk: dict) -> list[list[str]]:
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


def qid_block(defn: dict) -> dict[str, str]:
    """qid -> block description, for blocks in the flow."""
    B = defn["Blocks"]
    return {e["QuestionID"]: B[b]["Description"] for b in flow_block_ids(defn["SurveyFlow"]["Flow"])
            for e in B[b].get("BlockElements", []) if e.get("QuestionID")}


def tag_map(defn: dict) -> dict[str, str]:
    """export tag -> qid for live questions."""
    Q = defn["Questions"]
    return {Q[q]["DataExportTag"]: q for q in live_qids(defn) if q in Q}
