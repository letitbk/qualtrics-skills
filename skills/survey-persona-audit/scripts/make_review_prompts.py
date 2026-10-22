#!/usr/bin/env python3
"""Render one independent-review prompt per persona (for Codex, Gemini, or a Claude subagent), and
optionally run them through the Codex CLI read-only.

    python3 make_review_prompts.py --rules rules.md --logs DIR/path_logs --personas DIR \
        --definition DIR/definition_SID.json --out DIR/reviews [--run codex --jobs 4] [--only a,b]
    -> DIR/reviews/prompts/<persona>.md   (+ DIR/reviews/<persona>.md when --run codex)

Paths inside the prompt are written relative to the current working directory; run this from the
repository root the reviewer will be given. The template is in references/reviewing-traces.md.
"""
import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import shutil
import subprocess

TEMPLATE = """You are reviewing one simulated respondent's path through a survey. This is an analysis task only: do not modify any file.

Read these files (paths are relative to the working directory):
- {rules}: what the survey is supposed to do (the agreed rules). Treat it as the specification; where it is silent, use the definition.
- {log}: the path log. For every page it gives the export tags on the page, the exact text the respondent could see ("Seen"), what the scripted respondent answered, and the automated check against the exported response.
- {persona}: the scripted respondent's planned answers and the path they were designed to take.
- {definition}: the published survey definition (Qualtrics JSON: SurveyFlow with branches and embedded data, question display logic, choices, texts). Use it to derive which pages this respondent should have seen given their answers.

Check, citing page numbers and quoting the Seen text:
1. Route. Given the answers, list the blocks/pages the flow should show and compare with the pages actually shown. Report any page shown that should not have been, any page missing, any page out of order.
2. Content on each page. Flag wording that breaks the rules file; text inconsistent with what this respondent said earlier (a reminder that contradicts their answer, a question about current behaviour shown to someone who said they do not do it); unfilled piped text ("${{e://...}}", "${{q://...}}", empty quotes); typos; duplicated or missing answer options; mixed-up variants (wrong arm, wrong domain wording).
3. Recorded data. Using the automated check section, say whether the export stored the route, the answers and the derived fields as expected.
4. Anything a real respondent on this path would find confusing.

Distinguish survey defects from artifacts of the test harness (the automation browser being blocked at the final redirect, placeholder completion codes, the scripted respondent's own contradictory answers, walker defaults). Do not report issues you cannot support with a quote from these files.

Write plain Markdown with these sections:
## Verdict
One line: PASS (route and content as specified), ISSUES (count by severity), or CANNOT TELL (say why).
## Route
Expected vs shown, as a short list.
## Findings
A table: severity (blocker / high / medium / low / note), page, quote, what is wrong, survey defect or test artifact. Write "None" if there are none.
## Data recorded
Two or three sentences.
"""


def run_codex(prompt_path: pathlib.Path, out: pathlib.Path, cwd: pathlib.Path) -> str:
    r = subprocess.run(["codex", "--no-daemon", "exec", "-s", "read-only", "-C", str(cwd), "--skip-git-repo-check",
                        "--ephemeral", "-o", str(out), prompt_path.read_text()], capture_output=True, text=True, timeout=1800)
    ok = out.exists() and out.stat().st_size > 200
    if not ok:
        out.with_suffix(".stderr.txt").write_text(r.stdout[-4000:] + "\n---\n" + r.stderr[-4000:])
        return f"FAILED rc={r.returncode}"
    return out.read_text().split("## Verdict", 1)[-1].strip().splitlines()[0][:120]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--personas", required=True)
    ap.add_argument("--definition", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run", choices=["codex"], default=None)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    cwd = pathlib.Path.cwd()
    out = pathlib.Path(a.out)
    (out / "prompts").mkdir(parents=True, exist_ok=True)
    rel = lambda p: os.path.relpath(pathlib.Path(p).resolve(), cwd)  # noqa: E731
    pdir = pathlib.Path(a.personas)
    files = sorted(pdir.glob("*.json")) if pdir.is_dir() else [pdir]
    only = [x for x in a.only.split(",") if x]
    items = []
    for pf in files:
        name = json.loads(pf.read_text()).get("name") or pf.stem
        if only and name not in only:
            continue
        log = pathlib.Path(a.logs) / f"{name}.md"
        if not log.exists():
            print(f"  {name}: no path log, skipped")
            continue
        prompt = TEMPLATE.format(rules=rel(a.rules), log=rel(log), persona=rel(pf), definition=rel(a.definition))
        pp = out / "prompts" / f"{name}.md"
        pp.write_text(prompt)
        items.append((name, pp))
    print(f"{len(items)} prompts -> {out / 'prompts'}")
    if a.run == "codex":
        if not shutil.which("codex"):
            raise SystemExit("codex CLI not found on PATH")
        with cf.ThreadPoolExecutor(a.jobs) as ex:
            futs = {ex.submit(run_codex, pp, out / f"{name}.md", cwd): name for name, pp in items}
            for f in cf.as_completed(futs):
                print(f"  {futs[f]:28s} {f.result()}", flush=True)


if __name__ == "__main__":
    main()
