#!/usr/bin/env python3
"""Self-replay (docs D9): first divergence between two traces of the SAME
table on two versions of a design (e.g. the original buggy sources vs the
agent's fixed sources).  Same rules as the label pipeline (gen_real_labels):
top-level inputs and clocks excluded, signals present in only one trace are
structural differences, state-first = earliest differing register.

Usage: first_divergence.py A.vcd B.vcd --tb tb_table.cpp --top TOP --src rtl/*.v
Prints a short human-readable report for the agent and exits 0 (1 if the
traces never diverge).
"""
import argparse, pathlib, re, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PORT_RE = re.compile(r'\{"(\w+)", (true|false), (\d+),')
BLOCK_RE = re.compile(r"(always_ff\b|always\b)(.*?)(?=^\s*always|^\s*assign|^\s*endmodule|\Z)", re.S | re.M)
NB_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\])*\s*<=", re.M)
MODULE_RE = re.compile(r"^\s*module\s+(\w+)", re.M)

def base(sig): return re.sub(r"\[.*", "", sig.rsplit(".", 1)[-1])

def instance_map(text):
    modules = set(MODULE_RE.findall(text)); inst = {}
    rx = re.compile(r"^\s*(\w+)\s*(?:#\s*\((?:[^()]|\([^()]*\))*\))?\s*(\w+)\s*\(", re.M)
    for m in rx.finditer(text):
        if m.group(1) in modules: inst[m.group(2)] = m.group(1)
    return inst

def resolve_module(signal, top, inst):
    for scope in reversed(signal.split(".")[2:-1]):
        scope = re.sub(r"\[.*\]$", "", scope)
        if scope in inst: return inst[scope]
    return top

def _vcd_stream(path):
    """Yield (time, {full_name: value}) with ONLY the signals that changed at that
    time; first yields ("defs", {code: full_name}) once. Streams the file, holds
    no history (the label pipeline's snapshot list needs tens of GB on the
    483k-row zipcpu tables; this needs the current state only)."""
    codes, scopes, in_defs = {}, [], True
    pending, cur_t = {}, None
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if in_defs:
                if line.startswith("$scope"):
                    scopes.append(line.split()[2])
                elif line.startswith("$upscope"):
                    scopes.pop()
                elif line.startswith("$var"):
                    bits = line.split(); name = bits[4]; path_ = ".".join(scopes)
                    if len(scopes) >= 2 and scopes[0] == "TOP":
                        codes[bits[3]] = f"{path_}.{name}"
                elif line.startswith("$enddefinitions"):
                    in_defs = False; yield ("defs", dict(codes))
                continue
            if not line: continue
            if line[0] == "#":
                if pending: yield (cur_t, pending); pending = {}
                cur_t = int(line[1:]); continue
            if line[0] in "01xzXZ" and len(line) > 1 and line[1:] in codes:
                pending[codes[line[1:]]] = line[0]
            elif line[0] in "bBrR":
                v, _, c = line.partition(" ")
                if c in codes: pending[codes[c]] = v[1:]
            elif line[0] in "01xzXZ" and line[1:] in codes:
                pending[codes[line[1:]]] = line[0]
    if pending: yield (cur_t, pending)

def _merged(a_path, b_path):
    """Walk both streams in time order; yield (time, state_a, state_b) at every
    time where either changed. States are the live dicts (do not mutate)."""
    ga, gb = _vcd_stream(a_path), _vcd_stream(b_path)
    _, defs_a = next(ga); _, defs_b = next(gb)
    names_a, names_b = set(defs_a.values()), set(defs_b.values())
    sa, sb = {}, {}
    na, nb = next(ga, None), next(gb, None)
    def emit(): return sa, sb
    while na is not None or nb is not None:
        ta = na[0] if na else float("inf"); tb = nb[0] if nb else float("inf")
        t = min(ta, tb)
        if ta == t: sa.update(na[1]); na = next(ga, None)
        if tb == t: sb.update(nb[1]); nb = next(gb, None)
        yield t, sa, sb, names_a, names_b

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a"); ap.add_argument("b")
    ap.add_argument("--tb", required=True); ap.add_argument("--top", required=True)
    ap.add_argument("--src", nargs="+", required=True)
    ap.add_argument("--label-a", default="ORIGINAL"); ap.add_argument("--label-b", default="YOUR FIX")
    a = ap.parse_args()
    ports = {m.group(1): ("in" if m.group(2) == "true" else "out") for m in PORT_RE.finditer(pathlib.Path(a.tb).read_text())}
    inputs = {p for p, d in ports.items() if d == "in"}
    srcs = "\n".join(pathlib.Path(s).read_text() for s in a.src)
    regs = set(); [regs.update(NB_ASSIGN_RE.findall(m.group(2))) for m in BLOCK_RE.finditer(srcs)]
    inst = instance_map(srcs)
    def is_top_input(s): p = s.split("."); return p[:-1] == ["TOP", a.top] and base(s) in inputs
    def is_clock(s): return "clk" in base(s).lower() or base(s).lower() == "clock"
    first = None; state_first = None; names_a = names_b = set()
    # VCD time -> table row: the harness dumps once per rising edge, so the
    # n-th distinct timestamp is row n (same convention as the label pipeline,
    # which indexes snapshots).
    row = -1
    for t, sa, sb, names_a, names_b in _merged(a.a, a.b):
        row += 1
        common = names_a & names_b
        diff = sorted(s for s in common if sa.get(s, "x") != sb.get(s, "x"))
        kept = [s for s in diff if not is_top_input(s) and not is_clock(s)]
        if not kept: continue
        if first is None: first = (row, kept, {s: (sa.get(s, "x"), sb.get(s, "x")) for s in kept})
        if state_first is None:
            r = [s for s in kept if base(s) in regs]
            if r: state_first = (row, r[0])
        if first and state_first: break
    only_a = sorted(base(s) for s in names_a - names_b); only_b = sorted(base(s) for s in names_b - names_a)
    if first is None:
        print(f"The {a.label_a} and {a.label_b} designs never diverge on this table: the traces are identical "
              "on every internal signal. Your change has no effect under this stimulus.")
        if only_a or only_b: print(f"(structural: nets only in {a.label_a}: {only_a}; only in {a.label_b}: {only_b})")
        return 1
    cyc, sigs, vals = first
    print(f"First divergence between {a.label_a} and {a.label_b} on this table: table row {cyc}.")
    print(f"Signals that differ at that row ({len(sigs)}):")
    for s in sigs[:12]:
        short = s[4:] if s.startswith("TOP.") else s
        kind = "register" if base(s) in regs else ("output port" if ports.get(base(s)) == "out" else "net")
        print(f"  {short}  [{kind}, module {resolve_module(s, a.top, inst)}]  {a.label_a}={vals[s][0]}  {a.label_b}={vals[s][1]}")
    if len(sigs) > 12: print(f"  ... {len(sigs) - 12} more")
    if state_first:
        s = state_first[1]; print(f"Earliest differing REGISTER: {s[4:] if s.startswith('TOP.') else s} at row {state_first[0]}.")
    if only_a or only_b:
        print(f"Structural: nets only in {a.label_a}: {only_a or '-'}; only in {a.label_b}: {only_b or '-'} (not compared).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
