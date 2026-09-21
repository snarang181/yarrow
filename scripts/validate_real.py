#!/usr/bin/env python3
"""VALID gate for the real-bug tier (bench/real, docs D6).

For every bench/real/<id>/: build golden and buggy with the design's table
testbench, run every table on both, and require
  - golden PASSES every table,
  - buggy FAILS at least one table (it is 'killed').
Writes bench/kill_matrix_real.json (same shape as the other kill matrices:
{id: {compiled, killed_by, results: [{name, passed, returncode, wall_s,
output_tail}], golden: [...]}}) and exits non-zero on any violation.
Builds run in a small worker pool; the 522k-row zipcpu table takes seconds.
"""
import concurrent.futures as cf
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from yarrow.design import Design  # noqa: E402

WORK = ROOT / "bench" / ".validate_real_work"
BUILD = ROOT / "scripts" / "build_design.sh"


def build(design: Design, variant: str) -> tuple:
    out = WORK / design.id / f"obj_{variant}"
    src_dir = design.root / ("rtl" if variant == "golden" else "rtl_buggy")
    srcs = [str(src_dir / s) for s in design.sources]
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run([str(BUILD), str(out), str(design.tb_cpp), design.top] + srcs,
                       capture_output=True, text=True)
    return (p.returncode == 0, str(out / "sim"), (p.stdout + p.stderr)[-800:])


def run_tables(design: Design, sim: str) -> list:
    rows = []
    for t in design.tables:
        t0 = time.time()
        p = subprocess.run([sim, f"+table={design.root / 'tb' / t['file']}"],
                           capture_output=True, text=True, timeout=300)
        rows.append({"name": t["name"], "passed": p.returncode == 0,
                     "returncode": p.returncode, "wall_s": round(time.time() - t0, 2),
                     "output_tail": (p.stdout + p.stderr).strip()[-300:]})
    return rows


def check(design: Design) -> dict:
    ok_g, sim_g, log_g = build(design, "golden")
    ok_b, sim_b, log_b = build(design, "buggy")
    entry = {"name": design.id, "compiled": ok_g and ok_b, "golden": [], "results": [],
             "killed_by": [], "build_log": "" if (ok_g and ok_b) else (log_g + log_b)}
    if not entry["compiled"]:
        return entry
    entry["golden"] = run_tables(design, sim_g)
    entry["results"] = run_tables(design, sim_b)
    entry["killed_by"] = [r["name"] for r in entry["results"] if not r["passed"]]
    return entry


def main() -> int:
    global WORK
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", type=pathlib.Path, default=ROOT / "bench" / "real")
    ap.add_argument("--matrix", type=pathlib.Path, default=None,
                    help="default bench/kill_matrix_<bench name>.json")
    a = ap.parse_args()
    tier = a.bench.name
    WORK = ROOT / "bench" / f".validate_{tier}_work"
    matrix_path = a.matrix or (ROOT / "bench" / f"kill_matrix_{tier}.json")
    designs = [Design.load(d) for d in sorted(a.bench.iterdir())
               if (d / "design.json").exists()]
    WORK.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        entries = list(ex.map(check, designs))
    matrix = {e["name"]: e for e in entries}
    matrix_path.write_text(json.dumps(matrix, indent=2) + "\n")

    bad = []
    print(f"{'id':22} {'compiled':>8} {'golden':>10} {'buggy':>10}  first failure")
    for e in entries:
        g_ok = e["compiled"] and all(r["passed"] for r in e["golden"])
        killed = bool(e["killed_by"])
        first = next((r["output_tail"].splitlines()[0] for r in e["results"] if not r["passed"]), "")
        print(f"{e['name']:22} {str(e['compiled']):>8} "
              f"{('PASS' if g_ok else 'FAIL') + f' {len(e['golden'])}/{len(e['golden'])}':>10} "
              f"{('killed' if killed else 'ALIVE'):>10}  {first[:70]}")
        if not (e["compiled"] and g_ok and killed):
            bad.append(e["name"])
    print(f"\n{len(entries) - len(bad)}/{len(entries)} valid in {time.time() - t0:.1f}s")
    if bad:
        print("INVALID:", ", ".join(bad))
        return 1
    print("real-bug tier: VALID")
    return 0


if __name__ == "__main__":
    sys.exit(main())
