#!/usr/bin/env bash
# Build a Verilator sim for an arbitrary multi-file design (real-bug tier).
# build_sim.sh is left untouched: it is the frozen harness of the cache_ctrl
# tiers.  This one takes the top module and a source list, always traces
# (the label pipeline and probe.sh need all-signal VCDs), and zero-inits
# state so row 0 of a table testbench sees the same initial state the
# table's recorder/model-checker saw.
# Usage: build_design.sh <out_dir> <tb.cpp> <top_module> <src.v>...
set -euo pipefail
OUT=$1; TB=$2; TOP=$3; shift 3
XINIT=${YARROW_XINIT:-0}
verilator --cc "$@" --top-module "$TOP" --exe "$TB" \
  --trace --trace-structs --trace-max-array 1024 \
  --Mdir "$OUT" -o sim --build -j 4 \
  -Wall -Wno-fatal -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM -Wno-DECLFILENAME \
  --no-timing --relative-includes \
  --x-assign "$XINIT" --x-initial "$XINIT" \
  > "$OUT.build.log" 2>&1 || { cat "$OUT.build.log"; exit 3; }
echo "built: $OUT/sim"
