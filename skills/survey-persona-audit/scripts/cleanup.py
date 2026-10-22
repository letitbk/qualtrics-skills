#!/usr/bin/env python3
"""Delete the test responses of an audit by id prefix — and nothing else.

    python3 cleanup.py SID --prefix QA_s1_ [--pid-field PROLIFIC_PID] [--apply] [--export-dir DIR]

Dry run by default. Never deletes rows with a blank id (in a partner-run survey those are the partner's testers).
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _api as A  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sid")
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--pid-field", default="PROLIFIC_PID")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--export-dir", default="exports")
    a = ap.parse_args()
    if not a.prefix:
        raise SystemExit("a non-empty prefix is required")
    rows, path = A.export_responses(a.sid, a.export_dir)
    hits = [(r["ResponseId"], r.get(a.pid_field, "")) for r in rows if str(r.get(a.pid_field, "")).startswith(a.prefix)]
    for rid, pid in hits:
        if a.apply:
            A.delete_response(a.sid, rid)
        print(f"  {'deleted' if a.apply else 'would delete'} {rid} ({pid})")
    print(f"{len(hits)} of {len(rows)} rows match prefix {a.prefix!r}{'' if a.apply else ' (dry run; add --apply)'}; export {path}")


if __name__ == "__main__":
    main()
