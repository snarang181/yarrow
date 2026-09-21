#!/usr/bin/env bash
# YARROW quick start.
#   scripts/quickstart.sh setup    install CHIA + Python deps into .venv (needs uv, Verilator 5.x)
#   scripts/quickstart.sh check    validate the benchmark (reference passes, every bug is caught)
#   scripts/quickstart.sh demo     offline walk-through of one field bug: sandbox, tools, self-replay
#   scripts/quickstart.sh run      run the full YARROW loop on one bug with Gemini (needs gcloud ADC)
#   scripts/quickstart.sh all      setup + check + demo
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
BUG=${BUG:-axi-lite-s1b}
MODEL=${MODEL:-gemini-2.5-flash}

setup() {
  command -v uv >/dev/null || { echo "install uv first: https://docs.astral.sh/uv/"; exit 1; }
  command -v verilator >/dev/null || { echo "install Verilator 5.x and put it on PATH"; exit 1; }
  [ -d chia ] || git clone --depth 1 https://github.com/ucb-bar/chia chia
  [ -d .venv ] || uv venv .venv --python 3.12 --seed
  uv pip install --python $PY -e ./chia openai matplotlib
  echo "setup done: $($PY -c 'import chia, sys; print(sys.version.split()[0])')"
}

check() {
  $PY scripts/validate_bench.py            # synthetic tier: golden passes, all mutants killed
  $PY scripts/validate_real.py             # field tier: reference passes, faulty fails, 14/14
}

demo() {
  echo "== $BUG: build a hygiene sandbox (edit / reset / firstdiff tools), no model involved"
  $PY - <<PYEOF
import pathlib, shutil, subprocess, json
from yarrow import ops
from yarrow.design import Design
bug = "$BUG"; sb = pathlib.Path("runs/quickstart_demo") / bug
shutil.rmtree(sb, ignore_errors=True)
d = Design.load(f"bench/real/{bug}")
ops.make_sandbox(sb, d.buggy_texts(), [d.tables[0]["name"]], design_dir=f"bench/real/{bug}", hygiene=True)
run = lambda *a: subprocess.run(list(a), cwd=sb, capture_output=True, text=True).stdout.strip()
print("\n-- 1. the faulty design fails its table:"); print(run("./test.sh")[-160:])
print("\n-- 2. a lint-gated edit that breaks compilation is rolled back:")
src = d.sources[0]; first = (sb/"rtl"/src).read_text().splitlines()[0]
print(subprocess.run(["./edit","lines",src,"1","1"], cwd=sb, capture_output=True, text=True, input="module broken ( ((( ;\n").stdout.strip()[:200])
print("\n-- 3. apply the reference fix as if the agent had written it:")
for s, txt in d.golden_texts().items(): (sb/"rtl"/s).write_text(txt)
print(run("./test.sh")[-120:])
print("\n-- 4. self-replay: first internal signal that differs between original and patched design:")
print(run("./firstdiff.sh"))
lab = json.loads(pathlib.Path(f"bench/causal_labels/real/{bug}.json").read_text())["first_divergence"]
print(f"\n-- 5. hidden reference label (what the diagnosis check compares against): {lab['signal']} at row {lab['cycle']}")
print("\nsandbox kept at", sb)
PYEOF
}

run() {
  gcloud auth application-default print-access-token >/dev/null 2>&1 || { echo "gcloud ADC not configured (gcloud auth application-default login)"; exit 1; }
  $PY -m yarrow.driver --mutants "$BUG" --backend gemini --model "$MODEL" --mutants-dir bench/real \
    --llm-budget 20 --sim-budget 160 --wall-budget 2700 --restarts 3 --tight --cause-footer --cause-loop A \
    --out-dir runs/quickstart_$BUG
  $PY -c "import json; r=json.load(open('runs/quickstart_$BUG/summaries.json'))[0]; print({k:r[k] for k in ('mutant','status','cause_verified','usage')})"
}

case "${1:-all}" in
  setup) setup ;; check) check ;; demo) demo ;; run) run ;;
  all) setup; check; demo ;;
  *) sed -n 2,8p "$0"; exit 2 ;;
esac
