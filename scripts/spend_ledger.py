#!/usr/bin/env python3
"""Metered Vertex spend attributed to a billing period, with a hard cap.

Sums `usage.cost_usd` (Vertex list prices, as recorded per cell by the driver)
over every runs/*/<mutant>/result.json whose mtime is >= --since, i.e. cells
that actually executed after the given moment. This attributes spend to the
billing account linked at that time even when a run directory spans both
periods (a resumed run re-running old error rows).

Metering has historically read ABOVE the real charge (it said $321.87 when
Google cut the first account off at $300.00), so it is a conservative guard.

    spend_ledger.py --since 2026-09-14T00:00Z [--cap 250] [--quiet]

Exit status 0 under the cap, 2 at/over the cap (the watchdog keys on this).
"""
import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="ISO-8601, e.g. 2026-09-14T00:00Z")
    ap.add_argument("--cap", type=float, default=None, help="metered USD cap for the period")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    since = dt.datetime.fromisoformat(a.since.replace("Z", "+00:00")).timestamp()

    per_run = {}
    for rj in (ROOT / "runs").glob("*/*/result.json"):
        if rj.stat().st_mtime < since:
            continue
        try:
            s = json.loads(rj.read_text())["summary"]
        except Exception:
            continue
        c = (s.get("usage") or {}).get("cost_usd", 0) or 0
        per_run.setdefault(rj.parts[-3], [0.0, 0])
        per_run[rj.parts[-3]][0] += c
        per_run[rj.parts[-3]][1] += 1
    total = sum(v[0] for v in per_run.values())
    if not a.quiet:
        for run, (c, n) in sorted(per_run.items(), key=lambda kv: -kv[1][0]):
            print(f"  {run:32} {n:3d} cells  ${c:8.2f}")
        cap = f" of ${a.cap:.2f} cap ({100*total/a.cap:.0f}%)" if a.cap else ""
        print(f"METERED since {a.since}: ${total:.2f}{cap}")
    if a.cap is not None and total >= a.cap:
        if not a.quiet:
            print("CAP REACHED", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
