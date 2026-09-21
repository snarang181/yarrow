## Causal claim REJECTED (patch is fine)

Your patch passed independent verification and stands. But your causal
claim was checked mechanically against a golden-reference replay of the
original failure, and the signal you named is NOT the first element to
diverge from correct behavior.
$cycle_hint

Re-examine the ORIGINAL bug (your sandbox `rtl/cache_ctrl.sv` now contains
your fix — reason from the diff you made and the failure evidence, and use
`./probe.sh <signal-substring> [start_cycle] [end_cycle]` to inspect signal
activity if needed). Identify the FIRST architectural state or control
signal that took a wrong value because of the original bug — the earliest
corruption, not a downstream symptom.

Reply with ONLY the corrected footer:

```
CAUSE_SIGNAL: <name of the first corrupted signal>
CAUSE_MODULE: <module containing it>
CAUSE_EVENT: <one line: the condition under which it first corrupts>
CAUSE_MECHANISM: <one word/tag>
STATUS: PATCH_READY
```
