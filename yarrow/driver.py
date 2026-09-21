"""YARROW experiment driver.

Runs the loop over one or more benchmark bugs under a fixed per-bug budget,
on a local Ray instance, and records results + full transcripts under runs/.

Example:
  python -m yarrow.driver --mutants all --backend gemini --model gemini-2.5-flash \
      --mutants-dir bench/real --llm-budget 20 --sim-budget 160 --wall-budget 2700 \
      --restarts 3 --tight --cause-footer --cause-loop A --out-dir runs/my_run
"""
import argparse
import datetime
import json
import os
import pathlib
import sys
import time

import ray
from chia.base.ChiaFunction import get

from yarrow import config, ops
from yarrow.budget import BudgetActor
from yarrow.node import total_usage
from yarrow.loop import flat_solve


def detect_failing(mutant_rtl, design_dir=None) -> dict:
    """Run the full suite once on the untouched mutant to establish the
    reference failure evidence (not charged to the run budget)."""
    return get(ops.build_and_test.chia_remote(mutant_rtl, None, design_dir))


def evidence_text(det: dict) -> str:
    lines = [f"Failing suite configs: {', '.join(det['failing']) or '(none)'}"]
    for name in det["failing"][:3]:
        r = det["results"][name]
        lines.append(f"\n--- {name} (rc={r['returncode']}) ---\n"
                     f"{r['output_tail'][-400:]}")
    return "\n".join(lines)


def _load_cause_label(args, mutant_id: str):
    """The first-divergence label for the diagnosis check.
    Tier dir mirrors the mutants dir (single unless --mutants-dir names
    another tier)."""
    if not args.cause_loop:
        return None
    md = args.mutants_dir or ""
    tier = "hard" if "hard" in md else "cirfix" if "cirfix" in md else "single"
    if tier == "single" and md and (pathlib.Path(md) / mutant_id / "design.json").exists():
        tier = "real"
    p = config.BENCH_DIR / "causal_labels" / tier / f"{mutant_id}.json"
    return json.loads(p.read_text()) if p.exists() else None


def run_one(system: str, mutant_id: str, run_dir: pathlib.Path, args) -> dict:
    mdir = pathlib.Path(args.mutants_dir) if args.mutants_dir \
        else config.MUTANTS_DIR
    # Tier R (docs D6): a design.json makes the mutant a multi-file design
    # verified by table testbenches; otherwise the cache_ctrl path as before.
    from yarrow.design import Design
    design = Design.load(mdir / mutant_id)
    design_dir = str(mdir / mutant_id) if design else None
    if design:
        mutant_rtl = design.buggy_texts()
        golden = "\n".join(design.golden_texts().values())
        goal = (f"Debug and repair the bug(s) in the RTL under rtl/ "
                f"({', '.join(design.rtl_paths)}) so every table in the "
                f"regression suite passes.")
    else:
        mutant_rtl = (mdir / mutant_id / "cache_ctrl.sv").read_text()
        golden = config.RTL_GOLDEN.read_text()
        goal = ("Debug and repair the injected bug(s) in "
                "rtl/cache_ctrl.sv so the full regression suite passes.")
    det = detect_failing(mutant_rtl, design_dir)
    if det["all_pass"]:
        return {"mutant": mutant_id, "status": "error",
                "error": "mutant passes the suite; benchmark invalid"}

    # D8 restarts (docs): N independent attempts on a fresh sandbox, each
    # with 1/N of every budget; the first patch that passes independent
    # verification wins. N=1 is the unchanged single-session path.
    n_att = max(1, int(getattr(args, "restarts", 1) or 1))
    per = dict(llm=max(1, args.llm_budget // n_att), sim=max(1, args.sim_budget // n_att),
               wall=args.wall_budget / n_att)
    budget = BudgetActor.remote(per["llm"], per["sim"], per["wall"])
    task = {
        "id": mutant_id if n_att == 1 else f"{mutant_id}.a1",
        "run_dir": str(run_dir),
        "mutant_rtl": mutant_rtl,
        "failing": det["failing"],
        "evidence": evidence_text(det),
        "goal": goal,
        "budget": budget,
        "design_dir": design_dir,
        "cause_label": _load_cause_label(args, mutant_id),
        "cfg": {
            "backend": args.backend, "model": args.model,
            "cause_footer": args.cause_footer or bool(args.cause_loop),
            "cause_loop": args.cause_loop,
            "hygiene": bool(getattr(args, "tight", False)),
        },
    }
    t0 = time.time()
    result = get(flat_solve.chia_remote(task))
    attempts = [result]; spent = [get(budget.snapshot.remote())["spent"]]
    for i in range(2, n_att + 1):
        if result["status"] == "fixed":
            break
        ray.kill(budget)
        budget = BudgetActor.remote(per["llm"], per["sim"], per["wall"])
        task = dict(task, id=f"{mutant_id}.a{i}", budget=budget)
        result = get(flat_solve.chia_remote(task))
        attempts.append(result); spent.append(get(budget.snapshot.remote())["spent"])
    wall = round(time.time() - t0, 1)

    # Regression-free check: a 'fixed' verdict already means full suite green
    # on an independent rebuild; also record patch size vs the golden fix
    # (`golden` was chosen above: cache_ctrl.sv or the design's sources).
    summary = {
        "mutant": mutant_id,
        "system": system,
        "backend": args.backend,
        "model": args.model,
        "status": result["status"],
        "wall_s": wall,
        "usage": (total_usage(result) if n_att == 1 else
                  {k: round(sum(total_usage(a)[k] for a in attempts), 4)
                   for k in ("llm_calls", "sims", "cost_usd")}),
        "budget_final": (get(budget.snapshot.remote()) if n_att == 1 else
                         {"limits_per_attempt": {"llm_calls": per["llm"], "sims": per["sim"]},
                          "attempts": len(attempts), "spent_per_attempt": spent,
                          "attempt_status": [a["status"] for a in attempts]}),
        "failing_configs": det["failing"],
        "cause": result.get("cause", {}),
        "cause_verified": result.get("cause_verified"),
        "cause_rounds": result.get("cause_rounds"),
        "patch_lines": len([l for l in result.get("patch", "").splitlines()
                            if l[:1] in "+-" and l[:3] not in ("+++", "---")]),
        "matches_golden": (result["status"] == "fixed"
                           and _normalized(result, golden, mutant_rtl)),
    }
    (run_dir / "result.json").write_text(
        json.dumps({"summary": summary, "tree": result}, indent=2,
                   default=str) + "\n")
    ray.kill(budget)
    return summary


def _normalized(result, golden, mutant_rtl) -> bool:
    """Did the agent's fix restore exactly the golden text (modulo
    whitespace)? Semantic equivalence is what verification checks; this is
    just a bonus metric."""
    import re as _re
    patched = None
    # reconstruct patched file from the diff by re-reading sandbox copy is
    # not possible here; compare via patch text heuristic instead
    norm = lambda s: _re.sub(r"\s+", " ", s).strip()
    # count as exact match if patch, applied to mutant, yields golden --
    # approximated by: golden's changed line appears as a '+' line.
    plus = [l[1:] for l in result.get("patch", "").splitlines()
            if l.startswith("+") and not l.startswith("+++")]
    return any(norm(p) and norm(p) in norm(golden) for p in plus)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", choices=["flat"], default="flat",
                    help="kept for command compatibility; the loop is flat_solve")
    ap.add_argument("--mutants", default="all",
                    help="comma-separated mutant ids, or 'all'")
    ap.add_argument("--backend", choices=sorted(config.BACKEND_DEFAULT_MODEL),
                    default="claude")
    ap.add_argument("--model", default=None)
    ap.add_argument("--llm-budget", type=int, default=12)
    ap.add_argument("--sim-budget", type=int, default=80)
    ap.add_argument("--wall-budget", type=float, default=3600.0,
                    help="wall-clock budget per mutant, seconds")
    ap.add_argument("--cause-footer", action="store_true",
                    help="require the CAUSE_* diagnosis footer")
    ap.add_argument("--tight", action="store_true",
                    help="full loop tools for multi-file designs: lint-gated ./edit, "
                         "./reset.sh and ./firstdiff.sh self-replay + matching prompts")
    ap.add_argument("--restarts", type=int, default=1,
                    help="N independent attempts per bug, each on a fresh "
                         "sandbox with 1/N of each budget; first verified fix wins")
    ap.add_argument("--cause-loop", choices=["A", "B", "C", "D"], default=None,
                    help="diagnosis check after a passing patch: A = 'claim is "
                         "wrong, re-examine', B = A + divergence cycle; ablations "
                         "C/D put the verdict (C) or the label set (D) in the "
                         "failing-patch feedback instead; implies --cause-footer")
    ap.add_argument("--mutants-dir", default=None,
                    help="alternate mutant directory (e.g. bench/mutants_hard)")
    ap.add_argument("--out-dir", default=None,
                    help="fixed output dir; mutants with an existing "
                         "result.json there are skipped (resume support)")
    a = ap.parse_args()

    mdir = pathlib.Path(a.mutants_dir) if a.mutants_dir else config.MUTANTS_DIR
    if a.mutants == "all":
        mutants = sorted(d.name for d in mdir.iterdir()
                         if (d / "cache_ctrl.sv").exists()
                         or (d / "design.json").exists())
    else:
        mutants = [m.strip() for m in a.mutants.split(",") if m.strip()]

    if a.out_dir:
        out_root = pathlib.Path(a.out_dir)
    else:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        label = (f"{stamp}_{a.system}_{a.backend}"
                 + (f"_{a.tag}" if a.tag else ""))
        out_root = config.RUNS_DIR / label
    out_root.mkdir(parents=True, exist_ok=True)

    if os.environ.get("RAY_ADDRESS"):
        # Joining an existing cluster (chia job submit): pool resources are
        # declared by the nodes' `ray start --resources=...`, and passing
        # resources/num_cpus here would be rejected by ray.init.
        ray.init(ignore_reinit_error=True, log_to_driver=False)
    else:
        ray.init(resources=dict(config.RAY_RESOURCES), num_cpus=16,
                 ignore_reinit_error=True, log_to_driver=False)

    # resume: reload prior summaries, skip completed mutants
    summaries = []
    done = set()
    prior = out_root / "summaries.json"
    if a.out_dir and prior.exists():
        summaries = [s for s in json.loads(prior.read_text())
                     if s.get("status") != "error"]
        done = {s["mutant"] for s in summaries}

    for m in mutants:
        if m in done:
            print(f"=== {a.system} / {m} === (already done, skipping)",
                  flush=True)
            continue
        run_dir = out_root / m
        run_dir.mkdir(parents=True, exist_ok=True)
        print(f"=== {a.system} / {m} ===", flush=True)
        try:
            s = run_one(a.system, m, run_dir, a)
        except Exception as e:  # one bad mutant must not kill the campaign
            import traceback
            s = {"mutant": m, "system": a.system, "status": "error",
                 "error": f"{e}\n{traceback.format_exc()[-1500:]}"}
        summaries.append(s)
        print(json.dumps({k: v for k, v in s.items() if k != "budget_final"},
                         indent=2), flush=True)
        (out_root / "summaries.json").write_text(
            json.dumps(summaries, indent=2, default=str) + "\n")

    fixed = sum(1 for s in summaries if s.get("status") == "fixed")
    print(f"\n{fixed}/{len(summaries)} repaired. Results in {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
