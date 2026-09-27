#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cannlab_stats_probe.py - deterministic change-gate probe for a JSON stats feed.

Consumed by a scheduler's "monitor" mode: the probe runs once per tick, its
output is compared byte for byte with the previous tick, and the downstream job
(for example an agent) is only woken when the output actually changed.

Consequences of that contract:
  * The output MUST be fully deterministic - no timestamps, no randomness,
    fixed number of decimals. Otherwise every tick counts as "changed" and the
    gate is useless.
  * Only the few numbers that really matter are included. Dumping a whole table
    would fire on every neighbour's tiny move.

Point it at any endpoint that returns a list of objects with a score-ish field:

    GET <CANNLAB_STATS_URL>
    -> [{"id": ..., "name": ..., "score": ..., "passCount": ...}, ...]

Every identifier is supplied through the environment or flags; no contest, team,
organisation or person is referenced anywhere in this file.

Environment variables
---------------------
    CANNLAB_STATS_URL       full URL of the stats endpoint (required to do anything)
    CANNLAB_PROBE_ID_FIELD  field holding the unique id (default: id)
    CANNLAB_PROBE_ID        id of the entity to track (optional)
    CANNLAB_PROBE_NAME      label to track if the id does not match (optional)

Usage
-----
    python cannlab_stats_probe.py --cutoff-rank 30
    python cannlab_stats_probe.py --track-id 4242 --score-field score
    python cannlab_stats_probe.py --rank-band 5 --items-band 25
"""
import argparse
import os
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")


def _env(name, default=""):
    return os.environ.get(name) or default


def main():
    ap = argparse.ArgumentParser(
        description="Print a deterministic snapshot of a JSON stats endpoint so "
                    "a change gate can ignore ticks that did not move.")
    ap.add_argument("--url", default=_env("CANNLAB_STATS_URL", ""),
                    help="stats endpoint (default $CANNLAB_STATS_URL)")
    ap.add_argument("--track-id", default=_env("CANNLAB_PROBE_ID", ""),
                    help="id of the entity to track (default $CANNLAB_PROBE_ID)")
    ap.add_argument("--track-name", default=_env("CANNLAB_PROBE_NAME", ""),
                    help="label to track if the id does not match "
                         "(default $CANNLAB_PROBE_NAME)")
    ap.add_argument("--id-field", default=_env("CANNLAB_PROBE_ID_FIELD", "id"),
                    help="field holding the unique id (default: id)")
    ap.add_argument("--name-field", default="name",
                    help="field holding the display label (default: name)")
    ap.add_argument("--cutoff-rank", type=int, default=30,
                    help="rank whose score is tracked as the cut line (default 30)")
    ap.add_argument("--score-field", default="score", help="score field name")
    ap.add_argument("--pass-field", default="passCount",
                    help="secondary sort field (default passCount)")
    ap.add_argument("--rank-band", type=int, default=5,
                    help="bucket size for our rank, to avoid firing on tiny moves")
    ap.add_argument("--items-band", type=int, default=25,
                    help="bucket size for the number of entries")
    ap.add_argument("--cut-band", type=float, default=0.5,
                    help="bucket size for the cut-line score")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    if not args.url:
        print("error=no-url")
        print("hint: set $CANNLAB_STATS_URL or pass --url")
        return 2

    try:
        r = requests.get(args.url, timeout=args.timeout)
        r.raise_for_status()
        rows = r.json()
    except Exception:
        # A failed probe must still print something deterministic, otherwise
        # every tick looks like a change and the downstream job is woken all
        # night by a flapping endpoint.
        print("probe=error")
        return 0

    if not isinstance(rows, list) or not rows:
        print("probe=empty")
        return 0

    def num(x, field):
        try:
            return float(x.get(field) or 0)
        except (TypeError, ValueError):
            return 0.0

    rows = sorted(rows, key=lambda x: (num(x, args.score_field), num(x, args.pass_field)),
                  reverse=True)

    ours = None
    for i, it in enumerate(rows, 1):
        if args.track_id and str(it.get(args.id_field)) == args.track_id:
            ours = (i, it)
            break
        if args.track_name and it.get(args.name_field) == args.track_name:
            ours = (i, it)
            break

    top1 = num(rows[0], args.score_field) if rows else 0.0
    cut = (num(rows[args.cutoff_rank - 1], args.score_field)
           if len(rows) >= args.cutoff_rank else 0.0)

    # All numbers are deliberately coarse: mid-table moves constantly, and an
    # exact snapshot would wake the downstream job all night. Our own score is
    # kept exact - it only changes when we submit, which is exactly the kind of
    # change worth waking up for.
    cut_band = round(cut / args.cut_band) * args.cut_band
    items_band = (len(rows) // args.items_band) * args.items_band

    print(f"items~{items_band}")
    if ours:
        i, it = ours
        print(f"our_rank_band~{(i // args.rank_band) * args.rank_band}")
        print(f"our_score={num(it, args.score_field):.2f}")
    else:
        print("our_rank=not_found")
    print(f"top1_band~{round(top1)}")
    print(f"cutoff-{args.cutoff_rank}_band~{cut_band:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
