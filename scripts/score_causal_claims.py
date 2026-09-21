#!/usr/bin/env python3
"""Score CAUSE_* causal claims against mechanically-computed ground truth.

Usage: score_causal_claims.py <labels_dir> <summaries.json> [...]

Labels (bench/causal_labels/<tier>/<mutant>.json, produced by
gen_causal_labels.py):
    {"mutant": ..., "first_divergence": {"signal": ..., "cycle": ...,
     "module": ...}, ...}

Claims come from runs/*/summaries.json entries' "cause" field (emitted by
the --cause-footer flat system).

Frozen scoring levels (defined before
any Gemini measurement; do not adjust post hoc):
  exact   claimed signal base name == label signal base name
  block   claimed signal is assigned in the same always block as the label
          signal (static grouping over the mutant RTL)
  module  claimed module == label module only
  miss    none of the above (or no claim emitted)
"""
import json
import pathlib
import re
import sys

_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\])*\s*<?=", re.M)
_BLOCK_RE = re.compile(
    r"(always_ff\b|always_comb\b|always\b)(.*?)(?=^\s*always|\Z)",
    re.S | re.M)


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from yarrow.causal import base_name, truth_set  # single truth source


def block_groups(rtl_text: str) -> list:
    """Sets of signal base names assigned within each always block."""
    groups = []
    for m in _BLOCK_RE.finditer(rtl_text):
        names = set(_ASSIGN_RE.findall(m.group(2)))
        if names:
            groups.append(names)
    return groups


def score_one(claim: dict, label: dict, rtl_text: str) -> str:
    if not claim or not claim.get("signal"):
        return "miss"
    got = base_name(claim["signal"])
    want = base_name(label["first_divergence"]["signal"])
    if got and got in truth_set(label):
        return "exact"
    for g in block_groups(rtl_text):
        if got in g and want in g:
            return "block"
    want_mod = (label["first_divergence"].get("module") or "").strip()
    got_mod = (claim.get("module") or "").strip()
    if want_mod and got_mod and got_mod == want_mod:
        return "module"
    return "miss"


def main() -> int:
    labels_dir = pathlib.Path(sys.argv[1])
    labels = {}
    for f in labels_dir.rglob("*.json"):
        d = json.loads(f.read_text())
        labels[d["mutant"]] = (d, f)

    counts = {"exact": 0, "block": 0, "module": 0, "miss": 0,
              "unlabeled": 0}
    rows = []
    for summ in sys.argv[2:]:
        run = pathlib.Path(summ).parent.name
        for m in json.loads(pathlib.Path(summ).read_text()):
            lab = labels.get(m["mutant"])
            if not lab:
                counts["unlabeled"] += 1
                continue
            label, lf = lab
            # Score against the MUTANT RTL the agent saw (block grouping).
            mdir = lf.parent.name  # tier dir name mirrors mutants dir
            rtl = None
            for tier in ("mutants", "mutants_hard"):
                p = (pathlib.Path("bench") / tier / m["mutant"]
                     / "cache_ctrl.sv")
                if p.exists():
                    rtl = p.read_text()
                    break
            if rtl is None:
                # tier R (docs D6): concatenated design sources
                dj = pathlib.Path("bench") / "real" / m["mutant"] / "design.json"
                if dj.exists():
                    rtl = "\n".join((dj.parent / "rtl_buggy" / s).read_text()
                                    for s in json.loads(dj.read_text())["sources"])
            grade = score_one(m.get("cause") or {}, label, rtl or "")
            counts[grade] += 1
            rows.append((run, m["mutant"], m["status"], grade,
                         (m.get("cause") or {}).get("signal", "-"),
                         label["first_divergence"]["signal"]))

    for r in sorted(rows):
        print(f"{r[0]:34s} {r[1][:28]:28s} {r[2]:9s} {r[3]:7s} "
              f"claimed={r[4]:20s} truth={r[5]}")
    n = sum(counts[k] for k in ("exact", "block", "module", "miss"))
    if n:
        print(f"\nfidelity over {n} scored runs: "
              + "  ".join(f"{k}={counts[k]} ({100*counts[k]/n:.0f}%)"
                          for k in ("exact", "block", "module", "miss")))
    if counts["unlabeled"]:
        print(f"unlabeled mutants skipped: {counts['unlabeled']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
