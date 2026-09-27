#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cannlab_grab.py - poll a cloud NPU shop API for a free slot (pure HTTP).

Why it is written this way
--------------------------
* An environment in the SHUTDOWN state burns no accelerator hours. So "waiting
  for a slot" is nothing more than a cheap ``POST /resources/{id}/start`` every
  few minutes:
      pool full -> 400 {"error_message": "..."}   <- normal, stay silent
      accepted  -> 200 {"status": "PENDING"}      <- accepted != allocated
* The ONLY trustworthy success signal is ``status == RUNNING`` **and** a
  non-empty ``npu_instance_id``. A 200 response only means "request accepted";
  the allocation can silently fall through to an error state afterwards.
* White-list principle: a write request is sent only for a state we explicitly
  recognise. Every unknown / in-flight state is left untouched. A previous
  black-list version ("only protect the states I know about") cancelled an
  ``OPENING`` environment mid-boot the moment the platform introduced that new
  state name.
* Silent by default: no output == nothing worth notifying. With a cron
  ``no_agent`` job an empty stdout means "do not deliver".

Credentials never live in this file. Point the script at a cookie file that a
logged-in browser session exported (``--cookie-file`` or
``$CANNLAB_COOKIE_FILE``).

Usage
-----
    python cannlab_grab.py                    # poll once inside the window
    python cannlab_grab.py --status           # print state only, do nothing
    python cannlab_grab.py --window 7-23      # only poll 07:00-23:00
    python cannlab_grab.py --force            # ignore the time window
    python cannlab_grab.py --reset            # clear the "grabbed" marker

Environment variables (flags win where both exist)
--------------------------------------------------
    CANNLAB_COOKIE_FILE    browser-exported cookie file (default ~/.config/cannlab/gitcode-cookie.cookie)
    CANNLAB_TOKEN_COOKIE   cookie name that carries the access token (default GITCODE_ACCESS_TOKEN)
    CANNLAB_STATE_FILE     state json path (default: next to the cookie file)
    CANNLAB_API_BASE       resources API base URL
    CANNLAB_RESOURCE_TYPE  resource type query value (default: cann)
    CANNLAB_TARGET_NAME    instance_name to prefer (default: first instance)
    CANNLAB_AUTO_SCRIPT    pipeline script to launch after a real grab
    CANNLAB_AUTO_LOG       log file the detached pipeline writes to
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
TOKEN_COOKIE = _env("CANNLAB_TOKEN_COOKIE", "GITCODE_ACCESS_TOKEN")
STATE = _env("CANNLAB_STATE_FILE", os.path.join(os.path.dirname(COOKIE), "cannlab_grab.state.json"))
AUTO_LOG = _env("CANNLAB_AUTO_LOG", os.path.join(os.path.dirname(COOKIE), "cannlab_auto.log"))
AUTO_SCRIPT = _env("CANNLAB_AUTO_SCRIPT", os.path.join(HERE, "cannlab_auto.py"))
API = _env("CANNLAB_API_BASE",
           "https://web-api.gitcode.com/score-proxy/api/v1/shop/third-party/resources")
TYPE = _env("CANNLAB_RESOURCE_TYPE", "cann")
TARGET_NAME = _env("CANNLAB_TARGET_NAME", "")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://gitcode.com/",
    "Origin": "https://gitcode.com",
    "Content-Type": "application/json",
}

RUNNING = ("RUNNING", "running")
# Status values observed on the reference platform:
#   2=PROCESSING 3=OPENING 4=SHUTDOWN 5=SHUTTING_DOWN 9=UNKNOWN(broken)


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def launch_auto():
    """Start the long pipeline as a detached process.

    The pipeline runs for tens of minutes and must never live inside a cron
    tick; the grabber only fires and forgets.
    """
    if not os.path.exists(AUTO_SCRIPT):
        return False
    try:
        flags = 0
        if os.name == "nt":
            flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        os.makedirs(os.path.dirname(AUTO_LOG) or ".", exist_ok=True)
        with open(AUTO_LOG, "ab") as lf:
            subprocess.Popen([sys.executable, AUTO_SCRIPT], stdin=subprocess.DEVNULL,
                             stdout=lf, stderr=lf, creationflags=flags, close_fds=True)
        return True
    except Exception:
        return False


def headers():
    if not os.path.exists(COOKIE):
        print("WARN: no cookie file at %s - the grabber cannot talk to the API."
              % COOKIE)
        sys.exit(0)
    ck = open(COOKIE, encoding="utf-8").read().strip()
    if TOKEN_COOKIE not in ck:
        print("WARN: cookie file has no %s entry (session likely expired)."
              % TOKEN_COOKIE)
        sys.exit(0)
    h = dict(HEADERS)
    h["Cookie"] = ck
    return h


def find_env(h, state):
    """Return the target resource dict, preferring the id stored in state."""
    d = requests.get(f"{API}?type={TYPE}&page=1&per_page=100", headers=h, timeout=25).json()
    items = d.get("content") or []
    key = state.get("instance_key")
    if key:
        for it in items:
            if it.get("instance_key") == key:
                return it
    if TARGET_NAME:
        for it in items:
            if it.get("instance_name") == TARGET_NAME:
                return it
    return items[0] if items else None


def detail(h, key):
    return requests.get(f"{API}/{key}?type={TYPE}", headers=h, timeout=25).json()


def in_window(spec):
    """``7-23`` = hours, left closed right open. ``22-7`` wraps midnight."""
    try:
        a, b = spec.split("-")
        a, b = int(a), int(b)
    except Exception:
        return True
    hr = datetime.now().hour
    if a <= b:
        return a <= hr < b
    return hr >= a or hr < b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="0-24",
                    help="hours in which grabbing is allowed, e.g. 7-23. "
                         "Default 0-24: the pipeline is fully automatic, so a "
                         "slot won at night still runs and shuts down by itself.")
    ap.add_argument("--force", action="store_true", help="ignore the time window")
    ap.add_argument("--status", action="store_true", help="print state only")
    ap.add_argument("--reset", action="store_true", help="clear the grabbed marker")
    ap.add_argument("--stuck-minutes", type=float, default=20.0,
                    help="bounded timeout: if an in-flight status keeps an "
                         "unchanged fingerprint for this many minutes while no "
                         "resource is actually held, send one stop to force the "
                         "environment back to SHUTDOWN (default 20; 0 disables)")
    args = ap.parse_args()

    state = load_state()

    if args.reset:
        state.pop("grabbed_at", None)
        save_state(state)
        print("Cleared the 'already grabbed' marker; the next run will try again.")
        return 0

    h = headers()

    env = find_env(h, state)
    if not env:
        print("WARN: no resource found on this account (deleted?).")
        return 0
    key = env["instance_key"]
    state["instance_key"] = key
    save_state(state)

    d = detail(h, key)
    status = (d.get("status") or "").upper()

    if args.status:
        print(f"name  : {d.get('instance_name')} ({key})")
        print(f"spec  : {d.get('spec_name')}")
        print(f"status: {d.get('status')} (code={d.get('status_code')}, "
              f"detail={d.get('status_detail')})")
        print(f"used  : {d.get('used_duration')} | expires: {d.get('expire_time')}")
        print(f"ide   : {d.get('vscode_jump_url')}")
        return 0

    # Already running - nothing to grab.
    if status in RUNNING:
        if not state.get("grabbed_at"):
            state["grabbed_at"] = datetime.now().strftime("%F %T")
            state["status"] = status
            save_state(state)
            print(f"[grab] environment is already running ({d.get('instance_name')})")
            print("You can connect now. Shut it down when done "
                  "(the platform also auto-stops an idle environment).")
        return 0

    # Grabbed before - do not occupy repeatedly / burn hours.
    if state.get("grabbed_at"):
        return 0

    if not args.force and not in_window(args.window):
        return 0

    # WHITE-LIST principle (learned the hard way): only act on terminal states
    # we explicitly recognise; anything unknown is left alone.
    if status == "UNKNOWN":
        # Broken state: return to the clean state first, do not start this tick.
        state["status"] = f"reset-from({status})"
        state["last_try"] = datetime.now().strftime("%F %T")
        save_state(state)
        try:
            requests.post(f"{API}/{key}/stop?type={TYPE}", headers=h, timeout=30)
        except Exception:
            pass
        return 0

    if status != "SHUTDOWN":
        # Anything else (OPENING/PROCESSING/PENDING/SHUTTING_DOWN/new names):
        # the platform is busy -> send nothing, look again next tick.
        #
        # The single exception is a bounded timeout that requires ALL THREE:
        #   (1) status is not RUNNING            (excluded above)
        #   (2) npu_instance_id is empty         (nothing allocated)
        #   (3) the same (status, npu_instance_id, used_duration) fingerprint has
        #       been unchanged for >= stuck_minutes   (not just submitted)
        # (1)+(2) rule out "it is actually running"; (3) rules out "just queued".
        # Rationale: a PROCESSING that hangs for 90+ minutes with an empty
        # npu_instance_id and a frozen used_duration really holds nothing, so
        # cancelling is ~free - while leaving it there stalls forever.
        sig = f"{status}|{d.get('npu_instance_id')}|{d.get('used_duration')}"
        if state.get("stuck_sig") != sig:
            state["stuck_sig"] = sig
            state["stuck_since"] = datetime.now().strftime("%F %T")
            save_state(state)
        elif args.stuck_minutes > 0 and not d.get("npu_instance_id"):
            try:
                held = (datetime.now() - datetime.strptime(
                    state.get("stuck_since"), "%Y-%m-%d %H:%M:%S")).total_seconds() / 60.0
            except Exception:
                held = 0.0
            if held >= args.stuck_minutes:
                state["status"] = f"stuck-reset({status},{held:.0f}m)"
                state.pop("stuck_sig", None)
                state.pop("stuck_since", None)
                save_state(state)
                try:
                    requests.post(f"{API}/{key}/stop?type={TYPE}", headers=h, timeout=30)
                except Exception:
                    pass
                return 0

        state["status"] = f"wait({status})"
        save_state(state)
        return 0

    # SHUTDOWN: clean starting point -> fire one start request.
    state["last_try"] = datetime.now().strftime("%F %T")
    state["attempts"] = state.get("attempts", 0) + 1
    # Every new request must clear the hang timer: the new request's
    # fingerprint may be byte-identical to the previous one, which would
    # otherwise be misread as "hung for a long time" and reset immediately.
    state.pop("stuck_sig", None)
    state.pop("stuck_since", None)
    save_state(state)

    r = requests.post(f"{API}/{key}/start?type={TYPE}", headers=h, timeout=30)
    if r.status_code != 200:
        # "not enough resources" is the normal case: stay silent.
        if "\u8d44\u6e90\u4e0d\u8db3" in r.text or r.status_code == 400:
            state["status"] = "pool-full"
            save_state(state)
            return 0
        print(f"WARN: grab error HTTP {r.status_code} {r.text[:200]}")
        return 0

    # 200 + PENDING is only "request accepted", not "slot allocated".
    # The only trustworthy success signal is status=RUNNING with a non-empty
    # npu_instance_id. Do one short confirmation poll for the fast path; the
    # slow queueing path is handled by the next tick (never send stop here -
    # that would cancel our own request).
    got = None
    for _ in range(3):
        time.sleep(10)
        dd = detail(h, key)
        st = (dd.get("status") or "").upper()
        if st in RUNNING and dd.get("npu_instance_id"):
            got = dd
            break
        if st == "UNKNOWN" or dd.get("status_code") in (9,):
            break

    if not got:
        try:
            st_now = (detail(h, key).get("status") or "").upper()
        except Exception:
            st_now = "?"
        state["status"] = f"submitted({st_now})"
        save_state(state)
        return 0

    state["grabbed_at"] = datetime.now().strftime("%F %T")
    state["status"] = "RUNNING"
    save_state(state)
    launched = launch_auto()
    print(f"*** slot grabbed at {datetime.now():%m-%d %H:%M} ***")
    print(f"name  : {d.get('instance_name')} | spec: {d.get('spec_name')}")
    print(f"state : RUNNING | npu: {got.get('npu_instance_id')}")
    if launched:
        print("pipeline launched: ssh creds -> upload -> build -> smoke -> "
              "shape sweep -> profile -> pull back -> verify -> shut down")
        print(f"    log: {AUTO_LOG}")
    else:
        print("WARN: could not launch the pipeline, run it manually: "
              f"{AUTO_SCRIPT}")
    print("NOTE: accelerator hours are burning from now on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
