## Independent verification FAILED

Your patch was rebuilt and re-run independently. Result:

$verify_report

Your sandbox RTL under `rtl/` still contains your last edit. Diagnose why
the independent run disagrees with your sandbox run (most often: the fix is
incomplete and fails a table you did not exercise), fix the RTL, and
re-verify with the FULL suite via `./test.sh $full_suite`.

After the FULL suite passes, run `./firstdiff.sh` and take CAUSE_SIGNAL from its output (earliest differing register, else the first listed signal). Besides fixing the bug, you must identify its root cause precisely: the
FIRST architectural state element that takes a wrong value because of the
bug (not downstream symptoms — the earliest corrupted signal), the module
containing it, and the triggering condition.

End your reply with exactly this footer:

```
EXPLANATION: <what was wrong and what you changed>
CAUSE_SIGNAL: <name of the first corrupted state signal>
CAUSE_MODULE: <module containing it, e.g. $top>
CAUSE_EVENT: <one line: the condition/state under which it first corrupts>
CAUSE_MECHANISM: <one word/tag: e.g. handshake, counter, fsm, reset,
addressing, backpressure, ordering, datapath, arbiter>
STATUS: PATCH_READY | STUCK
```
