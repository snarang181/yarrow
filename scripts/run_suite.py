#!/usr/bin/env python3
"""Run the detection suite (bench/suite.json) against a built sim binary.

Usage: run_suite.py <sim_binary> [--json] [--config NAME]
Exit 0 if every config passes, 1 otherwise.
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITE = ROOT / "bench" / "suite.json"
WALL_TIMEOUT_S = 120  # per config; sim watchdog usually fires first


def run_config(sim: str, cfg: dict) -> dict:
    args = [sim] + [f"+{k}={v}" for k, v in cfg["args"].items()]
    t0 = time.time()
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=WALL_TIMEOUT_S)
        rc, out = p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        rc, out = 3, "FAIL: wall-clock timeout"
    return {
        "name": cfg["name"],
        "passed": rc == 0,
        "returncode": rc,
        "wall_s": round(time.time() - t0, 2),
        "output_tail": out[-500:],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sim")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--config", help="run only this named config")
    ap.add_argument("--configs", help="comma-separated list of config names")
    ap.add_argument("--suite", type=pathlib.Path, default=SUITE,
                    help="suite.json to use (default: bench/suite.json; "
                         "tier-R sandboxes pass their own)")
    a = ap.parse_args()

    suite = json.loads(a.suite.read_text())["configs"]
    want = None
    if a.config:
        want = {a.config}
    elif a.configs:
        want = {c.strip() for c in a.configs.split(",") if c.strip()}
    if want is not None:
        suite = [c for c in suite if c["name"] in want]
        missing = want - {c["name"] for c in suite}
        if missing:
            print(f"no such config(s): {sorted(missing)}", file=sys.stderr)
            return 2

    results = [run_config(a.sim, c) for c in suite]
    all_pass = all(r["passed"] for r in results)
    if a.json:
        print(json.dumps({"all_pass": all_pass, "results": results}, indent=2))
    else:
        for r in results:
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"{mark}  {r['name']:16s} rc={r['returncode']} {r['wall_s']}s")
        print("suite:", "PASS" if all_pass else "FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
