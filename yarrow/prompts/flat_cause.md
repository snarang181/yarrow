## Task

The design fails the regression suite. Find the injected bug(s) in
`rtl/cache_ctrl.sv`, fix them with minimal changes, and verify.

## Failure evidence

$evidence

## What to do

Investigate (read RTL, run `./sim.sh` probes, and inspect signal activity
with `./probe.sh <signal-substring> [start_cycle] [end_cycle]` — it traces
one failing run and prints when matching signals change), edit
`rtl/cache_ctrl.sv`, and verify with `./test.sh`. When the failing configs pass, run the FULL
suite: `./test.sh default evict_bp_heavy read_fastmem mixed_midlat
write_storm read_only long_soak smoke`. Only claim PATCH_READY when the
full suite passes in your sandbox.

## Causal claim

Besides fixing the bug, you must identify its root cause precisely: the
FIRST architectural state element that takes a wrong value because of the
bug (not downstream symptoms — the earliest corrupted signal), the module
containing it, and the triggering condition.

End your reply with exactly this footer:

```
EXPLANATION: <1-3 sentences: root cause and the change you made>
CAUSE_SIGNAL: <name of the first corrupted state signal, e.g. dirty_arr>
CAUSE_MODULE: <module containing it, e.g. cache_ctrl>
CAUSE_EVENT: <one line: the condition/state under which it first corrupts>
CAUSE_MECHANISM: <one word/tag: e.g. dirty, replacement, addressing,
tag_match, backpressure, ordering, reset, fsm, datapath, arbiter>
STATUS: PATCH_READY | STUCK
```
