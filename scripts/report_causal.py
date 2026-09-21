#!/usr/bin/env python3
"""Aggregate causal-fidelity results into the per-tier dissociation table.

Usage: report_causal.py <labels_dir> <summaries.json> [...]

Groups runs by (model, tier) — tier inferred from the run dir name — and
reports, aggregated over repeats: repair rate, exact/block/module/miss
fidelity over ALL runs, fidelity conditioned on FIXED runs, and the
dissociation (repair% − exact%|fixed). Scoring itself comes from
score_causal_claims.score_one (frozen levels).
"""
import importlib.util
import json
import pathlib
import sys

_here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "scc", _here / "score_causal_claims.py")
scc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scc)


def rtl_for(mutant: str) -> str:
    for tier in ("mutants", "mutants_hard"):
        p = _here.parent / "bench" / tier / mutant / "cache_ctrl.sv"
        if p.exists():
            return p.read_text()
    # tier R (docs D6): the block grade scans the concatenated design sources
    real = _here.parent / "bench" / "real" / mutant / "design.json"
    if real.exists():
        d = json.loads(real.read_text())
        return "\n".join((real.parent / "rtl_buggy" / s).read_text()
                         for s in d["sources"])
    return ""


def main() -> int:
    labels_dir = pathlib.Path(sys.argv[1])
    labels = {}
    for f in labels_dir.rglob("*.json"):
        d = json.loads(f.read_text())
        labels[d["mutant"]] = d

    cells = {}
    for summ in sys.argv[2:]:
        p = pathlib.Path(summ)
        run = (p.name.replace(".summaries.json", "")
               if p.parent.name == "results" else p.parent.name)
        for m in json.loads(pathlib.Path(summ).read_text()):
            if m["mutant"] not in labels or m.get("status") == "error":
                continue  # infra `error` rows are re-run by the driver, not scored
            model = m.get("model") or "default"
            tier = "hard" if "hard" in run else \
                   "real" if "real" in run else \
                   "cirfix" if "cirfix" in run else "single"
            grade = scc.score_one(m.get("cause") or {}, labels[m["mutant"]],
                                  rtl_for(m["mutant"]))
            cells.setdefault((model, tier), []).append(
                (m["status"], grade))

    hdr = (f"{'model':22s} {'tier':8s} {'n':>4s} {'repair':>7s} "
           f"{'exact':>6s} {'block':>6s} {'mod':>5s} {'miss':>5s} "
           f"{'exact|fixed':>11s} {'dissoc':>7s}")
    print(hdr)
    print("-" * len(hdr))
    for (model, tier), rows in sorted(cells.items()):
        n = len(rows)
        fixed = [g for s, g in rows if s == "fixed"]
        rep = len(fixed) / n
        def pct(grades, k):
            return 100 * sum(1 for g in grades if g == k) / max(len(grades), 1)
        allg = [g for _, g in rows]
        exf = pct(fixed, "exact")
        print(f"{model:22s} {tier:8s} {n:4d} {100*rep:6.0f}% "
              f"{pct(allg,'exact'):5.0f}% {pct(allg,'block'):5.0f}% "
              f"{pct(allg,'module'):4.0f}% {pct(allg,'miss'):4.0f}% "
              f"{exf:10.0f}% {100*rep - exf:6.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
