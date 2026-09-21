#!/usr/bin/env python3
"""S-vs-R tier comparison and the pre-registered D6 predictions.

Usage: report_tiers.py <labels_dir> <summaries.json ...>

Groups runs by (model, tier, repeat) — tier and repeat inferred from the run
name (`cause_<model>_<tier>_r<N>`, `d5_<model>_<tier>_<A|B>_r<N>`) — and
reports per model x tier: repair, exact|fixed (frozen set-exact), the
declared secondary state-exact|fixed, and the per-repeat spread.  Then
evaluates, where data exists:
  P1  repair(R) is >= 15 pp below repair(S) at the top of the ladder
  P2  exact|fixed(R) is not lower than exact|fixed(S)
  P3  the D5 tier-A loop raises repair on R more than on S
Scoring comes from score_causal_claims.score_one (frozen); state-exact is
claim base name == label first_divergence base name (yarrow.causal.base_name).
"""
import importlib.util
import json
import pathlib
import re
import sys
from collections import defaultdict

_here = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_here.parent))
spec = importlib.util.spec_from_file_location("scc", _here / "score_causal_claims.py")
scc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scc)
from yarrow.causal import base_name  # noqa: E402

RUN_RE = re.compile(r"^(?P<prefix>cause|d5|rs|rsA|tight)_(?P<model>[a-zA-Z0-9]+)_(?P<tier>single|hard|real|cirfix)"
                    r"(?:_(?P<arm>[A-D]))?_r(?P<rep>\d+)$")
# flashP = the §4.2 clean-ladder flash rerun (probe.sh present in the prompt,
# unlike the original flash S cells, which are the no-probe ablation arm).
MODEL_NAMES = {"flash": "gemini-2.5-flash", "flashP": "flash+probe", "25pro": "gemini-2.5-pro",
               "31pro": "gemini-3.1-pro"}


def rtl_for(mutant: str) -> str:
    for tier in ("mutants", "mutants_hard"):
        p = _here.parent / "bench" / tier / mutant / "cache_ctrl.sv"
        if p.exists():
            return p.read_text()
    dj = _here.parent / "bench" / "real" / mutant / "design.json"
    if dj.exists():
        d = json.loads(dj.read_text())
        return "\n".join((dj.parent / "rtl_buggy" / s).read_text() for s in d["sources"])
    return ""


def pct(a, b):
    return 100.0 * a / b if b else float("nan")


def main() -> int:
    labels = {}
    for f in pathlib.Path(sys.argv[1]).rglob("*.json"):
        d = json.loads(f.read_text())
        labels[d["mutant"]] = d

    # cells[(model, tier, arm)][rep] = list of (fixed, exact, state_exact)
    cells = defaultdict(lambda: defaultdict(list))
    for summ in sys.argv[2:]:
        p = pathlib.Path(summ)
        run = p.name.replace(".summaries.json", "") if p.parent.name == "results" else p.parent.name
        m = RUN_RE.match(run)
        if not m:
            continue
        key = (m["model"], m["tier"], m["arm"] or ("rsA" if m["prefix"] == "rsA" else "rs" if m["prefix"] == "rs" else "T" if m["prefix"] == "tight" else "open"))
        for r in json.loads(p.read_text()):
            lab = labels.get(r["mutant"])
            if not lab or r.get("status") == "error":
                continue  # infra errors (429, context overflow) are re-run, never scored
            claim = r.get("cause") or {}
            fixed = r["status"] == "fixed"
            exact = scc.score_one(claim, lab, rtl_for(r["mutant"])) == "exact"
            sexact = base_name(claim.get("signal", "")) == base_name(lab["first_divergence"]["signal"])
            cells[key][int(m["rep"])].append((fixed, exact, sexact))

    print(f"{'model':10} {'tier':7} {'arm':5} {'reps':>4} {'n':>4} {'repair':>7} {'exact|fix':>9} {'fix&reason':>10} "
          f"{'state|fix':>9}  per-repeat exact|fixed")
    agg = {}
    for key in sorted(cells):
        reps = cells[key]
        rows = [x for rep in reps.values() for x in rep]
        n_fix = sum(f for f, _, _ in rows)
        rep_exf = [pct(sum(e for f, e, _ in rep if f), sum(f for f, _, _ in rep)) for rep in reps.values()]
        agg[key] = {"repair": pct(n_fix, len(rows)),
                    "joint": pct(sum(1 for f, e, _ in rows if f and e), len(rows)),
                    "exf": pct(sum(e for f, e, _ in rows if f), n_fix),
                    "sexf": pct(sum(s for f, _, s in rows if f), n_fix),
                    "n": len(rows), "reps": len(reps)}
        a = agg[key]
        print(f"{MODEL_NAMES.get(key[0], key[0]):10} {key[1]:7} {key[2]:5} {a['reps']:4d} {a['n']:4d} "
              f"{a['repair']:6.0f}% {a['exf']:8.0f}% {a['joint']:9.0f}% {a['sexf']:8.0f}%  "
              + " ".join(f"{x:.0f}" for x in rep_exf))

    print("\nPRE-REGISTERED PREDICTIONS (docs D6):")
    for model in ("flash", "25pro", "31pro"):
        s, r = agg.get((model, "single", "open")), agg.get((model, "real", "open"))
        if not (s and r):
            continue
        d_rep = s["repair"] - r["repair"]
        d_exf = r["exf"] - s["exf"]
        print(f"  {MODEL_NAMES[model]}: repair S {s['repair']:.0f}% -> R {r['repair']:.0f}% "
              f"({-d_rep:+.0f} pp)  P1 {'HOLDS' if d_rep >= 15 else 'FAILS'} (needs >= 15 pp drop; "
              f"pre-registered for 31pro)")
        print(f"  {MODEL_NAMES[model]}: exact|fixed S {s['exf']:.0f}% -> R {r['exf']:.0f}% "
              f"({d_exf:+.0f} pp)  P2 {'HOLDS' if d_exf >= 0 else 'FAILS'} (R not lower than S)")
        sA, rA = agg.get((model, "single", "A")), agg.get((model, "real", "A"))
        if sA and rA:
            up_s, up_r = sA["repair"] - s["repair"], rA["repair"] - r["repair"]
            print(f"  {MODEL_NAMES[model]}: D5-A repair uplift S {up_s:+.0f} pp, R {up_r:+.0f} pp  "
                  f"P3 {'HOLDS' if up_r > up_s else 'FAILS'}")
        elif rA:
            print(f"  {MODEL_NAMES[model]}: D5-A on R: repair {r['repair']:.0f}% -> {rA['repair']:.0f}% "
                  f"({rA['repair']-r['repair']:+.0f} pp); no S-tier D5-A cell for this model to compare")
    return 0


if __name__ == "__main__":
    sys.exit(main())
