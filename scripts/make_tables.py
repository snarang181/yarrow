#!/usr/bin/env python3
"""Regenerate results/causal_tables.txt from results/*.summaries.json.

Frozen scoring via scripts/report_causal.py (ladder) and scripts/report_tiers.py
(per-repeat spread, state-exact, pre-registered predictions). The clean-ladder
flash rerun (cause_flashP_*) is reported as its own ablation block and is
NOT pooled into the flash ladder cells. Files listed in EXCLUDE never enter
any table (unrun cells kept in results/ only for the record).
"""
import datetime
import glob
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
LABELS = str(ROOT / "bench" / "causal_labels")
EXCLUDE = set()          # explicit exclusions by file name, if ever needed
MIN_VALID_FRACTION = 0.5  # a cell with fewer valid rows than this is UNRUN, not data


def _valid_fraction(path: str) -> float:
    import json
    rows = json.loads(pathlib.Path(path).read_text())
    return sum(1 for r in rows if r.get("status") != "error") / max(1, len(rows))


def files(pattern: str) -> list:
    out = []
    for f in sorted(glob.glob(str(ROOT / "results" / pattern))):
        if pathlib.Path(f).name in EXCLUDE:
            continue
        if _valid_fraction(f) < MIN_VALID_FRACTION:
            print(f"note: {pathlib.Path(f).name} is mostly error rows (unrun cell); excluded",
                  file=sys.stderr)
            continue
        out.append(f)
    return out


def run(script: str, args: list) -> str:
    return subprocess.run([PY, str(ROOT / "scripts" / script), LABELS] + args,
                          capture_output=True, text=True, check=True).stdout.rstrip()


def ladder(patterns: list) -> str:
    fs = [f for p in patterns for f in files(p)]
    return run("report_causal.py", fs)


def main() -> int:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    s_files = ["cause_flash_single_r*.summaries.json", "cause_flash_hard_r*.summaries.json",
               "cause_25pro_single_r*.summaries.json", "cause_25pro_hard_r*.summaries.json",
               "cause_31pro_single_r*.summaries.json", "cause_31pro_hard_r*.summaries.json"]
    r_files = ["cause_flash_real_r*.summaries.json", "cause_25pro_real_r*.summaries.json",
               "cause_31pro_real_r*.summaries.json"]
    x_files = ["cause_flash_cirfix_r*.summaries.json", "cause_31pro_cirfix_r*.summaries.json"]
    out = [f"== YARROW-Causal — all tables regenerated {stamp} (Gemini/Vertex only) ==",
           "", "== D3 ladder: tier S (synthetic cache_ctrl mutants, n=2) ==", ladder(s_files),
           "", "== D3 ladder: tier R (14 field bugs, RTL-Repair fpga-debugging; flash and 3.1-pro n=2, 2.5-pro n=1) ==",
           ladder(r_files),
           "", "== D3 ladder: tier X (24 externally-authored CirFix synthetic bugs; control for experimenter bias) ==",
           ladder(x_files) if any(files(p) for p in x_files) else "(not measured)",
           "",
           "NOTES: flash R r2 has 12 of 14 cells (zipcpu-spi-c3/d9 did not run: Vertex",
           "billing was disabled when the credit ran out); 3.1-pro R is 28/28. exact =",
           "claimed base name in the D1b first-divergence set; block = same always",
           "block; module = same module type. R label sets have 1-4 members (S up to 20).",
           "", "== D5 causal-closure loop ==",
           "flash, S single (probe harness):",
           run("report_causal.py", files("d5_flash_single_A_r*.summaries.json")).splitlines()[-1] + "   <- tier A",
           run("report_causal.py", files("d5_flash_single_B_r*.summaries.json")).splitlines()[-1] + "   <- tier B",
           "3.1-pro, R (tier A):",
           run("report_causal.py", files("d5_31pro_real_A_r*.summaries.json")).splitlines()[-1],
           "  26 valid cells (2 retry-turn context overflows >1,048,576 tokens, reported as",
           "  errors): 26 fixed; claim verified 24/26 = 92% among 26 valid evaluations, 86% of",
           "  all 28 attempts (open loop 64%); first-try 20/26;",
           "  6 entered the loop, 4 closed (d4 x2, spi-c1 x2 after 1-2 rounds), 2 did not",
           "  (spi-d9 wall-limited; c4 used both rounds).",
           *(["3.1-pro, R (tier B, cycle hint):",
              run("report_causal.py", files("d5_31pro_real_B_r*.summaries.json")).splitlines()[-1]]
             if files("d5_31pro_real_B_r*.summaries.json") else []),
           "flash, R (tier A):",
           *([run("report_causal.py", files("d5_flash_real_A_r*.summaries.json")).splitlines()[-1],
              "  repair 29% vs 54% open loop (the check runs only after a green patch; it grounds",
              "  fixes, it does not create them); 7 of 8 fixes verified."]
             if files("d5_flash_real_A_r*.summaries.json") else ["  (unrun)"]),
           "", "== D7 diagnosis feedback in the RED path (oracle localization, arm D; n=1) ==",
           *([run("report_causal.py", files("d5_flash_real_D_r*.summaries.json")).splitlines()[-1],
              "  repair 50% vs 54% open loop (P4 fails); exact|fixed is trivial under D (label shown)."]
             if files("d5_flash_real_D_r*.summaries.json") else ["  (unrun)"]),
           "", "== D8 closed loop: 3 restarts (budget split) + Feedback A; tier R ==",
           *[run("report_causal.py", files(p)).splitlines()[-1] for p in
             ("rsA_flash_real_r*.summaries.json", "rsA_25pro_real_r*.summaries.json",
              "rsA_31pro_real_r*.summaries.json") if files(p)],
           "  right fix AND right reason per scheduled bug: see report_tiers joint column;",
           "  flash 43% (open 27%), 2.5-pro 54% of 13 valid (open 57%; 1 context-overflow error row), 3.1-pro 86% (open 64%).",
           "", "== D9 tight loop: 3 restarts + lint-gated edit/reset + firstdiff self-replay + Feedback A; tier R ==",
           *[run("report_causal.py", files(p)).splitlines()[-1] for p in
             ("tight_flash_real_r*.summaries.json", "tight_25pro_real_r*.summaries.json",
              "tight_31pro_real_r*.summaries.json") if files(p)],
           "  right fix AND right reason per scheduled bug: report_tiers 'fix&reason' column (arm T).",
           "", "== Ablation: clean-ladder flash rerun WITH probe.sh (not pooled above) ==",
           ladder(["cause_flashP_single_r*.summaries.json"]).splitlines()[-1] + "   <- single, with probe (no-probe: 93/74)",
           ladder(["cause_flashP_hard_r*.summaries.json"]).splitlines()[-1] + "   <- hard, with probe (no-probe: 77/48)",
           "", "== Per-repeat spread, state-exact secondary, pre-registered predictions ==",
           run("report_tiers.py", files("cause_*.summaries.json") + files("d5_*.summaries.json")),
           ]
    text = "\n".join(out) + "\n"
    (ROOT / "results" / "causal_tables.txt").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
