# Changelog

All notable changes to this project are documented here.
The format loosely follows Keep a Changelog; this project uses semantic
versioning once it leaves `0.x`.

## [0.1.0] - unreleased

### Added
- Initial public export.
- `scripts/cannlab_grab.py` - HTTP-only scarce-slot grabber: free-state probing,
  a white-list state machine, a bounded stuck-timeout escape hatch, a time window
  and a detached hand-off to the pipeline.
- `scripts/cannlab_auto.py` - detached full pipeline (ssh setup, upload, remote
  run, pull back, local verification, shut down) with a re-entry lock and a
  result file that is written on failure too.
- `scripts/cannlab_ssh.py` - fetch connection info + private key from the
  dev-env API and wire up an ssh config `Host` block.
- `scripts/cannlab_watch.py` - session watchdog that pulls results as soon as a
  remote `DONE` marker appears; silent while there is nothing to do.
- `scripts/cannlab_report.py` - report a pipeline result exactly once
  (fingerprint de-duplication, otherwise silent).
- `scripts/cannlab_stats_probe.py` - deterministic change-gate probe for any JSON
  stats feed; every identifier, field name and band size comes from flags or the
  environment.
- `scripts/check_secrets.py` - dependency-free secret / PII / absolute-path
  scanner (stdlib only) used as the pre-publish gate.
- `README.md` / `README.zh-CN.md` - positioning, architecture diagram, 3-step
  quickstart, full environment-variable reference, limitations, licence,
  disclaimer.
- `docs/SANITIZE_LOG.md` - what was removed before publishing, by category, plus
  the honest list of items not verified on a foreign machine.
- `docs/ascend-cann-field-notes.md` - de-identified methodology notes for
  automating a scarce, metered accelerator cloud environment.
- `examples/upload.sh.example`, `examples/run_all.sh.example` - templates for the
  two scripts the pipeline drives, documenting the contract (arguments, output
  layout, `STATUS.txt` keys, `DONE` marker).
- `examples/scheduler.md` - cron / Task Scheduler wiring, silent mode and an
  unattended-run checklist.

### Changed
- Renamed the change-gate probe from `cannlab_intel_probe.py` to
  `cannlab_stats_probe.py` and rewrote it to be fully generic: the tracked
  entity, its id and label fields, and every bucket size are supplied by flags or
  environment variables. No contest, team, organisation or person is referenced.
- README quickstart no longer needs any absolute path from a specific machine.

### Fixed
- `scripts/cannlab_watch.py` raised
  `SyntaxError: name 'SSH_HOST' is used prior to global declaration` at import
  time, so the script could not run at all. The `global` statement now appears
  before the argument defaults that read those module-level values. Caught by the
  pre-publish smoke test that runs `--help` on every script.

### Security
- All credentials are supplied by the operator (browser-exported cookie file /
  environment variables). Nothing is hard-coded, and no value is recorded in the
  repository or in the sanitize log.
- `.gitignore` blocks cookies, `.env`, secrets directories, state files, pipeline
  logs, result files, tarballs and backup litter.

### Known limitations
- Platform endpoints are undocumented defaults and may change; all are
  overridable.
- Verified on one environment only (Windows 11, Python 3.14) and **not verified
  on a clean or foreign machine** - see `docs/SANITIZE_LOG.md`.
- The detached-process path is implemented for Windows only.
- The end-to-end pipeline was not run from this export (it needs live
  credentials plus the two example scripts).
