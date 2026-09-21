#!/usr/bin/env python3
"""Import the RTL-Repair `fpga-debugging` real-bug benchmarks into bench/real.

Source: https://github.com/ekiwi/rtl-repair (BSD-3 packaging) — the 11
designs / 14 bugs derived from the field-collected bugs of "Debugging in the
Brave New World of Reconfigurable Hardware" (ASPLOS'22).  Designs are
production open-source IP (alexforencich verilog-axis, Xilinx AXI templates,
ZipCPU sdspi, ...) under their own upstream licenses; we redistribute only
for benchmark reproduction and cite upstream.

Layout written per bug (one directory per bug so the driver's
`<mutants-dir>/<id>/` convention holds):

    bench/real/<id>/design.json      sources, top, bug_file, tables, provenance
    bench/real/<id>/rtl/*.v          golden sources
    bench/real/<id>/rtl_buggy/*.v    same file list, bug_file replaced
    bench/real/<id>/tb/*.csv         table testbenches applicable to this bug
    bench/real/<id>/bug.diff         upstream golden->buggy diff (hand-checks)

Usage: import_rtlrepair.py <rtl-repair checkout>/benchmarks/fpga-debugging
"""
import difflib
import json
import pathlib
import re
import shutil
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "bench" / "real"
PROVENANCES = {
    "real": {
        "suite": "rtl-repair/benchmarks/fpga-debugging",
        "suite_url": "https://github.com/ekiwi/rtl-repair",
        "upstream_study": "Debugging in the Brave New World of Reconfigurable "
                          "Hardware (ASPLOS'22), 20 field-collected bugs",
    },
    "cirfix": {
        "suite": "rtl-repair/benchmarks/cirfix",
        "suite_url": "https://github.com/ekiwi/rtl-repair",
        "upstream_study": "CirFix: Automatically Repairing Defects in Hardware "
                          "Design Code (ASPLOS'22); defects injected by the "
                          "CirFix authors and students (externally authored "
                          "synthetic control tier X, docs D6)",
    },
}
TABLE_SEMANTICS = ("one row per rising edge; inputs driven, outputs compared "
                   "pre-edge; recorded by the original Verilog testbenches via "
                   "@(posedge clk) $fwrite")
PROVENANCE = {**PROVENANCES["real"], "table_semantics": TABLE_SEMANTICS}


def load_project(pdir: pathlib.Path) -> dict:
    toml = pdir / "project.toml"
    if toml.exists():
        return tomllib.loads(toml.read_text())
    # fadd-d7 ships without a descriptor: synthesize one from its files.
    srcs = sorted(p.name for p in pdir.glob("*.v") if "_bug_" not in p.name)
    bugs = sorted(p.name for p in pdir.glob("*_bug_*.v"))
    assert len(srcs) == 1 and len(bugs) == 1, (pdir, srcs, bugs)
    top = re.search(r"^\s*module\s+(\w+)", (pdir / srcs[0]).read_text(), re.M).group(1)
    return {"project": {"sources": srcs, "toplevel": top},
            "bugs": [{"name": re.search(r"_bug_(\w+)\.v$", bugs[0]).group(1),
                      "original": srcs[0], "buggy": bugs[0]}],
            "testbenches": [{"name": "csv", "table": "tb.csv"}]}


def import_project(pdir: pathlib.Path) -> list:
    proj = load_project(pdir)
    sources = proj["project"]["sources"]
    top = proj["project"]["toplevel"]
    made = []
    for bug in proj["bugs"]:
        # Project dirs carry their bug ids ("axis-fifo-d4", "zipcpu-spi-c1-c3-d9",
        # "axi-lite-s1" with bugs s1b/s1r): strip the id list, append this bug.
        base = re.sub(r"(-(c\d+|d\d+|s\d+[a-z]?))+$", "", pdir.name)
        bid = f"{base}-{bug['name']}"
        dest = OUT / bid
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "rtl").mkdir(parents=True)
        (dest / "rtl_buggy").mkdir()
        (dest / "tb").mkdir()
        for s in sources:
            shutil.copy(pdir / s, dest / "rtl" / s)
            src = bug["buggy"] if s == bug["original"] else s
            shutil.copy(pdir / src, dest / "rtl_buggy" / s)
        # `include files (CirFix's pairing uses inc.v for its `W* defines):
        # copy them beside the sources in both trees; not part of `sources`.
        for s in sources:
            for inc in re.findall(r'`include\s+"([^"]+)"', (pdir / s).read_text()):
                if (pdir / inc).exists():
                    for tree in ("rtl", "rtl_buggy"):
                        shutil.copy(pdir / inc, dest / tree / inc)
        tables = []
        for tb in proj.get("testbenches", []):
            if "table" not in tb:
                continue  # Verilog oracle TBs need VCS/iverilog; tables suffice
            if tb.get("bugs") and bug["name"] not in tb["bugs"]:
                continue
            shutil.copy(pdir / tb["table"], dest / "tb" / tb["table"])
            tables.append({"name": tb["name"], "file": tb["table"],
                           "rows": sum(1 for _ in open(pdir / tb["table"])) - 1})
        if not tables:
            # CirFix's sdram_controller ships only Verilog oracle TBs (VCS/
            # iverilog); without a table there is no open-loop stimulus.
            print(f"skip {bid}: no table testbench applies", file=sys.stderr)
            shutil.rmtree(dest)
            continue
        golden = (pdir / bug["original"]).read_text().splitlines(keepends=True)
        buggy = (pdir / bug["buggy"]).read_text().splitlines(keepends=True)
        (dest / "bug.diff").write_text("".join(difflib.unified_diff(
            golden, buggy, fromfile=f"golden/{bug['original']}",
            tofile=f"buggy/{bug['original']}")))
        design = {"id": bid, "project": pdir.name, "bug": bug["name"],
                  "sources": sources, "top": top, "bug_file": bug["original"],
                  "tables": tables,
                  "rtl_lines": sum(len(open(pdir / s).readlines()) for s in sources),
                  "provenance": {**PROVENANCE, "upstream_buggy_file": bug["buggy"]}}
        (dest / "design.json").write_text(json.dumps(design, indent=2) + "\n")
        made.append(design)
    return made


def main() -> int:
    global OUT, PROVENANCE
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=pathlib.Path,
                    help="<rtl-repair>/benchmarks/fpga-debugging or .../cirfix")
    ap.add_argument("--tier", choices=PROVENANCES, default="real")
    ap.add_argument("--out", type=pathlib.Path, default=None,
                    help="default bench/<tier>")
    a = ap.parse_args()
    OUT = a.out or (ROOT / "bench" / a.tier)
    PROVENANCE = {**PROVENANCES[a.tier], "table_semantics": TABLE_SEMANTICS}
    src = a.src.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    designs = []
    # CirFix nests the OpenCores designs one level down
    pdirs = [p for p in sorted(src.iterdir()) if p.is_dir() and p.name != "opencores"]
    if (src / "opencores").is_dir():
        pdirs += [p for p in sorted((src / "opencores").iterdir()) if p.is_dir()]
    for pdir in pdirs:
        if not (pdir / "project.toml").exists() and not list(pdir.glob("*_bug_*.v")):
            continue
        designs.extend(import_project(pdir))
    print(f"{'id':28} {'top':22} {'files':>5} {'lines':>6} tables(rows)")
    for d in designs:
        print(f"{d['id']:28} {d['top']:22} {len(d['sources']):5d} {d['rtl_lines']:6d} "
              + ", ".join(f"{t['name']}({t['rows']})" for t in d["tables"]))
    print(f"\nimported {len(designs)} bugs into {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
