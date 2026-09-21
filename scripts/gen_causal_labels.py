#!/usr/bin/env python3
"""Generate first-divergence causal labels from open-loop replay traces.

The golden recorder is the only source of inputs.  Both replay binaries see
the resulting file, so a mutant cannot perturb later stimulus through its
ready/valid outputs.  ``first_divergence`` remains deliberately state-first;
``first_divergence_set`` captures every traced signal at the earliest
divergence cycle, so a direct combinational cause is not hidden by a later
sequential consequence.
"""
import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time

from vcd_query import timed_trace_snapshots

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITE = json.loads((ROOT / "bench" / "suite.json").read_text())["configs"]
STATE_RE = re.compile(r"^(state|beat|rf_got|fl_|wb_|r_|flush_done|resp_valid|tag_arr|valid_arr|dirty_arr|lru_arr|data_arr)")
OUTPUT_RE = re.compile(r"^(req_ready|resp_valid|resp_rdata|flush_done|mem_req_valid|mem_req_we|mem_req_addr|mem_req_wdata)$")
REPLAY_INPUTS = {
    "rst", "req_valid", "req_addr", "req_we", "req_wdata", "req_wstrb",
    "flush_req", "mem_req_ready", "mem_resp_valid", "mem_resp_rdata",
}
EXCLUDED_TRACE_SIGNALS = REPLAY_INPUTS | {"clk"}
TIERS = {
    "single": (ROOT / "bench" / "mutants", ROOT / "bench" / "kill_matrix.json"),
    "hard": (ROOT / "bench" / "mutants_hard", ROOT / "bench" / "kill_matrix_mutants_hard.json"),
}


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def build(rtl, out):
    run([ROOT / "scripts" / "build_sim.sh", rtl, out, ROOT / "tb" / "tb_replay.cpp"],
        stdout=subprocess.DEVNULL)
    return out / "sim"


def trace_snapshots(path, signals=None):
    return [snapshot for _, snapshot in timed_trace_snapshots(path, signals)]


def aligned_snapshots(golden, mutant, signals=None):
    """Yield snapshots aligned by VCD tick, carrying quiet-trace values on."""
    left = dict(timed_trace_snapshots(golden, signals))
    right = dict(timed_trace_snapshots(mutant, signals))
    a, b = {}, {}
    for cycle in sorted(set(left) | set(right)):
        if cycle in left:
            a = left[cycle]
        if cycle in right:
            b = right[cycle]
        yield cycle, a, b


def state_trace(path, signals=STATE_RE):
    """Return per-dump snapshots of the selected sequential state signals."""
    return trace_snapshots(path, signals)


def first_divergence(golden, mutant):
    for cycle, a, b in aligned_snapshots(golden, mutant, STATE_RE):
        for signal in sorted(set(a) | set(b)):
            if a.get(signal, "x") != b.get(signal, "x"):
                return {"signal": signal, "cycle": cycle, "module": "cache_ctrl"}
    return None


def first_divergence_set(golden, mutant):
    """All non-input traced signals at the earliest differing VCD dump."""
    for cycle, a, b in aligned_snapshots(golden, mutant):
        differing = sorted(signal for signal in set(a) | set(b)
                           if a.get(signal, "x") != b.get(signal, "x"))
        if not differing:
            continue
        input_diffs = [signal for signal in differing
                       if signal.rsplit(".", 1)[-1] in REPLAY_INPUTS]
        if input_diffs:
            raise RuntimeError(f"replay bug at cycle {cycle}: input ports diverged: "
                               + ", ".join(input_diffs))
        kept = [signal for signal in differing
                if signal.rsplit(".", 1)[-1] not in EXCLUDED_TRACE_SIGNALS]
        if not kept:
            raise RuntimeError(f"replay bug at cycle {cycle}: only excluded signals diverged: {differing}")
        return cycle, [{"signal": signal, "module": "cache_ctrl"} for signal in kept]
    return None


def choose_config(kills):
    names = kills.get("killed_by", [])
    if not names:
        raise RuntimeError(f"{kills['name']} has no killing suite config")
    name = "default" if "default" in names else names[0]
    return next(c for c in SUITE if c["name"] == name)


def args_for(cfg):
    return [f"+{key}={value}" for key, value in cfg["args"].items()]


def label_one(mutant, cfg, golden_sim, work, golden_cache):
    key = cfg["name"]
    if key not in golden_cache:
        inputs = work / f"{key}.inputs"
        recorded = work / f"{key}.record.vcd"
        replayed = work / f"{key}.golden.vcd"
        run([golden_sim, f"+record={inputs}", f"+trace={recorded}"] + args_for(cfg),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run([golden_sim, f"+replay={inputs}", f"+trace={replayed}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        golden_cache[key] = (inputs, replayed)
    inputs, golden = golden_cache[key]
    obj = work / ("obj_" + mutant.name)
    sim = build(mutant / "cache_ctrl.sv", obj)
    trace = work / (mutant.name + ".vcd")
    run([sim, f"+replay={inputs}", f"+trace={trace}"], stdout=subprocess.DEVNULL)
    divergence_set = first_divergence_set(golden, trace)
    if divergence_set is None:
        raise RuntimeError(f"{mutant.name}: no traced-signal divergence under {key}")
    divergence = first_divergence(golden, trace)
    if divergence is None:
        for cycle, a, b in aligned_snapshots(golden, trace, OUTPUT_RE):
            for signal in sorted(set(a) | set(b)):
                if a.get(signal, "x") != b.get(signal, "x"):
                    divergence = {"signal": signal, "cycle": cycle, "module": "cache_ctrl"}
                    break
            if divergence:
                break
    if divergence is None:
        raise RuntimeError(f"{mutant.name}: no state divergence under {key}")
    return divergence, divergence_set[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=TIERS, required=True)
    ap.add_argument("--mutants", help="comma-separated IDs (default: whole tier)")
    ap.add_argument("--work-dir", type=pathlib.Path, default=ROOT / ".causal_work")
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--validate", action="store_true", help="assert the three hand-derived single labels")
    a = ap.parse_args()
    mutant_dir, matrix_path = TIERS[a.tier]
    matrix = json.loads(matrix_path.read_text())
    wanted = set(a.mutants.split(",")) if a.mutants else None
    mutants = sorted(p for p in mutant_dir.iterdir() if p.is_dir() and (wanted is None or p.name in wanted))
    if wanted and wanted != {p.name for p in mutants}:
        raise SystemExit("unknown mutant(s): " + ", ".join(sorted(wanted - {p.name for p in mutants})))
    work = a.work_dir / a.tier
    if work.exists(): shutil.rmtree(work)
    work.mkdir(parents=True)
    t0 = time.time()
    golden_sim = build(ROOT / "rtl" / "cache_ctrl.sv", work / "obj_golden")
    golden_cache, labels = {}, {}
    for mutant in mutants:
        cfg = choose_config(matrix[mutant.name])
        div, div_set = label_one(mutant, cfg, golden_sim, work, golden_cache)
        parts = []
        for part in json.loads((mutant / "bug.json").read_text()).get("parts", []):
            part_dir = ROOT / "bench" / "mutants" / part
            if not part_dir.is_dir():
                raise RuntimeError(f"{mutant.name}: missing single-part mutant {part}")
            try:
                part_div, _ = label_one(part_dir, cfg, golden_sim, work, golden_cache)
            except RuntimeError as exc:
                part_div = None
                print(f"{mutant.name}/{part}: {exc}", file=sys.stderr)
            parts.append({"mutant": part, "first_divergence": part_div})
        labels[mutant.name] = {"mutant": mutant.name, "config": cfg["name"],
                               "seed": cfg["args"]["seed"], "first_divergence": div,
                               "first_divergence_set": div_set, "parts": parts,
                               "notes": "open-loop replay; state-first and all-traced-signal VCD diffs"}
        print(f"{mutant.name}: {div['signal']} cycle {div['cycle']}; set: "
              + ", ".join(item["signal"] for item in div_set))
    if a.validate:
        state_expected = {"M01_whit_no_dirty": "dirty_arr", "M17_reset_valid_high": "valid_arr",
                          "M26_resp_word0": "r_resp_data"}
        set_expected = {**state_expected, "M29_hit_requires_both_ways": "hit"}
        for mutant, needle in set_expected.items():
            if mutant not in labels:
                continue
            got_set = labels[mutant]["first_divergence_set"]
            print(f"validation {mutant}: set = " + ", ".join(item["signal"] for item in got_set))
            if not any(needle in item["signal"] for item in got_set):
                raise RuntimeError(f"validation {mutant}: expected {needle} in first_divergence_set")
            if mutant in state_expected:
                got = labels[mutant]["first_divergence"]["signal"]
                if state_expected[mutant] not in got:
                    raise RuntimeError(f"validation {mutant}: expected state-first {state_expected[mutant]}, got {got}")
    out = ROOT / "bench" / "causal_labels" / a.tier
    out.mkdir(parents=True, exist_ok=True)
    for name, label in labels.items():
        (out / f"{name}.json").write_text(json.dumps(label, indent=2) + "\n")
    print(f"wrote {len(labels)} labels in {time.time() - t0:.1f}s")
    if not a.keep_work:
        shutil.rmtree(work)


if __name__ == "__main__":
    main()
