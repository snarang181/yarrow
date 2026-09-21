#!/usr/bin/env python3
"""Validate the mutation benchmark.

For the golden RTL and every mutant: build with Verilator, run the detection
suite, and check the invariants:
  - golden passes every config
  - every mutant compiles and fails at least one config (is 'killed')

Writes bench/kill_matrix.json and prints a summary table.
Builds and suite runs are parallelized across a small worker pool.
"""
import concurrent.futures as cf
import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "scripts" / "build_sim.sh"
RUN_SUITE = ROOT / "scripts" / "run_suite.py"
WORK = ROOT / "bench" / ".validate_work"


def build_and_run(name: str, rtl: pathlib.Path) -> dict:
    objdir = WORK / f"obj_{name}"
    b = subprocess.run([str(BUILD), str(rtl), str(objdir)],
                       capture_output=True, text=True)
    if b.returncode != 0:
        return {"name": name, "compiled": False, "killed_by": [],
                "error": (b.stdout + b.stderr)[-800:]}
    r = subprocess.run([sys.executable, str(RUN_SUITE), str(objdir / "sim"),
                        "--json"], capture_output=True, text=True)
    data = json.loads(r.stdout)
    killed_by = [x["name"] for x in data["results"] if not x["passed"]]
    return {"name": name, "compiled": True, "killed_by": killed_by,
            "results": data["results"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mutants", nargs="?", default=ROOT / "bench" / "mutants",
                    type=pathlib.Path)
    ap.add_argument("--golden", type=pathlib.Path,
                    default=ROOT / "rtl" / "cache_ctrl.sv",
                    help="golden RTL to validate (default: rtl/cache_ctrl.sv)")
    a = ap.parse_args()
    mutants = a.mutants
    WORK.mkdir(parents=True, exist_ok=True)
    jobs = [("golden", a.golden)]
    for d in sorted(mutants.iterdir()):
        if (d / "cache_ctrl.sv").exists():
            jobs.append((d.name, d / "cache_ctrl.sv"))

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda j: build_and_run(*j), jobs))

    matrix = {r["name"]: r for r in results}
    # bench/mutants -> bench/kill_matrix.json (the file gen_causal_labels reads);
    # other tiers -> bench/kill_matrix_<dir>.json
    name = "kill_matrix.json" if mutants.name == "mutants" else f"kill_matrix_{mutants.name}.json"
    (mutants.parent / name).write_text(json.dumps(matrix, indent=2) + "\n")

    ok = True
    g = matrix["golden"]
    if not g["compiled"] or g["killed_by"]:
        print(f"ERROR golden: compiled={g['compiled']} failing={g['killed_by']}")
        ok = False
    else:
        print("golden: clean (passes all configs)")

    print(f"\n{'mutant':36s} {'killed by (#configs)':>20s}")
    for r in results:
        if r["name"] == "golden":
            continue
        if not r["compiled"]:
            print(f"{r['name']:36s} {'BUILD FAILED':>20s}")
            ok = False
        elif not r["killed_by"]:
            print(f"{r['name']:36s} {'SURVIVED (not detected)':>20s}")
            ok = False
        else:
            print(f"{r['name']:36s} {len(r['killed_by']):>3d}/8  "
                  f"{','.join(r['killed_by'][:3])}"
                  f"{'...' if len(r['killed_by']) > 3 else ''}")

    print("\nbenchmark:", "VALID" if ok else "INVALID")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
