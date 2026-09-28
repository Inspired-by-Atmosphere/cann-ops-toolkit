# cann-ops-toolkit

Small, dependency-light scripts that **grab a scarce accelerator slot, run a long
build/profile pipeline unattended, pull the results back, and release the slot** —
written for an Ascend/NPU cloud dev-environment shop with metered accelerator hours.

## Quick look

```bash
python -m pip install -r requirements.txt      # `requests`, nothing else
export CANNLAB_COOKIE_FILE="$HOME/.config/cannlab/cookie.cookie"
python scripts/cannlab_grab.py --status        # read-only: prints state, takes no slot
```

`--status` performs one read request and writes no state — it is the safe way to
confirm your cookie file works before letting anything run on a schedule. Full
setup, wiring and configuration table: see *Quickstart* and *Configuration* below.

## Why this exists

When the resource you need is scarcer than the work itself, three things decide
whether you get anything done:

1. **You must poll often** — a free slot disappears in seconds.
2. **Polling must not burn the resource** — an idle, shut-down environment costs
   nothing; a running one costs hours.
3. **A 45-minute pipeline must never live inside a scheduler tick** — it gets
   killed, or it overlaps the next tick.

This toolkit is the answer to those three constraints, split into jobs that each
do one thing: a **grabber**, a **detached pipeline**, a **watchdog**, a
**once-only reporter**, and a **change-gate probe** for external numbers.

## How it fits together

```
cannlab_grab.py          scheduler, every few minutes  (cheap, must be quick)
  │  detects a free slot, fires one start request,
  │  and only trusts "RUNNING + non-empty instance id" as success
  └─▶ cannlab_auto.py   detached process, 45+ min  (never inside a tick)
        ssh setup ─▶ upload ─▶ remote run_all.sh ─▶ pull results back
        ─▶ verify locally ─▶ shut the slot down ─▶ write a result file
  │
cannlab_watch.py         scheduler  - pulls results as soon as a remote DONE marker exists
cannlab_report.py        scheduler  - reads the result file and reports it exactly once
cannlab_stats_probe.py   scheduler  - deterministic snapshot of an external JSON feed
cannlab_ssh.py           on demand  - fetch connection info + private key, wire up ssh config
check_secrets.py         pre-publish gate - secret / PII / absolute-path scanner (stdlib only)
```

Design rules shared by every script:

- **Silent by default.** No output means "nothing worth telling a human". In a
  scheduler's `no_agent` mode an empty stdout sends no notification at all.
- **Never trust an accepted request.** A `200` means "queued", not "allocated".
- **White-list state machine.** A write request is only sent for a state that is
  explicitly recognised; every unknown or in-flight state is left alone.
- **Credentials are never in the code.** A browser-exported cookie file (plus
  environment variables) supplies everything.

## Repository layout

```
cann-ops-toolkit/
├── README.md / README.zh-CN.md
├── LICENSE                     MIT
├── requirements.txt            requests
├── docs/
│   ├── SANITIZE_LOG.md         what was removed before publishing, by category
│   └── ascend-cann-field-notes.md   de-identified methodology notes
├── examples/
│   ├── upload.sh.example       local -> remote sync template
│   ├── run_all.sh.example      remote build/smoke/sweep/package template
│   └── scheduler.md            how to wire the jobs into cron / Task Scheduler
└── scripts/
    ├── cannlab_grab.py
    ├── cannlab_auto.py
    ├── cannlab_watch.py
    ├── cannlab_report.py
    ├── cannlab_ssh.py
    ├── cannlab_stats_probe.py
    └── check_secrets.py
```

## Quickstart (3 steps)

```bash
# 1. install (Python 3.9+; only `requests` is needed)
python -m pip install -r requirements.txt

# 2. point the tools at YOUR session, never a value committed to a repo
export CANNLAB_COOKIE_FILE="$HOME/.config/cannlab/cookie.cookie"

# 3. look before you leap: print state only, request nothing, take no slot
python scripts/cannlab_grab.py --status
```

`--status` performs one read request and writes no state. Once it prints a
sensible instance, wire up the jobs (`examples/scheduler.md`) and let the
grabber run.

Exporting the cookie file is on you: log in to the platform in a browser, copy
the request `Cookie:` header of an authenticated API call into that file. The
toolkit never asks for a password and never stores one.

## Configuration

Everything is an environment variable (flags win where both exist). **No values
are shipped in this repository.**

### Grabber — `cannlab_grab.py`

| Variable | Default | Meaning |
|---|---|---|
| `CANNLAB_COOKIE_FILE` | `~/.config/cannlab/gitcode-cookie.cookie` | browser-exported cookie file |
| `CANNLAB_TOKEN_COOKIE` | `GITCODE_ACCESS_TOKEN` | cookie name that carries the access token |
| `CANNLAB_STATE_FILE` | next to the cookie file | state json (de-duplication) |
| `CANNLAB_API_BASE` | the platform resources endpoint | resources API base URL |
| `CANNLAB_RESOURCE_TYPE` | `cann` | resource type query value |
| `CANNLAB_TARGET_NAME` | first instance | `instance_name` to prefer |
| `CANNLAB_AUTO_SCRIPT` | `scripts/cannlab_auto.py` | pipeline launched after a real grab |
| `CANNLAB_AUTO_LOG` | next to the cookie file | log the detached pipeline writes to |

Flags: `--status`, `--window 7-23`, `--force`, `--reset`, `--stuck-minutes N`.

### Pipeline — `cannlab_auto.py`

| Variable | Default | Meaning |
|---|---|---|
| `CANNLAB_COOKIE_FILE` | see above | cookie file |
| `CANNLAB_STATE_FILE` | next to the cookie file | state json written by the grabber |
| `CANNLAB_SECRETS_DIR` | cookie directory | lock / log / result files |
| `CANNLAB_API_BASE` | platform resources endpoint | resources API base URL |
| `CANNLAB_RESOURCE_TYPE` | `cann` | resource type query value |
| `CANNLAB_WORK_DIR` | `scripts/work` | local project dir holding `upload.sh` |
| `CANNLAB_REMOTE_WORK` | `/mnt/workspace/work` | matching remote dir |
| `CANNLAB_SSH_ALIAS` | `cannlab` | ssh host alias to use |
| `CANNLAB_BUDGET_MIN` | `45` | remote time budget in minutes |

Flag: `--dry` (print the planned steps, touch nothing).

### SSH helper — `cannlab_ssh.py`

| Variable | Default | Meaning |
|---|---|---|
| `CANNLAB_COOKIE_FILE` | see above | cookie file |
| `CANNLAB_TOKEN_COOKIE` | `GITCODE_ACCESS_TOKEN` | token cookie name |
| `CANNLAB_STATE_FILE` | next to the cookie file | state json written by the grabber |
| `CANNLAB_SSH_ALIAS` | `cannlab` | host alias written into the ssh config |
| `CANNLAB_SSH_KEY` | `~/.ssh/cannlab_key` | private key output path |
| `CANNLAB_SSH_CONFIG` | `~/.ssh/config` | ssh config file to update |
| `CANNLAB_GATEWAY` | platform dev-env gateway | dev-env API gateway |
| `CANNLAB_SOURCE` | `cannlab` | `source` query value |

Flags: `--setup`, `--raw`, `--ssh`, `--run "cmd"`.

### Watchdog — `cannlab_watch.py`

| Variable | Default | Meaning |
|---|---|---|
| `CANNLAB_SSH_CONFIG` | `~/.ssh/config` | ssh config holding the host |
| `CANNLAB_SSH_HOST` | first concrete `Host` entry | host alias to use |
| `CANNLAB_REMOTE_WORK` | `/mnt/workspace/work` | remote work dir |
| `CANNLAB_DEST` | `./results` | local directory for pulled results |
| `CANNLAB_STATE_FILE` | `scripts/cannlab_watch.state` | run de-duplication state |

Flags: `--host`, `--ssh-config`, `--remote-work`, `--dest`, `--state`.

### Reporter — `cannlab_report.py`

| Variable | Default | Meaning |
|---|---|---|
| `CANNLAB_SECRETS_DIR` | script directory | holds the result and "seen" files |
| `CANNLAB_RESULT_FILE` | `<secrets>/cannlab_auto.result.json` | result json written by the pipeline |

Flags: `--result-file`, `--seen`.

### Change-gate probe — `cannlab_stats_probe.py`

| Variable | Default | Meaning |
|---|---|---|
| `CANNLAB_STATS_URL` | *(none — required)* | JSON stats endpoint |
| `CANNLAB_PROBE_ID_FIELD` | `id` | field holding the unique id |
| `CANNLAB_PROBE_ID` | *(none)* | id of the entity to track |
| `CANNLAB_PROBE_NAME` | *(none)* | label to track if the id does not match |

Flags: `--url`, `--track-id`, `--track-name`, `--id-field`, `--name-field`,
`--score-field`, `--pass-field`, `--cutoff-rank`, `--rank-band`, `--items-band`,
`--cut-band`, `--timeout`.

## Limitations

- **Undocumented platform endpoints.** The API paths baked in as defaults were
  reverse-engineered from a browser session against one deployment. They can
  change without notice and were never a public contract. Every URL is
  overridable (`CANNLAB_API_BASE`, `CANNLAB_GATEWAY`, `CANNLAB_STATS_URL`), and
  the scripts degrade to a warning instead of a traceback.
- **Verified on one environment only.** The scripts were exercised on Windows 11
  with Python 3.14 against a reference deployment. **They have not been verified
  on a clean or foreign machine** — see `docs/SANITIZE_LOG.md` for the exact list
  of unverified items.
- **Cookie-based auth only.** There is no OAuth or API-key path. A login flow
  change breaks the toolkit until the cookie name is updated
  (`CANNLAB_TOKEN_COOKIE`).
- **`cannlab_auto.py` needs two files that are not shipped as runnable code** —
  `$CANNLAB_WORK_DIR/upload.sh` and a remote `run_all.sh`. `examples/` contains
  templates describing the contract (arguments, expected variables, output
  layout); the end-to-end pipeline was **not** run from this export.
- **The detached-process flags are Windows-specific.** On POSIX, `fork` +
  `setsid` would be the equivalent; this path is not implemented.
- **History files grow forever.** The grabber's state json and the reporter's
  "seen" file are never pruned.
- **No automated tests are included.** Verification is manual: `--help` on every
  script plus a dry run of the pipeline.
- **No GPU/NPU work is done here.** These scripts drive a remote environment and
  shuttle artefacts; the actual operator/kernel toolchain lives elsewhere.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

These tools automate *your own* authenticated access to a service you are
entitled to use. You are responsible for complying with that service's terms of
service, quota rules and acceptable-use policy. Do not use this toolkit to
bypass quotas, circumvent access controls, or share an account. The authors
provide it as-is, with no warranty, and accept no liability for how you use it.
