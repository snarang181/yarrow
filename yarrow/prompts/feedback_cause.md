## Independent verification FAILED

Your patch was rebuilt and re-run independently. Result:

$verify_report

Your sandbox `rtl/cache_ctrl.sv` still contains your last edit. Diagnose why
the independent run disagrees with your sandbox run (most often: the fix is
incomplete and fails a config or seed you did not exercise), fix the RTL,
and re-verify with the FULL suite via `./test.sh default evict_bp_heavy
read_fastmem mixed_midlat write_storm read_only long_soak smoke`.

Besides fixing the bug, you must identify its root cause precisely: the
FIRST architectural state element that takes a wrong value because of the
bug (not downstream symptoms — the earliest corrupted signal), the module
containing it, and the triggering condition.

End your reply with exactly this footer:

```
EXPLANATION: <what was wrong and what you changed>
CAUSE_SIGNAL: <name of the first corrupted state signal, e.g. dirty_arr>
CAUSE_MODULE: <module containing it, e.g. cache_ctrl>
CAUSE_EVENT: <one line: the condition/state under which it first corrupts>
CAUSE_MECHANISM: <one word/tag: e.g. dirty, replacement, addressing,
tag_match, backpressure, ordering, reset, fsm, datapath, arbiter>
STATUS: PATCH_READY | STUCK
```
