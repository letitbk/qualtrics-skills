#!/usr/bin/env python3
"""Scripted Playwright walk of one persona through a Qualtrics (JFE) survey, recording what was seen.

    python3 walk.py SID --catalog DIR/catalog_SID.json --persona personas/x.json --pid QA_s1_x \
        --out walks/x.json [--capture capture/x] [--headed] [--activate] \
        [--pid-param PROLIFIC_PID] [--query "STUDY_ID=QA&SESSION_ID=QA"] [--redirect-prefix URL ...] \
        [--end-text TEXT ...] [--handlers my_handlers.py] [--max-pages 200]

Persona JSON: {"name", "brief", "expected_path", "answers": {TAG_or_QID: value}, "text_default": "..."}
  value: int/str choice id (MC) | [ids] (multi-select) | {"row": answer, "*": answer} or int (matrix)
         | {"loopkey": answer, "*": answer} (Loop & Merge) | "text" (TE) | {"label": "..."} or int index (dropdown)
         | 0.0-1.0 (slider) | "SKIP" (leave blank). Unlisted questions get a default (first option / middle / text_default).
Handlers (optional): a Python file with `def answer(walk, dom_id, qid, tag, outer, value) -> list[str] | None`
  for custom widgets (rosters, JS-built inputs); return None to fall through to the default answering.

Output JSON (one object): {"pid", "end", "pages", "log": [ {page, qids, tags, acts, url, seen?, shot?} |
  {page, note, after, text} (soft prompt) | {page, end, seen?} ]}. Exit code 0 even when the walk ends in ERROR:
  read `end`. The rules this walker encodes are listed in references/qualtrics-walker.md.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _api as A  # noqa: E402

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout  # noqa: E402

DEFAULT_REDIRECTS = ("https://app.prolific.com", "https://auth.prolific.com")   # Prolific bounces headless browsers to its login host
DEFAULT_END_TEXTS = ("You have chosen not to participate", "This was the last question",
                     "We thank you for your time", "Thank you for participating",
                     "survey is not currently active", "not currently active")


class Walk:
    def __init__(self, page, persona: dict, tags: dict, catalog: dict, log: list, *, max_pages=200,
                 capture: pathlib.Path | None = None, redirects=DEFAULT_REDIRECTS, end_texts=DEFAULT_END_TEXTS,
                 handlers=None):
        self.page, self.persona, self.log = page, persona, log
        self.tags = tags                     # qid -> export tag
        self.cat = {q["qid"]: q for q in catalog.get("questions", [])}
        self.max_pages, self.capture = max_pages, capture
        self.redirects, self.end_texts = tuple(redirects), tuple(end_texts)
        self.handlers = handlers
        self.answers = {}                    # qid -> value (persona keyed by tag or qid)
        t2q = {t: q for q, t in tags.items()}
        for k, v in (persona.get("answers") or {}).items():
            q = k if k.startswith("QID") else t2q.get(k)
            if q:
                self.answers[q] = v
        self.text_default = persona.get("text_default", "qa text")

    # ------------------------------------------------------------ page state
    def seen_text(self) -> str | None:
        if not self.capture:
            return None
        try:
            body = self.page.locator("#Questions")
            t = body.inner_text(timeout=3000) if body.count() else self.page.locator("body").inner_text(timeout=3000)
            return re.sub(r"\n{3,}", "\n\n", t).strip()
        except Exception as e:
            return f"(could not read page text: {type(e).__name__})"

    def modal_text(self) -> str | None:
        try:
            if self.page.locator("body.Inactive").count():
                t = self.page.locator(".Modal, .QModal, [role=dialog]").all_inner_texts()
                return " | ".join(x.strip()[:200] for x in t) or "body.Inactive (no text)"
        except Exception:
            pass
        return None

    def at_end(self) -> str | None:
        url = self.page.url
        if url.startswith(self.redirects):
            return f"redirect:{url}"
        eos = self.page.locator("#EndOfSurvey")
        # the ConfirmStart stub also contains #EndOfSurvey (hidden): require visible AND no Next button
        if eos.count() and eos.first.is_visible() and not self.page.locator("#NextButton").count():
            return "EndOfSurvey"
        try:
            body = self.page.locator("body").inner_text(timeout=2000)
        except Exception:
            body = ""
        for marker in self.end_texts:
            if marker.lower() in body.lower() and not self.page.locator("#NextButton").count():
                return f"text:{marker}"
        return None

    def validation_errors(self) -> list[str]:
        try:
            return [t.strip()[:120] for t in self.page.locator(".ValidationError, [class*='alidation']").all_inner_texts() if t.strip()]
        except Exception:
            return []

    def question_ids(self) -> list[str]:
        try:
            ids = self.page.evaluate("() => Array.from(document.querySelectorAll('.QuestionOuter'))"
                                     ".filter(e => e.offsetParent !== null).map(e => e.id)")
            return [i for i in ids if i]
        except Exception as e:      # execution context destroyed by the final redirect
            if "context was destroyed" in str(e) or "navigation" in str(e).lower():
                return []
            raise

    @staticmethod
    def qid_of(dom_id: str) -> str:
        m = re.search(r"(QID\d+)", dom_id)
        return m.group(1) if m else dom_id

    def click_next(self) -> bool:
        btn = self.page.locator("#NextButton")
        btn.wait_for(state="attached", timeout=15000)
        self.page.wait_for_function("() => { const b = document.querySelector('#NextButton'); return b && !b.disabled; }", timeout=15000)
        before = json.dumps(self.question_ids())
        btn.click()
        cond = ("(b) => document.body.classList.contains('Inactive')"
                " || JSON.stringify(Array.from(document.querySelectorAll('.QuestionOuter'))"
                ".filter(e => e.offsetParent !== null).map(e => e.id)) !== b"
                " || (document.querySelector('#EndOfSurvey') && document.querySelector('#EndOfSurvey').offsetParent !== null"
                "     && !document.querySelector('#NextButton'))"
                + "".join(f" || location.href.startsWith('{h}')" for h in self.redirects))
        changed = True
        try:
            self.page.wait_for_function(cond, arg=before, timeout=20000)
        except PWTimeout:
            changed = False
        if changed:
            try:
                self.page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout:
                pass
            time.sleep(0.4)
            if self.page.locator("body.Inactive").count():
                return True
            if json.dumps(self.question_ids()) == before and self.page.locator("#NextButton").count():
                try:
                    self.page.wait_for_function(cond, arg=before, timeout=15000)
                except PWTimeout:
                    changed = False
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass
        time.sleep(0.5)
        return changed

    # ------------------------------------------------------------ answering
    def click_wrapper(self, inp) -> None:
        """Click the li/td/label wrapper of a radio/checkbox input (raw inputs are pointer-intercepted)."""
        h = inp.element_handle()
        if h is None:
            return
        w = h.evaluate_handle("el => el.closest('li') || el.closest('td') || el.closest('label') || el.parentElement")
        try:
            w.as_element().click()
        except Exception:
            inp.check(force=True)

    def answer(self, dom_id: str) -> list[str]:
        qid = self.qid_of(dom_id)
        tag = self.tags.get(qid, qid)
        outer = self.page.locator(f'[id="{dom_id}"]')
        cls = outer.get_attribute("class") or ""
        val = self.answers.get(qid)
        m_it = re.match(r"^(\d+)_QID", dom_id)          # Loop & Merge page: "{rowkey}_QIDnn"
        if m_it and isinstance(val, dict) and "label" not in val and (m_it.group(1) in val or "*" in val):
            val = val.get(m_it.group(1), val.get("*"))
        acts: list[str] = []
        if "Timing" in cls or re.search(r"\b(DB|Meta)\b", cls):   # timers, text-only, browser meta
            return acts
        if val == "SKIP":
            return [f"{qid}:skipped"]
        if self.handlers is not None:
            r = self.handlers.answer(self, dom_id, qid, tag, outer, val)
            if r is not None:
                return r
        if "Matrix" in cls or outer.locator("tr").filter(has=self.page.locator("input[type=radio]")).count() > 1:
            self.answer_matrix(outer, qid, val, acts)
            return acts
        if outer.locator("select").count():
            sel = outer.locator("select").first
            if isinstance(val, dict) and "label" in val:
                try:
                    sel.select_option(label=val["label"])
                    acts.append(f"{qid}:select[{val['label']}]")
                except Exception:
                    sel.select_option(index=1)
                    acts.append(f"{qid}:select[label-miss->idx1]")
            elif isinstance(val, int):
                sel.select_option(index=val)
                acts.append(f"{qid}:select[idx{val}]")
            else:
                n = sel.locator("option").count()
                sel.select_option(index=1 if n > 1 else 0)
                acts.append(f"{qid}:select[idx1]")
            return acts
        if outer.locator("input[type=checkbox]").count():
            boxes = outer.locator("input[type=checkbox]")
            if isinstance(val, list):
                for c in val:
                    inp = outer.locator(f"input[type=checkbox][id$='~{c}']")
                    if inp.count():
                        self.click_wrapper(inp.first)
                        acts.append(f"{qid}:check[{c}]")
                    else:
                        acts.append(f"{qid}:check[{c}]:MISSING")
            else:
                self.click_wrapper(boxes.first)
                acts.append(f"{qid}:check[first]")
            self.fill_choice_text(outer, qid, acts)
            return acts
        if outer.locator("input[type=radio]").count():
            if val is None:
                self.click_wrapper(outer.locator("input[type=radio]").first)
                acts.append(f"{qid}:radio[first]")
            else:
                inp = outer.locator(f"input[type=radio][id$='~{val}']")
                if not inp.count():
                    inp = outer.locator(f"input[type=radio][value='{val}']")
                if inp.count():
                    acts.append(f"{qid}:radio[{val}]")
                else:
                    inp = outer.locator("input[type=radio]")
                    acts.append(f"{qid}:radio[{val}->first]")
                self.click_wrapper(inp.first)
            self.fill_choice_text(outer, qid, acts)
            return acts
        if outer.locator("textarea").count():
            outer.locator("textarea").first.fill(val if isinstance(val, str) else self.text_default)
            return [f"{qid}:textarea"]
        track = outer.locator("[class*='-track'], .horizontalbar, .slider-track")
        if track.count():
            self.answer_slider(outer, track.first, qid, val, acts)
            return acts
        if outer.locator("input[type=text]").count():
            txt = val if isinstance(val, str) else self.text_default
            outer.locator("input[type=text]").first.fill(txt)
            return [f"{qid}:text"]
        return [f"{qid}:unhandled[{cls[:40]}]"]

    def fill_choice_text(self, outer, qid, acts) -> None:
        """Text boxes attached to a SELECTED choice ('Other, please specify'): fill them so the page validates.
        Typing into an unselected choice's box auto-selects that choice, so only checked wrappers are touched."""
        try:
            sel = outer.locator("li:has(input:checked) input[type=text], label:has(input:checked) input[type=text], "
                                "td:has(input:checked) input[type=text]")
            for inp in sel.all():
                if inp.is_visible() and not (inp.input_value() or "").strip():
                    inp.fill(self.text_default)
                    acts.append(f"{qid}:choice_text")
        except Exception:
            pass

    def answer_matrix(self, outer, qid, val, acts) -> None:
        rows = outer.locator("tr").filter(has=self.page.locator("input[type=radio], input[type=checkbox]"))
        for i in range(rows.count()):
            tr = rows.nth(i)
            if not tr.is_visible():
                continue
            inputs = tr.locator("input[type=radio], input[type=checkbox]")
            if not inputs.count():
                continue
            row_id = (inputs.first.get_attribute("name") or "").split("~")[-1]
            want = None
            if isinstance(val, dict):
                if row_id in val:
                    want = str(val[row_id])
                elif "*" in val:
                    want = str(val["*"])
                else:
                    continue                                   # unlisted rows stay blank (soft prompt)
            elif isinstance(val, int):
                want = str(val)
            target = None
            if want is not None:
                target = tr.locator(f"input[value='{want}']")
                if not target.count():
                    target = tr.locator(f"input[id$='~{want}']")
            if target is None or not target.count():
                n = inputs.count()
                target = inputs.nth(min(n - 1, max(0, n // 2)))  # default: middle option
            self.click_wrapper(target.first)
            acts.append(f"{qid}:row{row_id}={want or 'mid'}")

    def answer_slider(self, outer, tr, qid, val, acts) -> None:
        try:
            box = tr.bounding_box()
            handle = outer.locator("[class*='handle']").first
            hb = handle.bounding_box() if handle.count() else None
            if box:
                frac = 0.6 if not isinstance(val, (int, float)) else max(0.02, min(0.98, float(val)))
                tx, ty = box["x"] + box["width"] * frac, box["y"] + box["height"] / 2
                if hb:                                         # a real drag; a track click is ignored
                    self.page.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2)
                    self.page.mouse.down()
                    self.page.mouse.move(tx, ty, steps=12)
                    self.page.mouse.up()
                    acts.append(f"{qid}:slider_drag[{frac:.2f}]")
                else:
                    self.page.mouse.click(tx, ty)
                    acts.append(f"{qid}:slider_click[{frac:.2f}]")
                time.sleep(0.3)
        except Exception as e:
            acts.append(f"{qid}:slider:ERR:{type(e).__name__}")
        ti = outer.locator("input[type=text]")
        if ti.count() and not (ti.first.input_value() or "").strip():
            try:
                ti.first.fill("3.5")
                acts.append(f"{qid}:slider_text")
            except Exception:
                pass

    # ------------------------------------------------------------ main loop
    def run(self) -> dict:
        page_no = 0
        while page_no < self.max_pages:
            time.sleep(0.4)
            m = None
            for _ in range(5):                                 # give a soft-prompt modal ~2 s to appear
                m = self.modal_text()
                if m:
                    break
                time.sleep(0.4)
            if not m:
                # the JFE briefly shows the previous page after Next: wait until the visible ids are
                # stable across two reads and differ from the page just submitted
                prev_ids = next((e["qids"] for e in reversed(self.log) if "qids" in e), None)
                last_note = self.log[-1].get("note") if self.log else None
                last = None
                for _ in range(20):
                    cur = self.question_ids()
                    if cur == last and (cur != prev_ids or last_note or not cur):
                        break
                    last = cur
                    time.sleep(0.4)
                    if self.modal_text():
                        break
                m = self.modal_text()
            if m and "Response Requested" in m:
                btn = self.page.get_by_role("button", name=re.compile("Continue Without Answering", re.I))
                if not btn.count():
                    btn = self.page.locator("button", has_text="Continue Without Answering")
                btn.first.click()
                prev = next((e for e in reversed(self.log) if "qids" in e), None)
                self.log.append({"page": page_no, "note": "soft-response prompt -> continued",
                                 "after": [self.qid_of(x) for x in (prev or {}).get("qids", [])][:4], "text": m[:160]})
                try:
                    self.page.wait_for_load_state("networkidle", timeout=15000)
                except PWTimeout:
                    pass
                time.sleep(0.6)
                page_no += 1
                continue
            if m:
                shot = self.shot(f"modal_p{page_no}")
                raise RuntimeError(f"page {page_no}: blocking modal: {m} | questions={self.question_ids()} | shot={shot}")
            end = self.at_end()
            if end:
                self.log.append({"page": page_no, "end": end, "seen": self.seen_text()})
                return {"end": end, "pages": page_no}
            ids = self.question_ids()
            seen = self.seen_text()
            acts: list[str] = []
            for dom_id in ids:
                try:
                    acts += self.answer(dom_id)
                except Exception as e:
                    acts.append(f"{self.qid_of(dom_id)}:ERR:{type(e).__name__}:{str(e)[:80]}")
            entry = {"page": page_no, "qids": ids, "tags": [self.tags.get(self.qid_of(x), self.qid_of(x)) for x in ids],
                     "acts": acts, "url": self.page.url[:160]}
            if self.capture:
                entry["seen"] = seen
                s = self.shot(f"p{page_no:03d}")
                if s:
                    entry["shot"] = s.name
            self.log.append(entry)
            if not self.page.locator("#NextButton").count():
                end = None
                for _ in range(15):                  # the end page or the redirect may still be settling
                    end = self.at_end()
                    if end:
                        break
                    time.sleep(0.4)
                end = end or f"no NextButton ({self.page.url[:100]})"
                self.log.append({"page": page_no, "end": end, "seen": self.seen_text()})
                return {"end": end, "pages": page_no}
            if not self.click_next():
                errs = self.validation_errors()
                shot = self.shot(f"stall_p{page_no}")
                raise RuntimeError(f"page {page_no} did not advance after Next; questions={ids}; "
                                   f"validation={errs or '(none surfaced)'}; shot={shot}")
            page_no += 1
        return {"end": "max_pages", "pages": page_no}

    def shot(self, name: str) -> pathlib.Path | None:
        d = self.capture or pathlib.Path("walk_shots")
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{name}.png"
        try:
            self.page.screenshot(path=str(p), full_page=True)
            return p
        except Exception:
            return None


def survey_url(sid: str, pid: str, pid_param: str, query: str | None) -> str:
    q = f"{pid_param}={pid}" if pid_param else ""
    if query:
        q = f"{q}&{query}" if q else query
    return f"{A.JFE}{sid}" + (f"?{q}" if q else "")


def load_handlers(path: str | None):
    if not path:
        return None
    spec = importlib.util.spec_from_file_location("walk_handlers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def do_walk(sid, persona, catalog, pid, *, pid_param="PROLIFIC_PID", query=None, headed=False, capture=None,
            redirects=DEFAULT_REDIRECTS, end_texts=DEFAULT_END_TEXTS, handlers=None, max_pages=200) -> dict:
    tags = {q["qid"]: q["tag"] for q in catalog.get("questions", [])}
    log: list = []
    if capture:
        capture.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(8000)              # a vanished element should not cost 30 s
        page.set_default_navigation_timeout(60000)  # set separately: default_timeout also covers goto
        page.goto(survey_url(sid, pid, pid_param, query), wait_until="networkidle")
        w = Walk(page, persona, tags, catalog, log, max_pages=max_pages, capture=capture,
                 redirects=redirects, end_texts=end_texts, handlers=handlers)
        try:
            res = w.run()
        except Exception as e:
            res = {"end": f"ERROR:{e}", "pages": len([x for x in log if 'qids' in x])}
        browser.close()
    res.update({"pid": pid, "sid": sid, "persona": persona.get("name"), "log": log,
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sid")
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--persona", required=True)
    ap.add_argument("--pid", required=True, help="test id, e.g. QA_<stage>_<persona>; used to find the export row")
    ap.add_argument("--pid-param", default="PROLIFIC_PID", help="URL parameter the survey captures as embedded data ('' for none)")
    ap.add_argument("--query", default=None, help="extra URL query, e.g. STUDY_ID=QA&SESSION_ID=QA")
    ap.add_argument("--out", required=True)
    ap.add_argument("--capture", default=None, help="directory for per-page text + screenshots")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--activate", action="store_true", help="activate, wait 45 s, walk, deactivate (always)")
    ap.add_argument("--redirect-prefix", nargs="*", default=list(DEFAULT_REDIRECTS))
    ap.add_argument("--end-text", nargs="*", default=list(DEFAULT_END_TEXTS))
    ap.add_argument("--handlers", default=None)
    ap.add_argument("--max-pages", type=int, default=200)
    a = ap.parse_args()
    persona = json.loads(pathlib.Path(a.persona).read_text())
    catalog = json.loads(pathlib.Path(a.catalog).read_text())
    if a.activate:
        A.set_active(a.sid, True)
        print("activated; waited for the edge cache")
    try:
        res = do_walk(a.sid, persona, catalog, a.pid, pid_param=a.pid_param, query=a.query, headed=a.headed,
                      capture=pathlib.Path(a.capture) if a.capture else None, redirects=a.redirect_prefix,
                      end_texts=a.end_text, handlers=load_handlers(a.handlers), max_pages=a.max_pages)
    finally:
        if a.activate:
            A.set_active(a.sid, False)
            print("deactivated")
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    print(f"{persona.get('name')}: end={res['end']} pages={res['pages']} -> {out}")
    for e in res["log"]:
        if "qids" in e:
            print(f"  p{e['page']}: {e['tags']} -> {e['acts']}")
        elif "end" in e:
            print(f"  p{e['page']}: END {e['end']}")
        else:
            print(f"  p{e['page']}: {e.get('note')}")


if __name__ == "__main__":
    main()
