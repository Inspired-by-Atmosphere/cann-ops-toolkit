#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cannlab_report.py - announce pipeline results exactly once.

Why it is a separate script: the full pipeline runs for 45+ minutes and must
not sit in a scheduler tick. The pipeline writes its outcome to a result file;
this short job just reads it and reports it once.

The same result is announced only once (fingerprint de-duplication), and when
there is no new result the script prints nothing at all - which, in a
"no output == no notification" scheduler setup, means total silence.

Environment variables
---------------------
    CANNLAB_SECRETS_DIR  directory holding the result and seen files
                         (default: directory of this script)
    CANNLAB_RESULT_FILE  result json written by cannlab_auto.py
"""
import argparse
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

SECRETS = os.environ.get("CANNLAB_SECRETS_DIR") or os.path.dirname(os.path.abspath(__file__))
RESULT = os.environ.get("CANNLAB_RESULT_FILE") or os.path.join(SECRETS, "cannlab_auto.result.json")
SEEN = os.path.join(SECRETS, "cannlab_report.seen")


def fingerprint(path):
    h = hashlib.sha256()
    h.update(open(path, "rb").read())
    return h.hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser(
        description="Report a pipeline result exactly once. Prints nothing when "
                    "there is no new result, so it is safe in a scheduler.")
    ap.add_argument("--result-file", default=RESULT,
                    help="result json written by cannlab_auto.py")
    ap.add_argument("--seen", default=SEEN,
                    help="fingerprint file used to de-duplicate reports")
    args = ap.parse_args()

    result, seen = args.result_file, args.seen

    if not os.path.exists(result):
        return 0  # silent

    fp = fingerprint(result)
    old = ""
    if os.path.exists(seen):
        old = open(seen, encoding="utf-8").read().strip()
    if fp == old:
        return 0  # already reported, stay silent

    try:
        d = json.load(open(result, encoding="utf-8"))
    except Exception as e:
        print(f"WARN: corrupt result file: {e}")
        open(seen, "w", encoding="utf-8").write(fp)
        return 0

    print(f"[pipeline] {d.get('at')}")
    print(d.get("summary", "(no summary)"))
    steps = d.get("steps") or {}
    if steps:
        print("steps: " + " | ".join(f"{k}={v}" for k, v in steps.items()))

    open(seen, "w", encoding="utf-8").write(fp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
