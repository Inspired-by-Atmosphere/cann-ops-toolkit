# Sanitize log — cann-ops-toolkit

Record of what was removed / rewritten before this repository was exported, and
what was deliberately kept. Format: **category | count | action**.

Per project policy, **no original values are recorded here** — not even hashes.
Counts refer to the pre-publish audit of the staging tree plus the edits applied
by the publish task.

| Category | Count | Action |
|---|---|---|
| Credentials (API keys, tokens, passwords, private keys, sudo password) | 0 found | No hard-coded credential existed. Every script already took its credentials from a browser-exported cookie file plus environment variables; the README documents **variable names only**. |
| Cookie / session material in tracked files | 0 found | `.gitignore` blocks `*.cookie`, `.env`, `secrets/`, `*state*.json`, `*.state`, the pipeline log and the result json. Nothing of that shape is tracked. |
| Personal identity (real name, nickname, e-mail, phone, QQ/WeChat id, student id) | 0 found | Nothing personal appears anywhere in the tree. The only author string in the repository is the GitHub-noreply pseudonym in `LICENSE`. |
| Organisation / school / team names | 0 found | Rewritten out or never included. No team name, no school, no company, no project-internal codename remains. |
| Absolute user paths | 3 replaced | Default paths that pointed at one machine's home directory were replaced by `~/.config/...` style defaults and by `$HOME` in the README. Every one is overridable by environment variable. |
| Absolute project paths (drive-letter paths) | 0 found | Nothing referenced an absolute project path. Scripts resolve their own location at runtime (`__file__`) and take work directories from `$CANNLAB_WORK_DIR`. |
| Contest material (problem/operator names, scores, ranks, submission ids, judge-account names) | 1 file neutralised | One probe script carried contest-flavoured naming (`team` / `team id` / leaderboard wording). It was rewritten to a fully generic feed probe (`scripts/cannlab_stats_probe.py`), with the tracked entity, its id field, its name field and every band size supplied as flags/environment. No contest name, operator name, score, rank, team id or judge account is present. |
| Internal network topology (private IPv4, MAC, hostnames, router names, internal domains) | 0 found | No private address, MAC or internal hostname exists in the tree. |
| Platform endpoint URLs | 3 kept (documented) | The public service base URLs are kept as defaults because the toolkit is useless without them; see "Deliberate keeps" below. All three are overridable. |
| Unrunnable / out-of-scope scripts | — see below | Not included; reasons listed in "Not included" below. |

## Deliberate keeps

1. **Public platform endpoints as *defaults*.** The resource API base, the
   dev-environment gateway and the resource type value default to the public
   service that the toolkit targets. They are not secrets, they identify the
   platform rather than any user, and each is overridable
   (`CANNLAB_API_BASE`, `CANNLAB_GATEWAY`, `CANNLAB_RESOURCE_TYPE`). A user of a
   different deployment can point all of them elsewhere without editing code.
2. **The `cannlab_` script prefix and `CANNLAB_` environment prefix.** Kept so
   the components are recognisable as one family. They name the *platform*, not
   a user, a team or a contest.
3. **The `GITCODE_ACCESS_TOKEN` default cookie name.** It is the cookie name the
   platform uses, not a value. The token itself never leaves the operator's
   browser-exported cookie file.
4. **`scripts/check_secrets.py`.** Kept although it is tooling rather than a
   product feature: it is the very gate that produced the rows above, and it is
   useful to anyone forking this repository.

## Not included

| Item | Reason |
|---|---|
| `upload.sh` (local → remote sync) | Machine-specific: it embedded one operator's local project layout. Replaced by `examples/upload.sh.example`, a template that takes the ssh alias as an argument. |
| `run_all.sh` (remote build / smoke / sweep / profile / package) | Project-specific: it drove one particular operator/kernel project and referenced material that is not open source. Replaced by `examples/run_all.sh.example`, which documents the contract (arguments, output layout, the `STATUS.txt` keys and the `DONE` marker) that `cannlab_watch.py` and `cannlab_auto.py` rely on. |
| Any benchmark result, log, profile or dataset | Belongs to a private workload; not required to use the toolkit. |
| Any script that could not run standalone | Not included. Every tracked script imports only the standard library plus `requests`, and takes all configuration from flags/environment. |
| `cannlab_intel_probe.py` | Superseded by the neutralised `cannlab_stats_probe.py` (renamed, so no stale reference is left behind). |

## Unverified on a foreign machine — stated honestly

The following were verified **only** on the author's environment (Windows 11,
Python 3.14) or **not at all**. They are the honest "未测 / not tested" list:

| Item | Status |
|---|---|
| `--help` / argument parsing for every tracked script | Verified (exit code 0, output recorded in the publish gate). |
| `cannlab_grab.py` against a live platform session | Not tested in this export: needs real credentials. Behaviour documented in the skill's case notes. |
| `cannlab_ssh.py --setup` (private-key fetch + ssh config rewrite) | Not tested in this export: needs real credentials and a RUNNING instance. |
| `cannlab_auto.py` end-to-end | Not tested: needs real credentials, an ssh alias and the two template scripts. Only `--dry` was exercised. |
| `cannlab_watch.py` pull path (`scp` + extract) | Not tested: needs a reachable remote host. The `--help` path (exit 0) and the "nothing to do" paths (missing ssh config / unreachable host, exit 0, no output) were exercised. **The export smoke test caught a `SyntaxError` in this script that made it unrunnable; it is fixed** and `--help` now exits 0. |
| `cannlab_report.py` de-duplication over two real results | Partially tested: the silent/no-file path was exercised; the two-run de-duplication path was not. |
| `cannlab_stats_probe.py` against a real feed | Not tested: needs a real endpoint. The no-URL path was exercised. |
| `check_secrets.py` on trees other than this one | Verified on this staging tree (19 scanned, 0 findings) — see the publish gate report. |
| POSIX (Linux/macOS) run of the detached pipeline | Not tested: the detached-process path is implemented for Windows only. |
| Any run from a clean clone on a machine that never had these files before | **Not tested.** This is the biggest caveat of the export. |
