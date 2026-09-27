# Field notes: automating a metered accelerator cloud environment

De-identified method notes behind this toolkit. Nothing here refers to a
particular user, team, contest, project or workload — the point is the method,
which transfers to any "scarce, metered, slow to obtain, long to use" resource.

## 1. Find the state that costs nothing

The core contradiction of grabbing a scarce resource: **you must probe often,
but probing must not consume the resource.** Enumerate the resource's state
machine first, then ask of each state: *if I send a write request now, does the
meter run?* There is almost always one state — usually "shut down" — in which a
probe is free.

- Do: poll `POST /resources/{id}/start` every few minutes while the environment
  is shut down. "Pool full" comes back as a normal `400`; nothing was created,
  nothing was billed.
- Do not: a create → fail → delete loop. It is slower, it burns quota, and it
  looks like abuse to the platform's risk controls.

Pair the probe with a **time window** (`--window 7-23`): winning a slot at 03:00
when nobody is awake to use it is the same as burning hours.

## 2. Never mistake "accepted" for "allocated"

Asynchronous allocation is two-phase: **accepted, then allocated.** A `200` with
`PENDING` means the request entered a queue, not that hardware exists. Allocation
can silently fall through into an error state afterwards.

The fix is to find the field that **only exists once the resource is really
yours**, and to require it:

```
success := status == RUNNING  AND  instance_id is non-empty
```

A status name alone can be fooled by an accepted-but-queued state. A non-empty
resource id cannot be produced by a queue entry.

Corollaries:

- When the state machine lands in an *unknown/broken* state, go back to the
  clean, free state first (one `stop`), then start again next tick. Trying to
  start from a broken state returns a "no resources" error that looks like a
  full pool but is really a wrong predecessor state — a very expensive
  misreading.
- If the attempt did not work out, return the resource to the free state. Never
  leave a metered resource running because the script got confused.

## 3. A white list, not a black list

Write requests are sent **only** for a terminal state the script explicitly
recognises. Everything else — in-flight, new, renamed, unknown — is left alone.

Why it matters: a black-list version ("only protect the states I know about")
canceled a booting environment the first time the platform introduced a new
state name. Unknown means "do nothing", not "probably fine to touch".

Because "unknown" can also mean "wedged", add a **bounded** escape hatch that
requires *all three*: (1) not running, (2) no resource id allocated, and
(3) the same `(status, resource_id, used_duration)` fingerprint unchanged for
N minutes. (1)+(2) rule out "it is actually working"; (3) rules out "it was
only just submitted". Only then is one `stop` cheap.

## 4. Three processes, one job each — the long task is never in a tick

A 45-minute pipeline inside a scheduler tick will be killed by the tick timeout
or overlap the next tick. Split it:

| Component | Trigger | Job |
|---|---|---|
| Grabber | scheduler, every 1–10 min | probe, send one start, verify, launch the long job **detached**, exit |
| Pipeline | launched detached | do the whole thing, log as it goes, always write a result file (including on failure) |
| Reporter | scheduler, every few minutes | read the result file, report **once** (fingerprint de-dup), otherwise print nothing |
| Change probe | scheduler monitor mode | print a deterministic snapshot of external numbers |

Detached launch on Windows:

```python
subprocess.Popen([sys.executable, script], stdin=subprocess.DEVNULL,
                 stdout=log, stderr=log,
                 creationflags=subprocess.DETACHED_PROCESS
                              | subprocess.CREATE_NEW_PROCESS_GROUP,
                 close_fds=True)
```

Add a lock file: the scheduler will fire again a few minutes later and must find
"already running" and exit quietly.

## 5. Change gating: a snapshot only fires when it really moved

Watching external numbers (capacity, a cut line, a rule) with "run an agent every
N minutes and write a digest" produces a fixed stream of noise. Instead the probe
prints a snapshot and the scheduler compares it byte-for-byte with the previous
tick; the downstream job only wakes on a real change.

This forces two properties on the probe:

- **Deterministic output.** No timestamps, no randomness, fixed decimal places,
  stable ordering. One timestamp and every tick counts as a change.
- **Coarse numbers by default.** Exact values for anything mid-table are worse
  than useless — neighbours move constantly. Bucket them: round the cut line to
  the nearest half point, the item count to the nearest 25, the rank to the
  nearest 5. Keep our own figure exact, because it only moves when we act, and
  that is exactly the change worth waking someone for.

A failing probe must still print something deterministic (`probe=error`) — a
flapping endpoint must not look like new information.

## 6. Artefact discipline: persistent disk first, real verification, then release

A resource paid for in hours must never end with the artefact still on the
machine that is about to be released.

1. Write results to the **persistent** mounted volume (survives shutdown), not
   to a scratch path that dies with the instance.
2. Pull them back, then **verify content**, not the transfer return code: file
   count, total bytes, at least one non-empty text/CSV, at least one file above
   a sane size floor.
3. Release only after verification passes. If verification fails, **do not**
   release — leave it up for a human. "Hours burned, results lost" is the worst
   possible outcome; "hours burned, environment left up" is merely annoying.
4. Keep the **upload path and the run path identical** (`/mnt/workspace/work`
   on both sides). A mismatch fails late — at build time — and is expensive to
   diagnose.

## 7. Shipping logs and hiding noise

- Silence means "nothing worth a human". In a scheduler's `no_agent` mode an
  empty stdout sends no notification at all — a grabber that lost the race
  should say nothing, every few minutes, all day.
- De-duplicate announcements by content fingerprint so one result is announced
  exactly once even if the reporter runs 100 times.
- Always write a result file — success *and* failure. A silent death leaves no
  evidence and no notification.

## 8. Getting a shell: connection info and keys from the API

The development environment's own API hands out what ssh needs, but only while
the instance is actually running:

```
GET {gateway}/.../instances?devenv_type=1
GET {gateway}/.../instances/connect-urls/{id}?source=...
GET {gateway}/.../instances/private-keys/{id}?source=...
auth: Bearer <token taken from the browser session cookie>
```

While the instance is not running these answer `400` with a "not ready" error
code — that is expected, not a bug, and the script should say so instead of
dumping a traceback. Parse defensively (the exact field names are not
guaranteed) and print the raw response when parsing fails; the private key may
arrive either bare or wrapped in an object.

Write the key with owner-only permissions, then rewrite the matching `Host`
block in the ssh config (delete the old block for that alias, append the new
one) so the alias always points at the current instance.

## 9. Compliance boundary

Do not route around a platform control that explicitly blocks you (a gateway
`403` on a hidden test set is an answer, not an obstacle). Reaching an
application-level error means the path and the permissions are right — there is
nothing to gain by going deeper, and an account flagged as abusive costs far
more than the information was worth. Automate **your own** entitled access, and
respect the platform's quota and acceptable-use rules.

## 10. Troubleshooting checklist

| Symptom | First thing to check |
|---|---|
| "no resources" though capacity exists | Predecessor state. Return to the free state, then retry. |
| Grabbed, then lost the slot | You treated `200`/`PENDING` as success. Require the allocated resource id. |
| Environment canceled while booting | Black-list state machine touch an unrecognised state. Switch to a white list. |
| "Uploaded but not found" at build time | Local and remote paths differ. Make them the same string. |
| Checker fires every tick with no real change | Probe output is not deterministic (usually a timestamp). |
| Scheduler killed the run mid-way | The long job lives inside a tick. Detach it. |
| Released the environment, results gone | Released before verifying, or wrote to a non-persistent path. |
