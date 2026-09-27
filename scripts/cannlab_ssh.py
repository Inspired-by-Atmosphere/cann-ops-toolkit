#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cannlab_ssh.py - fetch connection info + SSH private key from the dev-env API.

How it works (verified against the reference platform)
------------------------------------------------------
    gateway  https://dev.gitcode.com/api/v1/proxy/vscode
    base     /DevContainerService/open-api-inner/v1/devenv
        GET  {base}/instances?devenv_type=1
        GET  {base}/instances/connect-urls/{id}?source=cannlab    <- connection info
        GET  {base}/instances/private-keys/{id}?source=cannlab    <- ssh private key
    auth: use the token from the browser session cookie as ``Bearer``.

    While the instance is NOT running those endpoints answer
    ``400 {"error_msg":"...","error_code":"HD.98340004"}`` - they only work once
    the slot has actually been allocated (RUNNING).

Usage
-----
    python cannlab_ssh.py --setup        # write ~/.ssh/config and test the link
    python cannlab_ssh.py --raw          # dump raw API responses (troubleshooting)
    python cannlab_ssh.py --ssh          # open an interactive ssh session
    python cannlab_ssh.py --run "cmd"    # run one command on the remote host

Environment variables
---------------------
    CANNLAB_COOKIE_FILE    browser-exported cookie file
    CANNLAB_TOKEN_COOKIE   cookie name carrying the access token (default GITCODE_ACCESS_TOKEN)
    CANNLAB_STATE_FILE     state json written by cannlab_grab.py
    CANNLAB_SSH_ALIAS      ssh host alias to write (default: cannlab)
    CANNLAB_SSH_KEY        private key output path (default ~/.ssh/cannlab_key)
    CANNLAB_SSH_CONFIG     ssh config to update (default ~/.ssh/config)
    CANNLAB_GATEWAY        dev-env API gateway (default https://dev.gitcode.com/api/v1/proxy/vscode)
    CANNLAB_SOURCE         ``source`` query value (default: cannlab)
"""
import argparse
import json
import os
import subprocess
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")


def _env(name, default=""):
    val = os.environ.get(name) or default
    return os.path.expanduser(os.path.expandvars(val))


COOKIE = _env("CANNLAB_COOKIE_FILE", "~/.config/cannlab/gitcode-cookie.cookie")
TOKEN_COOKIE = _env("CANNLAB_TOKEN_COOKIE", "GITCODE_ACCESS_TOKEN")
STATE = _env("CANNLAB_STATE_FILE", os.path.join(os.path.dirname(COOKIE), "cannlab_grab.state.json"))

GATEWAY = _env("CANNLAB_GATEWAY", "https://dev.gitcode.com/api/v1/proxy/vscode")
BASE = f"{GATEWAY}/DevContainerService/open-api-inner/v1/devenv"
SOURCE = _env("CANNLAB_SOURCE", "cannlab")

HOST_ALIAS = _env("CANNLAB_SSH_ALIAS", "cannlab")
KEY_PATH = _env("CANNLAB_SSH_KEY", "~/.ssh/cannlab_key")
SSH_DIR = os.path.dirname(KEY_PATH)
CONFIG = _env("CANNLAB_SSH_CONFIG", "~/.ssh/config")


def token():
    if not os.path.exists(COOKIE):
        print(f"WARN: no cookie file at {COOKIE} - log in to the platform first.")
        sys.exit(1)
    ck = open(COOKIE, encoding="utf-8").read()
    for part in ck.split(";"):
        part = part.strip()
        if part.startswith(TOKEN_COOKIE + "="):
            return part.split("=", 1)[1]
    print(f"WARN: cookie file has no {TOKEN_COOKIE} entry (session expired?).")
    sys.exit(1)


def headers():
    return {
        "Authorization": "Bearer " + token(),
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "https://gitcode.com/",
    }


def instance_key():
    try:
        with open(STATE, encoding="utf-8") as f:
            k = json.load(f).get("instance_key")
        if k:
            return k
    except Exception:
        pass
    r = requests.get(f"{BASE}/instances?devenv_type=1", headers=headers(), timeout=25)
    js = r.json().get("result") or {}
    items = js.get("content") or []
    if not items:
        print("WARN: no dev-env instance on this account.")
        sys.exit(1)
    return items[0]["id"]


def fetch(key):
    h = headers()
    out = {}
    for name, url in (
        ("connect", f"{BASE}/instances/connect-urls/{key}?source={SOURCE}"),
        ("key", f"{BASE}/instances/private-keys/{key}?source={SOURCE}"),
    ):
        r = requests.get(url, headers=h, timeout=30)
        out[name] = {"status": r.status_code, "text": r.text}
    return out


def dig(d, *names):
    """Find the first non-empty value for any of ``names`` in a nested structure.

    Used because the exact response field names are not guaranteed; the script
    prints the raw response when it cannot find what it needs.
    """
    if isinstance(d, dict):
        for n in names:
            v = d.get(n)
            if isinstance(v, (str, int)) and str(v).strip():
                return str(v)
        for v in d.values():
            got = dig(v, *names)
            if got:
                return got
    elif isinstance(d, list):
        for v in d:
            got = dig(v, *names)
            if got:
                return got
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--ssh", action="store_true")
    ap.add_argument("--run", default=None, help="run one command on the remote host")
    args = ap.parse_args()

    key = instance_key()
    print(f"instance: {key}")

    data = fetch(key)

    if args.raw:
        for n, v in data.items():
            print(f"\n=== {n} -> HTTP {v['status']} ===")
            print(v["text"][:1500])
        return 0

    for n, v in data.items():
        if v["status"] != 200:
            print(f"WARN: {n} endpoint returned {v['status']}: {v['text'][:200]}")
            print("   -> usually means the environment is not RUNNING yet.")
            return 1

    try:
        cj = json.loads(data["connect"]["text"]).get("result") or {}
        kj = json.loads(data["key"]["text"]).get("result") or {}
    except Exception as e:
        print("WARN: response is not the expected JSON, use --raw:", e)
        return 1

    host = dig(cj, "host", "hostname", "hostName", "ip")
    port = dig(cj, "port", "ssh_port", "sshPort") or "22"
    user = dig(cj, "username", "user", "ssh_user", "sshUser") or "developer"
    proxy = dig(cj, "proxy_command", "proxyCommand", "proxy")

    print(f"connect: {user}@{host}:{port}")
    if proxy:
        print(f"ProxyCommand: {proxy}")
    if not host:
        print("WARN: could not parse a host, raw response follows:")
        print(data["connect"]["text"][:1500])
        return 1

    # The private key may be a bare PEM or wrapped in an object.
    pem = dig(kj, "private_key", "privateKey", "key", "content") or ""
    if "BEGIN" not in pem:
        print("WARN: could not parse a private key, raw response follows:")
        print(data["key"]["text"][:1500])
        return 1

    os.makedirs(SSH_DIR, exist_ok=True)
    with open(KEY_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(pem if pem.endswith("\n") else pem + "\n")
    try:
        os.chmod(KEY_PATH, 0o600)
    except Exception:
        pass
    print(f"private key written: {KEY_PATH}")

    # Write / update the Host block in the ssh config.
    os.makedirs(os.path.dirname(CONFIG) or ".", exist_ok=True)
    block = [f"Host {HOST_ALIAS}",
             f"    HostName {host}",
             f"    Port {port}",
             f"    User {user}",
             f"    IdentityFile {KEY_PATH}",
             "    ServerAliveInterval 30"]
    if proxy:
        block.insert(1, f"    ProxyCommand {proxy}")
    text = ""
    if os.path.exists(CONFIG):
        old = open(CONFIG, encoding="utf-8").read()
        keep, skip = [], False
        for line in old.splitlines():
            if line.strip().startswith("Host "):
                skip = (line.split(None, 1)[1].strip() == HOST_ALIAS)
            if not skip:
                keep.append(line)
        text = "\n".join(keep).rstrip() + "\n"
    text += "\n".join(block) + "\n"
    with open(CONFIG, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"updated {CONFIG} (Host {HOST_ALIAS})")

    if args.ssh:
        os.execvp("ssh", ["ssh", HOST_ALIAS])
    if args.run:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST_ALIAS, args.run],
                           capture_output=True, text=True, timeout=300)
        print("--- stdout ---")
        print(r.stdout[-4000:])
        if r.stderr.strip():
            print("--- stderr ---")
            print(r.stderr[-1500:])
        return r.returncode

    print("\ntesting connectivity...")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
                        HOST_ALIAS, "uname -a; id; ls /mnt/workspace"],
                       capture_output=True, text=True, timeout=120)
    print(r.stdout.strip() or "(no output)")
    if r.returncode != 0:
        print("WARN: ssh failed:")
        print(r.stderr[-1200:])
        return r.returncode
    print("\nSSH is up, ready to upload and run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
