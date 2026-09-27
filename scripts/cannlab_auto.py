#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cannlab_auto.py - full pipeline once a slot has been grabbed.

Chain: configure SSH -> upload the project -> run ``run_all.sh`` remotely
(build / smoke / shape sweep / profile / package) -> pull the run directory
back -> verify locally -> shut the environment down -> write a result file.

Design notes (why it looks like this)
-------------------------------------
* This runs for 45+ minutes, so it must NEVER sit inside a cron tick. It is
  launched as a detached process by ``cannlab_grab.py`` and writes its own log
  and result file; notification is a separate short cron job
  (``cannlab_report.py``).
* Results are written to the remote persistent disk first and the environment
  is shut down ONLY after the local verification passes - so we never end up
  with "hours burned, results lost".
* A lock file prevents re-entry (the cron grabber fires again every few
  minutes, it just exits).

Usage
-----
    python cannlab_auto.py           # run once (after a slot was grabbed)
    python cannlab_auto.py --dry     # print the planned steps only

Environment variables
---------------------
    CANNLAB_COOKIE_FILE    browser-exported cookie file
    CANNLAB_STATE_FILE     shared state json (written by cannlab_grab.py)
    CANNLAB_SECRETS_DIR    directory for lock/log/result files (default: next to the cookie file)
    CANNLAB_API_BASE       resources API base URL
    CANNLAB_RESOURCE_TYPE  resource type query value (default: cann)
    CANNLAB_WORK_DIR       local project dir holding upload.sh (default ./work)
    CANNLAB_REMOTE_WORK    matching remote dir (default /mnt/workspace/work)
    CANNLAB_SSH_ALIAS      ssh host alias to use (default: cannlab)
    CANNLAB_BUDGET_MIN     remote time budget in minutes (default 45)
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

import requests

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))


def _env(name, default=""):
    val = os.environ.get(name) or default
    return os.path.expanduser(os.path.expandvars(val))


COOKIE = _env("CANNLAB_COOKIE_FILE", "~/.config/cannlab/gitcode-cookie.cookie")
STATE = _env("CANNLAB_STATE_FILE", os.path.join(os.path.dirname(COOKIE), "cannlab_grab.state.json"))
SECRETS = _env("CANNLAB_SECRETS_DIR", os.path.dirname(COOKIE))
LOCK = os.path.join(SECRETS, "cannlab_auto.lock")
RESULT = os.path.join(SECRETS, "cannlab_auto.result.json")
LOG = os.path.join(SECRETS, "cannlab_auto.log")

API = _env("CANNLAB_API_BASE",
           "https://web-api.gitcode.com/score-proxy/api/v1/shop/third-party/resources")
TYPE = _env("CANNLAB_RESOURCE_TYPE", "cann")

WORK = _env("CANNLAB_WORK_DIR", os.path.join(HERE, "work"))
LOCAL_RESULTS = os.path.join(WORK, "results")
REMOTE_WORK = _env("CANNLAB_REMOTE_WORK", "/mnt/workspace/work")
ALIAS = _env("CANNLAB_SSH_ALIAS", "cannlab")
BUDGET_MIN = int(_env("CANNLAB_BUDGET_MIN", "45"))

PY = sys.executable or "python"

_logf = None


def log(msg):
    line = f"[{datetime.now():%F %T}] {msg}"
    print(line, flush=True)
    global _logf
    if _logf is None:
        os.makedirs(os.path.dirname(LOG) or ".", exist_ok=True)
        _logf = open(LOG, "a", encoding="utf-8")
    _logf.write(line + "\n")
    _logf.flush()


def sh(cmd, timeout=600, check=True):
    log("$ " + (cmd if isinstance(cmd, str) else " ".join(cmd)))
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                       text=True, timeout=timeout)
    tail = (r.stdout or "").strip()
    if tail:
        log(tail[-2000:])
    if r.stderr.strip():
        log("[stderr] " + r.stderr.strip()[-1200:])
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}): {cmd}")
    return r


def lock_ok():
    """Return False when another live pipeline holds the lock."""
    if os.path.exists(LOCK):
        try:
            info = json.load(open(LOCK, encoding="utf-8"))
            if time.time() - info.get("ts", 0) < 3 * 3600:
                log(f"a pipeline is already running (pid={info.get('pid')} "
                    f"since {info.get('at')}), exiting")
                return False
        except Exception:
            pass
    os.makedirs(os.path.dirname(LOCK) or ".", exist_ok=True)
    json.dump({"pid": os.getpid(), "ts": time.time(),
               "at": datetime.now().strftime("%F %T")},
              open(LOCK, "w", encoding="utf-8"))
    return True


def finish(ok, summary, steps):
    try:
        os.remove(LOCK)
    except Exception:
        pass
    json.dump({"ok": ok, "at": datetime.now().strftime("%F %T"),
               "summary": summary, "steps": steps},
              open(RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(("OK  " if ok else "FAIL ") + summary)


def headers():
    ck = open(COOKIE, encoding="utf-8").read().strip()
    return {"Cookie": ck, "User-Agent": "Mozilla/5.0", "Accept": "application/json",
            "Referer": "https://gitcode.com/", "Origin": "https://gitcode.com",
            "Content-Type": "application/json"}


def instance_key():
    try:
        return json.load(open(STATE, encoding="utf-8"))["instance_key"]
    except Exception:
        return None


def stop_env(key):
    try:
        h = headers()
        r = requests.post(f"{API}/{key}/stop?type={TYPE}", headers=h, timeout=30)
        log(f"stop request -> {r.status_code} {r.text[:120]}")
        time.sleep(4)
        d = requests.get(f"{API}/{key}?type={TYPE}", headers=h, timeout=25).json()
        log(f"state after stop: {d.get('status')} / {d.get('status_code')}")
    except Exception as e:
        log(f"stop failed (does not affect results): {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    steps = {}
    if args.dry:
        print("would run:")
        for s in [f"1. {PY} {os.path.join(HERE, 'cannlab_ssh.py')} --setup",
                  f"2. bash {os.path.join(WORK, 'upload.sh')} {ALIAS}",
                  f"3. ssh {ALIAS} 'bash {REMOTE_WORK}/run_all.sh {BUDGET_MIN}'",
                  f"4. scp the newest run_* back to {LOCAL_RESULTS}",
                  "5. local verification (non-empty logs + CSV)",
                  "6. POST /stop to end billing",
                  "7. write the result file -> cannlab_report.py announces it"]:
            print("   " + s)
        return 0

    if not lock_ok():
        return 0

    key = instance_key()
    log(f"===== pipeline start (instance {key}) =====")

    # ---- 1. SSH credentials (only available while RUNNING) ----
    try:
        sh([PY, os.path.join(HERE, "cannlab_ssh.py"), "--setup"], timeout=300)
        steps["ssh"] = "ok"
    except Exception as e:
        steps["ssh"] = "fail"
        finish(False, f"SSH setup failed, pipeline aborted: {str(e)[:200]}", steps)
        return 1

    # ---- 2. upload ----
    try:
        sh(["bash", os.path.join(WORK, "upload.sh"), ALIAS], timeout=1800)
        steps["upload"] = "ok"
    except Exception as e:
        steps["upload"] = "fail"
        finish(False, f"upload failed: {str(e)[:200]}", steps)
        stop_env(key)
        return 1

    # ---- 3. remote full run (budget + 10 min of slack) ----
    run_ok = True
    try:
        sh(["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30", ALIAS,
            f"bash {REMOTE_WORK}/run_all.sh {BUDGET_MIN}"],
           timeout=(BUDGET_MIN + 12) * 60)
        steps["run"] = "ok"
    except Exception as e:
        steps["run"] = "fail"
        run_ok = False
        log(f"remote run error (still trying to pull results): {str(e)[:300]}")

    # ---- 4. pull the newest run_* directory ----
    pulled = None
    try:
        r = sh(["ssh", "-o", "BatchMode=yes", ALIAS,
                f"ls -dt {REMOTE_WORK}/run_* 2>/dev/null | head -1"],
               timeout=120, check=False)
        remote_dir = (r.stdout or "").strip().splitlines()
        remote_dir = remote_dir[0].strip() if remote_dir else ""
        if not remote_dir:
            raise RuntimeError("no run_* directory on the remote side "
                               "(the build likely failed early)")
        local_dir = os.path.join(LOCAL_RESULTS, os.path.basename(remote_dir))
        os.makedirs(local_dir, exist_ok=True)
        sh(["scp", "-r", f"{ALIAS}:{remote_dir}/.", local_dir], timeout=1800)
        pulled = local_dir
        steps["pull"] = "ok"
    except Exception as e:
        steps["pull"] = "fail"
        log(f"pull failed: {str(e)[:300]}")

    # ---- 5. local verification: the payload must really have content ----
    verified = False
    detail = ""
    if pulled:
        files = []
        for root, _, fs in os.walk(pulled):
            for f in fs:
                p = os.path.join(root, f)
                try:
                    files.append((os.path.relpath(p, pulled), os.path.getsize(p)))
                except OSError:
                    pass
        total = sum(s for _, s in files)
        detail = f"{len(files)} files / {total / 1024:.0f} KB"
        has_data = any(s > 1024 for _, s in files)
        has_text = any(f.endswith((".csv", ".log", ".txt")) and s > 0 for f, s in files)
        verified = bool(has_data and has_text)
        steps["verify"] = f"{'ok' if verified else 'fail'} ({detail})"
        if not verified:
            log("local verification failed: payload empty or too small - "
                "keeping the environment alive for a human to inspect")
    else:
        steps["verify"] = "skip (nothing pulled)"

    # ---- 6. shut down only when verification passed ----
    if verified:
        stop_env(key)
        steps["stop"] = "ok"
    else:
        steps["stop"] = "skipped (protecting results, not shutting down)"

    # ---- 7. result ----
    ok = bool(verified) and run_ok
    if ok:
        msg = (f"pipeline finished, payload {detail}, pulled back and shut down.\n"
               f"results: {pulled}")
    elif verified:
        msg = (f"payload pulled back ({detail}) but the remote run had errors; "
               f"environment shut down.\nresults: {pulled}")
        ok = True
    else:
        msg = (f"pipeline produced no usable results (run={steps.get('run')}, "
               f"pull={steps.get('pull')}, verify={steps.get('verify')}).\n"
               f"environment left running, log: {LOG}")
    finish(ok, msg, steps)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
