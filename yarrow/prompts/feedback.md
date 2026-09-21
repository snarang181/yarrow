## Independent verification FAILED

Your patch was rebuilt and re-run independently. Result:

$verify_report

Your sandbox `rtl/cache_ctrl.sv` still contains your last edit. Diagnose why
the independent run disagrees with your sandbox run (most often: the fix is
incomplete and fails a config or seed you did not exercise), fix the RTL,
and re-verify with the FULL suite via `./test.sh default evict_bp_heavy
read_fastmem mixed_midlat write_storm read_only long_soak smoke`.

End your reply with exactly this footer:

```
EXPLANATION: <what was wrong and what you changed>
STATUS: PATCH_READY | STUCK
```
