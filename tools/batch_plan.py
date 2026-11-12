#!/usr/bin/env python3
"""Developer batch driver: plan the albums of several artist folders, three looked up at once
(as the app does), and save each album's plan on its own (OUTDIR/<artist>/plan_<n>.json), ready
to check (tagmender check PLAN) or apply one by one. Read-only: it plans, it never applies.

  tools/batch_plan.py --profile NAME --root LIBRARY_ROOT OUTDIR ARTIST...

ARTIST is a folder name below LIBRARY_ROOT. Progress is appended to OUTDIR/progress.log.
Not part of the shipped app."""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402
import plan as planmod  # noqa: E402
import profiles as pf  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", required=True, help="the profile to plan with")
    ap.add_argument("--root", required=True, help="the library folder the ARTIST folders are in")
    ap.add_argument("outdir", metavar="OUTDIR", help="where the plans and progress.log go")
    ap.add_argument("artists", nargs="+", metavar="ARTIST")
    args = ap.parse_args(argv)
    os.makedirs(args.outdir, exist_ok=True)
    log = os.path.join(args.outdir, "progress.log")
    prof = pf.load(args.profile)
    for artist in args.artists:
        path = os.path.join(args.root, artist)
        od = os.path.join(args.outdir, "".join(c if c.isalnum() else "_" for c in artist))
        os.makedirs(od, exist_ok=True)
        t0 = time.time()
        try:
            e = engine.Engine(prof)
            e.lookup_workers = 3
            keys = [engine.group_key(g.folder, g.files) for g in e.albums_in(path)]
            final = {}
            e.plan_each(path, keys, lambda k, p: final.__setitem__(k, p))
            for n, k in enumerate(keys):
                p = final.get(k)
                if p is None:
                    continue
                p = dict(p)
                p["job"] = f"{p.get('job') or 'batch'}-{n}"
                planmod.save(p, os.path.join(od, f"plan_{n:03}.json"))
            msg = f"{artist}: {len(keys)} albums, {len(final)} planned"
        except Exception as exc:                          # one artist failing must not stop the batch
            msg = f"{artist}: FAILED {exc!r}"
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M')} {msg} {int(time.time() - t0)}s\n")
    with open(log, "a", encoding="utf-8") as f:
        f.write("DONE\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
