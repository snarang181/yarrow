#!/usr/bin/env python3
"""Generate mutant RTL files from bench/mutations.json.

Each mutation's `find` string must occur exactly once in the golden RTL;
the mutant is the golden file with that one occurrence replaced.
Outputs bench/mutants/<id>/cache_ctrl.sv plus a bug.json metadata stub.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "rtl" / "cache_ctrl.sv"
MANIFEST = ROOT / "bench" / "mutations.json"
OUT = ROOT / "bench" / "mutants"


def main() -> int:
    golden = GOLDEN.read_text()
    manifest = json.loads(MANIFEST.read_text())
    errors = 0
    for m in manifest["mutations"]:
        n = golden.count(m["find"])
        if n != 1:
            print(f"ERROR {m['id']}: find-string occurs {n} times (want 1)")
            errors += 1
            continue
        mutated = golden.replace(m["find"], m["replace"], 1)
        d = OUT / m["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "cache_ctrl.sv").write_text(mutated)
        (d / "bug.json").write_text(json.dumps(
            {"id": m["id"], "class": m["class"], "desc": m["desc"]}, indent=2) + "\n")
        print(f"ok    {m['id']}")
    if errors:
        return 1
    print(f"\ngenerated {len(manifest['mutations'])} mutants in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
