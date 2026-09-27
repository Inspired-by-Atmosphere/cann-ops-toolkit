#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cannlab_watch.py - session watchdog: pull results as soon as they are ready.

Duties
------
1. Detect whether the dev-env instance is reachable (via an ssh config file).
2. As soon as the remote run writes ``DONE``, pull the result tarball back and
   verify it.
3. Only print when something actually happened, so it can run from a scheduler
   in "no output == no notification" mode and never spams.

It deliberately does NOT shut the machine down: the platform stops an idle
environment by itself, while the real billing stop is the console's "shut down"
button. Telling a human to press it is safer than an OS-level shutdown, which
may leave the platform still counting the instance as running.

Environment variables
---------------------
    CANNLAB_SSH_CONFIG     ssh config holding the instance host (default ~/.ssh/config)
    CANNLAB_SSH_HOST       use this host alias directly (otherwise the first
                           concrete Host entry in the config is used)
    CANNLAB_REMOTE_WORK    remote work dir (default /mnt/workspace/work)
    CANNLAB_DEST           local directory for pulled results (default ./results)
    CANNLAB_STATE_FILE     state file used to de-duplicate runs (default ./cannlab_watch.state)
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import datetime


def _env(name, default=""):
    val = os.environ.get(name) or default
    return os.path.expanduser(os.path.expandvars(val))


SSH_CONFIG = _env("CANNLAB_SSH_CONFIG", "~/.ssh/config")
SSH_HOST = _env("CANNLAB_SSH_HOST", "")
STATE = _env("CANNLAB_STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "cannlab_watch.state"))
DEST = _env("CANNLAB_DEST", os.path.join(os.getcwd(), "results"))
REMOTE_WORK = _env("CANNLAB_REMOTE_WORK", "/mnt/workspace/work")


def read_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def write_state(s):
    os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        f.write(s)


def find_host():
    """Take the first concrete Host entry from the dev-env ssh config."""
    if SSH_HOST:
        return SSH_HOST
    if not os.path.exists(SSH_CONFIG):
        return None
    try:
        with open(SSH_CONFIG, encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        return None
    hosts = re.findall(r"^\s*Host\s+(.+)$", txt, flags=re.M)
    for h in hosts:
        for name in h.split():
            if "*" in name or "?" in name:
                continue
            return name
    return None


def ssh(host, cmd, timeout=25):
    return subprocess.run(
        ["ssh", "-F", SSH_CONFIG, "-o", "BatchMode=yes",
         "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new",
         host, cmd],
        capture_output=True, text=True, timeout=timeout,
    )


def main():
    global SSH_HOST, SSH_CONFIG, REMOTE_WORK, DEST, STATE

    ap = argparse.ArgumentParser(
        description="Pull results from a remote dev-env session as soon as its "
                    "run writes a DONE marker. Silent unless something happened.")
    ap.add_argument("--host", default=SSH_HOST,
                    help="ssh host alias (default: first concrete Host in the config)")
    ap.add_argument("--ssh-config", default=SSH_CONFIG, help="ssh config file to use")
    ap.add_argument("--remote-work", default=REMOTE_WORK,
                    help="remote work dir (default /mnt/workspace/work)")
    ap.add_argument("--dest", default=DEST, help="local results directory")
    ap.add_argument("--state", default=STATE, help="de-duplication state file")
    args = ap.parse_args()

    SSH_HOST, SSH_CONFIG, REMOTE_WORK, DEST, STATE = (
        args.host, args.ssh_config, args.remote_work, args.dest, args.state)

    host = find_host()
    if not host:
        # Never connected yet - stay silent (do not disturb).
        return 0

    try:
        r = ssh(host, f"test -f {REMOTE_WORK}/DONE && cat {REMOTE_WORK}/STATUS.txt "
                      f"|| echo __NO_DONE__")
    except Exception:
        # Instance unreachable (stopped / not started) - stay silent.
        return 0

    out = (r.stdout or "").strip()
    if not out or "__NO_DONE__" in out:
        return 0

    info = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()

    stamp = info.get("RUN_STAMP", "unknown")
    if read_state() == stamp:
        return 0  # this run was already handled

    tarball = info.get("TARBALL", f"{REMOTE_WORK}/results_{stamp}.tar.gz")
    os.makedirs(DEST, exist_ok=True)
    local_tar = os.path.join(DEST, os.path.basename(tarball))

    cp = subprocess.run(
        ["scp", "-F", SSH_CONFIG, "-o", "StrictHostKeyChecking=accept-new",
         "-o", "ConnectTimeout=15", f"{host}:{tarball}", local_tar],
        capture_output=True, text=True, timeout=600,
    )

    lines = [f"[run finished] {datetime.now():%m-%d %H:%M}",
             f"make exit code = {info.get('MAKE_RC', '?')} (0 = build ok)"]
    smoke = info.get("SMOKE", "")
    if smoke:
        lines.append(f"smoke: {smoke[:80]}")

    if cp.returncode == 0 and os.path.exists(local_tar) and os.path.getsize(local_tar) > 0:
        size_mb = os.path.getsize(local_tar) / 1024 / 1024
        lines.append(f"results pulled back ({size_mb:.2f} MB)")
        lines.append(f"-> {local_tar}")
        sha = subprocess.run(["sha256sum", local_tar], capture_output=True, text=True)
        if sha.returncode == 0:
            lines.append("sha256 " + sha.stdout.split()[0][:16] + "...")
        try:
            subprocess.run(["tar", "xzf", local_tar, "-C", DEST],
                           capture_output=True, text=True, timeout=120)
            lines.append(f"extracted to {DEST}")
        except Exception:
            pass
        lines.append("-> now release the environment from the console "
                     "(an idle instance is also auto-stopped after a while)")
        write_state(stamp)
    else:
        lines.append(f"pull failed: {(cp.stderr or '')[:200]}")
        lines.append("(the instance may already be stopped; the next run will retry)")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
