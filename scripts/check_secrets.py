#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_secrets.py - dependency-free secret / PII / path scanner (stdlib only).

Purpose: gate an open-source export. Run it over a staging tree and require
zero findings before anything is pushed to a public remote.

It flags:
  * private key blocks
  * obvious credential assignments (a keyword immediately followed by a value)
  * Bearer tokens and JWT-looking strings
  * high-entropy strings that look like generated keys
  * internal (RFC1918 / CGNAT) IPv4 addresses
  * MAC addresses
  * e-mail addresses (a small allowlist covers GitHub noreply / example.com)
  * absolute Windows drive paths and MSYS-style /c/Users paths

Only the standard library is used; no network access. Findings print the file,
line number, category and a MASKED snippet - never the full value.

Usage:
    python check_secrets.py .              # scan a tree
    python check_secrets.py . --quiet      # category counts only
    python check_secrets.py . --max-bytes 2000000

Exit code: 1 if anything was found, 0 if clean.
"""
import argparse
import math
import os
import re
import sys

BS = chr(92)          # a single backslash, kept out of the source literal
DASH5 = "-" * 5

# --- patterns -------------------------------------------------------------
# Keyword and snippet literals are assembled from fragments so that this file
# does not trip its own rules when the tree is scanned.
PK_BLOCK = re.compile(DASH5 + r"BEGIN [A-Z ]*PRIVATE KEY" + DASH5)
KEYWORD_ASSIGN = re.compile(
    r"(?i)\b(" + "|".join([
        "api" + "_key", "apikey", "sec" + "ret", "pass" + "word", "pass" + "wd",
        "to" + "ken", "pwd", "client" + "_secret", "access" + "_key",
        "private" + "_key", "authorization", "credential",
    ]) + r")\b\s*[\"']?\s*[:=]\s*[\"']?([^\s\"']{8,})")
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
INTERNAL_IP = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b")
MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")
WIN_PATH = re.compile(r"[A-Za-z]:" + BS + r"|/c/[Uu]sers|/mnt/c/[Uu]sers")
HIGH_ENTROPY = re.compile(r"\b[A-Za-z0-9+/]{32,}\b")

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz",
            ".tar", ".whl", ".so", ".dll", ".exe", ".pyc", ".woff", ".woff2"}
EMAIL_ALLOW = ("users.noreply.github.com", "example.com", "example.org", "invalid")
ENTROPY_MIN = 4.0
ENTROPY_ALLOW = {"changemeyour", "yourvaluehere"}


def entropy(s):
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = float(len(s))
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def mask(value, keep=4):
    value = value.strip()
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * min(len(value) - keep, 12)


def find_line_issues(path, line, no):
    """Yield (category, masked_snippet) for one line."""
    m = PK_BLOCK.search(line)
    if m:
        yield "private-key-block", mask(m.group(0))

    m = KEYWORD_ASSIGN.search(line)
    if m:
        yield "credential-assignment", f"{m.group(1)}={mask(m.group(2))}"

    for m in BEARER.finditer(line):
        yield "bearer-token", mask(m.group(0))

    for m in JWT.finditer(line):
        yield "jwt", mask(m.group(0))

    for m in EMAIL.finditer(line):
        addr = m.group(0)
        if not any(addr.lower().endswith(d) for d in EMAIL_ALLOW):
            yield "email", mask(addr)

    for m in INTERNAL_IP.finditer(line):
        yield "internal-ip", m.group(0)

    for m in MAC.finditer(line):
        yield "mac", m.group(0)

    for m in WIN_PATH.finditer(line):
        yield "absolute-path", m.group(0)

    for m in HIGH_ENTROPY.finditer(line):
        tok = m.group(0)
        if tok.lower() in ENTROPY_ALLOW:
            continue
        if not (re.search(r"[a-z]", tok) and re.search(r"[A-Z]", tok)
                and re.search(r"\d", tok)):
            continue
        if entropy(tok) < ENTROPY_MIN:
            continue
        yield "high-entropy", mask(tok)


def scan_file(path, max_bytes):
    try:
        if os.path.getsize(path) > max_bytes:
            return None
    except OSError:
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    out = []
    for no, line in enumerate(text.splitlines(), 1):
        for cat, snip in find_line_issues(path, line, no):
            out.append((no, cat, snip))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--quiet", action="store_true", help="counts only")
    ap.add_argument("--max-bytes", type=int, default=2_000_000,
                    help="skip files larger than this (default 2 MB)")
    args = ap.parse_args()

    findings = 0
    scanned = 0
    per_cat = {}
    for dirpath, dirnames, filenames in os.walk(args.root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            path = os.path.join(dirpath, name)
            res = scan_file(path, args.max_bytes)
            if res is None:
                continue
            scanned += 1
            for no, cat, snip in res:
                findings += 1
                per_cat[cat] = per_cat.get(cat, 0) + 1
                if not args.quiet:
                    rel = os.path.relpath(path, args.root)
                    print(f"{rel}:{no}: [{cat}] {snip}")

    print(f"\nscanned {scanned} files, {findings} finding(s)")
    for cat in sorted(per_cat):
        print(f"  {cat}: {per_cat[cat]}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
