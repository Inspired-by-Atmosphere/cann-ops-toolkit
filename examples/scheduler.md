# Wiring the jobs into a scheduler

The toolkit is deliberately split so that the **only** jobs on a scheduler are
short: the grabber, the watchdog, the reporter and the change probe. The long
pipeline (45+ minutes) is launched detached by the grabber and must never appear
in a schedule.

## The schedule

| Job | Interval | Command |
|---|---|---|
| Grabber | every 5–10 min | `python scripts/cannlab_grab.py --window 7-23` |
| Watchdog | every 10 min | `python scripts/cannlab_watch.py` |
| Reporter | every 10 min | `python scripts/cannlab_report.py` |
| Change probe | monitor mode | `python scripts/cannlab_stats_probe.py --cutoff-rank 30` |
| Pipeline | **never scheduled** | launched detached by the grabber |

All four are silent when there is nothing to say. Wire them to a channel that
only sends on non-empty output (see "Silent mode" below).

Suggested order of adoption: run `cannlab_grab.py --status` by hand first, then
the reporter, then the grabber, and only then the watchdog (which needs a working
ssh alias).

## POSIX cron

```cron
# every 6 minutes, 07:00-23:00 only
*/6 7-23 * * *  cd $HOME/cann-ops-toolkit && \
  CANNLAB_COOKIE_FILE=$HOME/.config/cannlab/cookie.cookie \
  /usr/bin/python3 scripts/cannlab_grab.py --window 7-23

*/10 * * * *  cd $HOME/cann-ops-toolkit && /usr/bin/python3 scripts/cannlab_report.py
*/10 * * * *  cd $HOME/cann-ops-toolkit && /usr/bin/python3 scripts/cannlab_watch.py
```

`cron` gives a minimal environment: export or inline **every** variable the
script needs. `cd` into the checkout first, because the default state/result
paths are resolved relative to the script or to the working directory.

## Windows Task Scheduler

Create one task per job, trigger = repeat every N minutes indefinitely, action =
"Start a program" with:

- Program: `<path to python.exe>` (substitute your own absolute interpreter path;
  this placeholder is intentional — no machine-specific path is shipped)
- Arguments: `scripts\cannlab_grab.py --window 7-23`
- Start in: the checkout directory
- **Do not** tick "Stop the task if it runs longer than…" for anything that
  launches the pipeline — but the grabber itself never needs more than a minute,
  so a short limit is a useful safety net there.

Environment variables can be set per task (or in the user's environment). Use
`schtasks /create /tn ... /tr ... /sc minute /mo 6` for a scriptable setup.

## Silent mode ("no output == no notification")

The scripts print nothing when nothing happened. Wire that to a channel that only
sends when stdout is non-empty:

- A runner with a "deliver only if there is output" mode (many agent runners
  have one) — empty stdout means nothing is delivered.
- `cron` + mail: `out="$(job)"; [ -n "$out" ] && printf '%s\n' "$out" | mail ...`
- A wrapper that pipes output into your notifier, exiting early when the output
  is empty.

This is what keeps a grabber that loses the race every few minutes all day from
becoming background noise.

## Ordering rules

1. Grabber before pipeline: the pipeline needs connection info that only exists
   while the instance is RUNNING.
2. Reporter after the pipeline: it reads the pipeline's result file. It is cheap
   and de-duplicated, so its interval does not matter much.
3. Watchdog and reporter are independent of each other; either may fire first.
4. Never schedule the pipeline directly, and never lengthen a tick to cover it.

## Checklist before leaving it unattended

- [ ] `cannlab_grab.py --status` prints the expected instance.
- [ ] `cannlab_ssh.py --setup` wrote the ssh alias and the link test passed.
- [ ] `cannlab_auto.py --dry` prints the planned steps with the right paths.
- [ ] `$CANNLAB_WORK_DIR/upload.sh` and the remote `run_all.sh` exist and agree
      on the remote path.
- [ ] The reporter has something to read: `cannlab_auto.result.json` appears
      after a pipeline run.
- [ ] You know how to release the environment by hand if the pipeline leaves it
      running on purpose (verification failed).
