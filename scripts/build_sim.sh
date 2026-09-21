#!/usr/bin/env bash
# Build the Verilator simulation for a given RTL file.
# Usage: build_sim.sh <rtl_file> <out_dir> [tb_file]
set -euo pipefail
RTL=${1:-rtl/cache_ctrl.sv}
OUT=${2:-obj_dir}
TB=${3:-}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -z "$TB" ]]; then
  TB="$ROOT/tb/tb_main.cpp"
fi
TRACE_ARGS=()
if [[ "$(basename "$TB")" == "tb_replay.cpp" ]]; then
  TRACE_ARGS=(--trace --trace-structs --trace-max-array 1024)
fi
verilator --cc "$RTL" --exe "$TB" "${TRACE_ARGS[@]}" \
  --Mdir "$OUT" -o sim --build -j 4 \
  -Wall -Wno-fatal -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM --x-assign unique --x-initial unique \
  > "$OUT.build.log" 2>&1 || { cat "$OUT.build.log"; exit 3; }
echo "built: $OUT/sim"
