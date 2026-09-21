## Task

The design fails the regression suite. Find the injected bug(s) in
`rtl/cache_ctrl.sv`, fix them with minimal changes, and verify.

## Failure evidence

$evidence

## What to do

Investigate (read RTL, run `./sim.sh` probes), edit `rtl/cache_ctrl.sv`,
and verify with `./test.sh`. When the failing configs pass, run the FULL
suite: `./test.sh default evict_bp_heavy read_fastmem mixed_midlat
write_storm read_only long_soak smoke`. Only claim PATCH_READY when the
full suite passes in your sandbox.

End your reply with exactly this footer:

```
EXPLANATION: <1-3 sentences: root cause and the change you made>
STATUS: PATCH_READY | STUCK
```
