## Task

The design fails the regression suite. Find the bug(s) in the RTL under
`rtl/` ($rtl_files), fix them with minimal changes, and verify.

## Failure evidence

$evidence

## What to do

Investigate (read the RTL and the failing table in `tb/`, run `./sim.sh
<config>`, and inspect signal activity with `./probe.sh <signal-substring>
[start_row] [end_row]` — it traces the failing table and prints when
matching signals change, by table row), edit the RTL under `rtl/`, and
verify with `./test.sh`. When the failing configs pass, run the FULL suite:
`./test.sh $full_suite`. Only claim PATCH_READY when the full suite passes
in your sandbox.

## Causal claim

Besides fixing the bug, you must identify its root cause precisely: the
FIRST architectural state element that takes a wrong value because of the
bug (not downstream symptoms — the earliest corrupted signal), the module
containing it, and the triggering condition. After the FULL suite passes,
run `./firstdiff.sh` and take CAUSE_SIGNAL from its output (the earliest
differing register if there is one, otherwise the first listed signal);
explain the mechanism in your own words.

End your reply with exactly this footer:

```
EXPLANATION: <1-3 sentences: root cause and the change you made>
CAUSE_SIGNAL: <name of the first corrupted state signal>
CAUSE_MODULE: <module containing it, e.g. $top>
CAUSE_EVENT: <one line: the condition/state under which it first corrupts>
CAUSE_MECHANISM: <one word/tag: e.g. handshake, counter, fsm, reset,
addressing, backpressure, ordering, datapath, arbiter>
STATUS: PATCH_READY | STUCK
```
