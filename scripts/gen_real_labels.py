#!/usr/bin/env python3
"""Causal ground-truth labels for the real-bug tier (bench/real, docs D6).

Same D1/D1b semantics as gen_causal_labels.py, applied to table testbenches:
golden and buggy are driven by the identical CSV (open loop by construction),
both dump all-signal VCDs, and the label is the FIRST divergence.

  first_divergence_set : every traced non-input, non-clock signal that
                         differs at the earliest differing VCD tick
  first_divergence     : state-first — the earliest differing signal that
                         is assigned with `<=` inside an always block of the
                         design sources (the declared analog of cache_ctrl's
                         hand-written STATE_RE); else the earliest differing
                         output port
  module               : the MODULE TYPE containing the signal (resolved
                         from instantiations in the sources), comparable to
                         the agent's CAUSE_MODULE; the VCD instance path is
                         recorded as `instance`
  cycle                : the table row (the generated TB dumps once per row
                         at VCD time == row)

Writes bench/causal_labels/real/<id>.json.  Reuses the VALID-gate builds in
bench/.validate_real_work/ (run scripts/validate_real.py first) and the
failing table recorded in bench/kill_matrix_real.json.

Usage: gen_real_labels.py [--ids a,b] [--hand-check]
"""
import argparse
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from yarrow.design import Design  # noqa: E402
from vcd_query import timed_trace_snapshots  # noqa: E402

WORK = ROOT / "bench" / ".validate_real_work"
OUT = ROOT / "bench" / "causal_labels" / "real"
PORT_RE = re.compile(r'\{"(\w+)", (true|false), (\d+),')
MODULE_RE = re.compile(r"^\s*module\s+(\w+)", re.M)
BLOCK_RE = re.compile(r"(always_ff\b|always_comb\b|always\b)(.*?)(?=^\s*always|^\s*endmodule|\Z)",
                      re.S | re.M)
NB_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\])*\s*<=", re.M)


def ports_of(design: Design) -> dict:
    """{port: 'in'|'out'} from the generated table TB (Verilator header)."""
    return {m.group(1): ("in" if m.group(2) == "true" else "out")
            for m in PORT_RE.finditer(design.tb_cpp.read_text())}


def registers_of(sources_text: str) -> set:
    regs = set()
    for m in BLOCK_RE.finditer(sources_text):
        regs |= set(NB_ASSIGN_RE.findall(m.group(2)))
    return regs


def instance_map(sources_text: str) -> dict:
    """{instance name: module type} for every instantiation in the sources."""
    modules = set(MODULE_RE.findall(sources_text))
    inst = {}
    # `<type> [#( ... )] <inst> (` — parameter lists may nest one level
    rx = re.compile(r"^\s*(\w+)\s*(?:#\s*\((?:[^()]|\([^()]*\))*\))?\s*(\w+)\s*\(", re.M)
    for m in rx.finditer(sources_text):
        if m.group(1) in modules:
            inst[m.group(2)] = m.group(1)
    return inst


def resolve_module(signal: str, top: str, inst: dict) -> str:
    """Module TYPE containing the signal: walk the VCD scope path from the
    signal outward to the nearest instance name (skipping generate scopes
    such as `genblk1` or `m_ifaces[0]`); the top module if none."""
    parts = signal.split(".")  # TOP.<top>.<inst|genblk...>.<sig>
    for scope in reversed(parts[2:-1]):
        scope = re.sub(r"\[.*\]$", "", scope)
        if scope in inst:
            return inst[scope]
    return top


def aligned(golden, buggy):
    left = dict(timed_trace_snapshots(golden))
    right = dict(timed_trace_snapshots(buggy))
    a, b = {}, {}
    for cycle in sorted(set(left) | set(right)):
        a = left.get(cycle, a)
        b = right.get(cycle, b)
        yield cycle, a, b


def base(sig: str) -> str:
    return re.sub(r"\[.*", "", sig.rsplit(".", 1)[-1])


def label_one(design: Design, table: str, hand_check: bool) -> dict:
    work = WORK / design.id
    sims = {v: work / f"obj_{v}" / "sim" for v in ("golden", "buggy")}
    if not all(s.exists() for s in sims.values()):
        raise SystemExit(f"{design.id}: run scripts/validate_real.py first (builds missing)")
    csv = design.table_path(table)
    vcds = {}
    for v, sim in sims.items():
        vcds[v] = work / f"{v}.{table}.vcd"
        subprocess.run([str(sim), f"+table={csv}", f"+trace={vcds[v]}"],
                       capture_output=True, text=True, timeout=600)
    ports = ports_of(design)
    inputs = {p for p, d in ports.items() if d == "in"}
    srcs = "\n".join(design.golden_texts().values())
    regs, inst = registers_of(srcs), instance_map(srcs)

    def is_top_input(s: str) -> bool:
        # Only the TOP-module scope carries the driven ports; an inner module's
        # port of the same name (e.g. a register slice's s_axis_tvalid) is an
        # internal net and a legitimate divergence candidate.
        parts = s.split(".")
        return parts[:-1] == ["TOP", design.top] and base(s) in inputs

    def is_clock(s: str) -> bool:
        return base(s).lower() in ("clk", "clock") or "clk" in base(s).lower()

    # A real bug may add/remove/rename nets (d4 deletes `full_wr`): a signal
    # present in only one trace is a STRUCTURAL difference, not a divergence.
    golden_names = set().union(*(snap.keys() for _, snap in timed_trace_snapshots(vcds["golden"])))
    buggy_names = set().union(*(snap.keys() for _, snap in timed_trace_snapshots(vcds["buggy"])))
    common = golden_names & buggy_names
    structural = {"only_golden": sorted(base(s) for s in golden_names - buggy_names),
                  "only_buggy": sorted(base(s) for s in buggy_names - golden_names)}

    first = None       # (cycle, [signals])
    state_first = None  # (cycle, signal)
    out_first = None
    for cycle, a, b in aligned(vcds["golden"], vcds["buggy"]):
        diff = sorted(s for s in common if a.get(s, "x") != b.get(s, "x"))
        if not diff:
            continue
        bad_inputs = [s for s in diff if is_top_input(s)]
        if bad_inputs and first is None:
            # identical tables drive both designs; a TOP input differing before
            # any internal signal would be a harness bug, not a label
            raise RuntimeError(f"{design.id}: input ports diverged at row {cycle}: {bad_inputs}")
        kept = [s for s in diff if not is_top_input(s) and not is_clock(s)]
        if not kept:
            continue
        if first is None:
            first = (cycle, kept)
        if state_first is None:
            r = [s for s in kept if base(s) in regs]
            if r:
                state_first = (cycle, r[0])
        if out_first is None:
            o = [s for s in kept if ports.get(base(s)) == "out"]
            if o:
                out_first = (cycle, o[0])
        if first and state_first:
            break
    if first is None:
        raise RuntimeError(f"{design.id}: golden and buggy traces never diverge on {table}")
    chosen = state_first or out_first or (first[0], first[1][0])
    mod = lambda s: resolve_module(s, design.top, inst)  # noqa: E731
    label = {
        "mutant": design.id, "config": table, "seed": None,
        "first_divergence": {"signal": chosen[1], "cycle": chosen[0], "module": mod(chosen[1]),
                             "instance": chosen[1].rsplit(".", 1)[0],
                             "kind": "state" if state_first else ("output" if out_first else "signal")},
        "first_divergence_set": [{"signal": s, "module": mod(s)} for s in first[1]],
        "parts": [],
        "structural_diff": structural,
        "set_size": len(first[1]), "set_cycle": first[0],
        "trace_rows": next(t["rows"] for t in design.tables if t["name"] == table),
        "notes": "table replay (open loop by construction); pre-edge sampling; "
                 "state-first = signals assigned with <= in always blocks; "
                 "module = instantiated module type, instance = VCD scope path",
    }
    if hand_check:
        print(f"\n===== HAND CHECK {design.id} =====")
        print("label:", json.dumps(label["first_divergence"]))
        print(f"set @row {first[0]} ({len(first[1])}):", ", ".join(base(s) for s in first[1])[:300])
        if structural["only_golden"] or structural["only_buggy"]:
            print("structural (nets present in one design only):", structural)
        diff_lines = [l for l in (design.root / "bug.diff").read_text().splitlines()
                      if l[:1] in "+-" and l[:3] not in ("+++", "---")]
        print("upstream bug diff:"); print("\n".join("   " + l.rstrip() for l in diff_lines[:12]))
    return label


def main() -> int:
    global WORK, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", help="comma-separated ids (default: all)")
    ap.add_argument("--hand-check", action="store_true")
    ap.add_argument("--bench", type=pathlib.Path, default=ROOT / "bench" / "real",
                    help="bench/<tier> (labels go to bench/causal_labels/<tier>, "
                         "builds come from bench/.validate_<tier>_work)")
    a = ap.parse_args()
    tier = a.bench.name
    WORK = ROOT / "bench" / f".validate_{tier}_work"
    OUT = ROOT / "bench" / "causal_labels" / tier
    km = json.loads((ROOT / "bench" / f"kill_matrix_{tier}.json").read_text())
    designs = [Design.load(d) for d in sorted(a.bench.iterdir())
               if (d / "design.json").exists()]
    if a.ids:
        want = set(a.ids.split(","))
        designs = [d for d in designs if d.id in want]
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    sizes = []
    print(f"{'id':22} {'row':>7} {'kind':>6} {'set':>4}  state-first signal (module)")
    for d in designs:
        e = km.get(d.id)
        if not e or not e.get("compiled") or not e.get("killed_by"):
            print(f"{d.id:22} skipped: not VALID in kill matrix (build/kill gate)")
            continue
        table = e["killed_by"][0]
        lab = label_one(d, table, a.hand_check)
        (OUT / f"{d.id}.json").write_text(json.dumps(lab, indent=2) + "\n")
        fd = lab["first_divergence"]
        sizes.append(lab["set_size"])
        print(f"{d.id:22} {fd['cycle']:7d} {fd['kind']:>6} {lab['set_size']:4d}  "
              f"{base(fd['signal'])} ({fd['module']})")
    from collections import Counter
    print(f"\nset-size distribution: {dict(sorted(Counter(sizes).items()))}")
    print(f"wrote {len(designs)} labels to {OUT} in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
