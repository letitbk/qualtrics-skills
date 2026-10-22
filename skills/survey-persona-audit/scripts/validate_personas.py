#!/usr/bin/env python3
"""Validate persona files against a catalog before walking: unknown tags, bad choice/row/answer ids,
dropdown labels, loop keys, value shapes. Fixes nothing; prints issues; exit 1 if any.

    python3 validate_personas.py --catalog DIR/catalog_SID.json --personas DIR_OR_FILES...
"""
import argparse
import json
import pathlib
import sys


def validate(cat, path) -> list[str]:
    byt = {q["tag"]: q for q in cat["questions"]}
    byq = {q["qid"]: q for q in cat["questions"]}
    try:
        d = json.loads(path.read_text())
    except Exception as e:
        return [f"INVALID JSON: {e}"]
    issues = []
    if not d.get("name"):
        issues.append("missing 'name'")
    for key, v in (d.get("answers") or {}).items():
        q = byt.get(key) or byq.get(key)
        if not q:
            issues.append(f"unknown tag/qid {key}")
            continue
        if v == "SKIP":
            continue
        t = q["type"]
        ch, an = set(q.get("choices", {})), set(q.get("answers", {}))
        if q.get("loop_items"):
            keys = set(q["loop_items"])
            if not (isinstance(v, dict) and all(k == "*" or k in keys for k in v) and all(str(x) in ch for x in v.values())):
                issues.append(f"{key}: loop dict keys must be loop row keys or '*', values choice ids from {sorted(ch, key=int)}; got {str(v)[:80]}")
        elif t.startswith("MC/MAVR") or t.startswith("MC/MAHR") or t.startswith("MC/MACOL") or t.startswith("MC/MSB"):
            if not isinstance(v, list) or any(str(x) not in ch for x in v):
                issues.append(f"{key}: multi-select needs a list of ids from {sorted(ch, key=int)}, got {v}")
        elif t.startswith("MC/DL"):
            if not (isinstance(v, dict) and v.get("label") in set(q.get("choices", {}).values())):
                issues.append(f"{key}: dropdown needs {{\"label\": <exact label>}}; got {v}")
        elif t.startswith("MC"):
            if isinstance(v, (dict, list)) or str(v) not in ch:
                issues.append(f"{key}: single choice id from {sorted(ch, key=int)}, got {v}")
        elif t.startswith("Matrix"):
            if not isinstance(v, dict):
                issues.append(f"{key}: matrix needs a dict")
                continue
            bad = [k for k in v if k != "*" and k not in ch] + [f"{k}->{x}" for k, x in v.items() if str(x) not in an]
            if bad:
                issues.append(f"{key}: bad rows/answers {bad[:5]} (rows {sorted(ch, key=int)}, answers {sorted(an, key=int)})")
        elif t.startswith("TE"):
            if not isinstance(v, str):
                issues.append(f"{key}: text must be a string, got {v!r}")
        elif t.startswith("Slider"):
            if not isinstance(v, (int, float)) or not 0 <= v <= 1:
                issues.append(f"{key}: slider needs a number 0.0-1.0")
    return issues


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--personas", nargs="+", required=True, help="directory or persona JSON files")
    a = ap.parse_args()
    cat = json.loads(pathlib.Path(a.catalog).read_text())
    files = []
    for p in a.personas:
        p = pathlib.Path(p)
        files += sorted(p.glob("*.json")) if p.is_dir() else [p]
    n_bad = 0
    for f in files:
        iss = validate(cat, f)
        if iss:
            n_bad += 1
            print(f"{f.name}: {len(iss)} issue(s)")
            for i in iss:
                print("   -", i)
    print(f"{len(files) - n_bad}/{len(files)} persona files clean")
    sys.exit(1 if n_bad else 0)


if __name__ == "__main__":
    main()
