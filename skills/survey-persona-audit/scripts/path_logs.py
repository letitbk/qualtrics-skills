#!/usr/bin/env python3
"""One readable trace per persona: what they saw on every page (text + screenshot name), what they answered
(with labels), how the export check came out. This is the file reviewers read.

    python3 path_logs.py --definition DIR/definition_SID.json --personas DIR --walks DIR \
        --checks DIR/checks.json --out DIR/path_logs [--capture-root DIR]   (fields come from check.py --fields)
    -> DIR/path_logs/<persona>.md + index.md
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _api as A  # noqa: E402


def readable(act: str, Q: dict) -> str:
    """'QID290:check[1]' -> 'SCR: check "Had a conversation ..."'."""
    m = re.match(r"(?:\d+_)?(QID\d+)(?:#\d+)?:(\w+)\[(.*)\]$", act)
    if not m or m.group(1) not in Q:
        return act
    q = Q[m.group(1)]
    tag, kind, val = q.get("DataExportTag", m.group(1)), m.group(2), m.group(3)
    ch = q.get("Choices") if isinstance(q.get("Choices"), dict) else {}
    an = q.get("Answers") if isinstance(q.get("Answers"), dict) else {}
    lab = None
    if kind in ("radio", "check") and val in ch:
        lab = A.clean(ch[val].get("Display"))
    elif "=" in val:
        r, a = val.split("=", 1)
        lab = f"{A.clean(ch.get(r, {}).get('Display')) or r} = {A.clean(an.get(a, {}).get('Display')) or a}"
    return f"{tag}: {kind} {lab!r}" if lab else f"{tag}: {kind}[{val}]"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--definition", required=True)
    ap.add_argument("--personas", required=True)
    ap.add_argument("--walks", required=True)
    ap.add_argument("--checks", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--capture-root", default=None, help="where screenshots live (printed as a path hint)")
    a = ap.parse_args()
    d = json.loads(pathlib.Path(a.definition).read_text())
    Q = d["Questions"]
    checks = {}
    if a.checks and pathlib.Path(a.checks).exists():
        checks = {R["name"]: R for R in json.loads(pathlib.Path(a.checks).read_text())["personas"]}
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    index = ["# Simulated paths", "", "| Persona | Expected path | Walk end | Pages | Check |", "|---|---|---|---|---|"]
    pdir = pathlib.Path(a.personas)
    files = sorted(pdir.glob("*.json")) if pdir.is_dir() else [pdir]
    n = 0
    for pf in files:
        p = json.loads(pf.read_text())
        name = p.get("name") or pf.stem
        wf = pathlib.Path(a.walks) / f"{name}.json"
        w = json.loads(wf.read_text()) if wf.exists() else {"end": "no walk", "pages": 0, "log": []}
        if isinstance(w, list):
            w = w[0]
        R = checks.get(name, {})
        L = [f"# {name}", "",
             f"- Survey: {d.get('SurveyName')} ({w.get('sid', '')})",
             f"- Persona: {p.get('brief', '')}",
             f"- Expected path: {p.get('expected_path', '')}",
             f"- Scripted answers: `{json.dumps(p.get('answers', {}), ensure_ascii=False)[:600]}`",
             f"- Walk end: {w.get('end')} after {w.get('pages')} pages",
             f"- Screenshots: `{a.capture_root or '<capture dir>'}/{name}/pNNN.png`", ""]
        if R:
            L += ["## Automated check against the export", "",
                  f"- Response: {R.get('response_id')} (finished = {R.get('finished')}, flag = {R.get('flag')}); "
                  f"answers matching the persona: {R.get('answers_ok')}/{R.get('answers_checked')}; loop items filled: {R.get('loop_filled')}",
                  f"- Soft prompts after: {R.get('soft_prompts')}"]
            L += [f"- Finding: {f}" for f in R.get("findings", [])] or ["- Findings: none"]
            L += [f"- OK: {o}" for o in R.get("ok", [])[:12]]
            if R.get("exported"):
                L.append(f"- Exported fields: {R['exported']}")
            if R.get("walker_notes"):
                L.append(f"- Walker notes (harness, not survey): {R['walker_notes']}")
            L.append("")
        L += ["## Pages in the order shown", ""]
        for e in w.get("log", []):
            if "note" in e:
                L += [f"### Page {e['page']}: soft prompt", "", f"Prompt text: {e.get('text', '')!r}. The respondent chose to continue without answering.", ""]
                continue
            if "end" in e:
                L += [f"### End: {e['end']}", ""]
                if e.get("seen"):
                    L += ["```text", e["seen"][:3000], "```", ""]
                continue
            L += [f"### Page {e['page']}: {', '.join(e.get('tags') or []) or '(no questions)'}" + (f" (screenshot {e['shot']})" if e.get("shot") else ""), "",
                  "Seen:", "", "```text", (e.get("seen") or "(page text not captured; run walk.py with --capture)")[:6000], "```", "",
                  "Answered: " + ("; ".join(readable(x, Q) for x in e.get("acts", [])) or "nothing"), ""]
        (out / f"{name}.md").write_text("\n".join(L))
        n += 1
        nf = len(R.get("findings", [])) if R else None
        index.append(f"| [{name}]({name}.md) | {p.get('expected_path', '')} | {str(w.get('end'))[:40]} | {w.get('pages')} | "
                     + ("no check" if nf is None else ("OK" if nf == 0 else f"{nf} finding(s)")) + " |")
    (out / "index.md").write_text("\n".join(index) + "\n")
    print(f"wrote {n} path logs to {out}")


if __name__ == "__main__":
    main()
