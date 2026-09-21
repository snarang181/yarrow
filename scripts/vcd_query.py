#!/usr/bin/env python3
"""Print compact value-change rows for matching cache-controller VCD signals.

Usage: vcd_query.py <vcd> <signal-substring> [start_cycle] [end_cycle]
"""
import pathlib
import sys

# Keep this parser shared with the D1/D1b trace format rather than relying on
# a system VCD package.  The replay harness traces the DUT below this scope.


def timed_trace_snapshots(path, signals=None):
    """Return snapshots at each VCD dump for the design's internal signals.

    ``signals`` is an optional regex over the local signal name; this is the
    same parser used by ``gen_causal_labels.py`` for replay-trace alignment.

    Scope selection (docs D6): if the trace has a ``TOP.cache_ctrl`` scope the
    behavior is exactly the original — that scope only, names
    ``TOP.cache_ctrl.<sig>`` — so every cache_ctrl-tier probe and label is
    byte-identical.  Otherwise (tier R designs) every scope strictly below
    ``TOP`` is recorded with its full hierarchical name; the bare ``TOP``
    scope is skipped because it only mirrors the top-level ports.
    """
    text = pathlib.Path(path).read_text()
    legacy = "$scope module cache_ctrl" in text.split("$enddefinitions", 1)[0]
    codes, scopes, in_defs = {}, [], True
    current, snapshots = {}, []
    current_time = None
    for raw in text.splitlines():
        line = raw.strip()
        if in_defs:
            if line.startswith("$scope"):
                scopes.append(line.split()[2])
            elif line.startswith("$upscope"):
                scopes.pop()
            elif line.startswith("$var"):
                bits = line.split()
                name = bits[4]
                path_ = ".".join(scopes)
                keep = (path_ == "TOP.cache_ctrl") if legacy else \
                    (len(scopes) >= 2 and scopes[0] == "TOP")
                if keep and (signals is None or signals.match(name)):
                    codes[bits[3]] = path_ + "." + name
            elif line == "$enddefinitions $end":
                in_defs = False
            continue
        if not line or line.startswith("$"):
            continue
        if line.startswith("#"):
            if current_time is not None:
                snapshots.append((current_time, current.copy()))
            current_time = int(line[1:])
            continue
        if line[0] in "01xXzZ":
            value, code = line[0].lower(), line[1:]
        elif line[0] in "bBrR":
            value, code = line[1:].split()
            value = value.lower()
        else:
            continue
        if code in codes:
            current[codes[code]] = value
    if current_time is not None:
        snapshots.append((current_time, current.copy()))
    return snapshots


def print_changes(path, needle, start=0, end=None, cap=200):
    """Print matching value changes in the inclusive VCD-cycle window."""
    previous = {}
    shown = 0
    matched = False
    for cycle, snapshot in timed_trace_snapshots(path):
        for signal in sorted(snapshot):
            value = snapshot[signal]
            old = previous.get(signal)
            if needle in signal and old != value:
                matched = True
                if cycle >= start and (end is None or cycle <= end):
                    if shown == 0:
                        print("cycle signal old -> new")
                    print(f"{cycle} {signal} {old if old is not None else '-'} -> {value}")
                    shown += 1
                    if shown >= cap:
                        print(f"[probe output truncated at {cap} value changes]")
                        return
            previous[signal] = value
    if not matched:
        print(f"[no value changes matching {needle!r}]")
    elif shown == 0:
        print(f"[no matching value changes in cycles {start}..{end if end is not None else 'end'}]")


def main():
    if not 3 <= len(sys.argv) <= 5:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        start = int(sys.argv[3]) if len(sys.argv) >= 4 else 0
        end = int(sys.argv[4]) if len(sys.argv) >= 5 else None
    except ValueError:
        print("start_cycle and end_cycle must be integers", file=sys.stderr)
        return 2
    print_changes(sys.argv[1], sys.argv[2], start, end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
