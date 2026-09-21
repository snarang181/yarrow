#!/usr/bin/env python3
"""Lint-gated edit tool for the agent sandbox (arm H, docs D8).

    ./edit replace FILE 'OLD' 'NEW'      exact-string replace; OLD must occur exactly once
    ./edit lines   FILE START END        replace lines START..END (1-based, inclusive) with stdin
    ./edit lint                          lint rtl/ only
    ./reset.sh [FILE ...]                restore original source(s) (separate script)

After every edit the whole design is linted with Verilator (same flags as the
build). If lint fails the edit is rolled back and the error is printed:
"EDIT REJECTED". Nothing else in the sandbox is touched.
"""
import json, pathlib, subprocess, sys

SB = pathlib.Path(__file__).resolve().parent if False else pathlib.Path.cwd()

def cfg():
    return json.loads((SB / ".design.json").read_text())

def lint() -> subprocess.CompletedProcess:
    c = cfg()
    srcs = [f"rtl/{s}" for s in c["sources"]]
    return subprocess.run(["verilator", "--lint-only", "-Wall", "-Wno-fatal",
                           "-Wno-UNUSEDSIGNAL", "-Wno-UNUSEDPARAM", "-Wno-DECLFILENAME",
                           "--no-timing", "--relative-includes", "--top-module", c["top"]] + srcs,
                          capture_output=True, text=True, cwd=SB)

def _target(name: str) -> pathlib.Path:
    p = pathlib.Path(name)
    if not str(p).startswith("rtl/"):
        p = pathlib.Path("rtl") / p.name
    if p.name not in cfg()["sources"]:
        sys.exit(f"EDIT REJECTED: {name} is not a design source (allowed: "
                 + ", ".join(cfg()["sources"]) + ")")
    return SB / p

def _apply(path: pathlib.Path, new_text: str) -> int:
    old = path.read_text()
    path.write_text(new_text)
    r = lint()
    if r.returncode != 0:
        path.write_text(old)
        err = "\n".join(l for l in (r.stderr + r.stdout).splitlines() if "%Error" in l)[-1500:]
        print("EDIT REJECTED: the design no longer compiles; the file was restored.\n" + err)
        return 1
    warn = [l for l in (r.stderr + r.stdout).splitlines() if "%Warning" in l and "-Wno" not in l]
    print(f"EDIT OK: {path.relative_to(SB)} updated and design lints clean"
          + (f" ({len(warn)} warnings)" if warn else ""))
    return 0

def main() -> int:
    a = sys.argv[1:]
    if not a or a[0] not in ("replace", "lines", "lint"):
        print(__doc__); return 2
    if a[0] == "lint":
        r = lint(); print(r.stderr[-3000:] or "lint clean"); return r.returncode
    if a[0] == "replace":
        if len(a) != 4: print(__doc__); return 2
        path = _target(a[1]); text = path.read_text(); n = text.count(a[2])
        if n != 1:
            print(f"EDIT REJECTED: OLD text occurs {n} times in {path.name}; it must occur exactly once. "
                  "Include more surrounding context.")
            return 1
        return _apply(path, text.replace(a[2], a[3], 1))
    if a[0] == "lines":
        if len(a) != 4: print(__doc__); return 2
        path = _target(a[1]); s, e = int(a[2]), int(a[3])
        lines = path.read_text().splitlines(keepends=True)
        if not (1 <= s <= e <= len(lines)):
            print(f"EDIT REJECTED: line range {s}..{e} outside 1..{len(lines)}"); return 1
        new = sys.stdin.read()
        if new and not new.endswith("\n"): new += "\n"
        return _apply(path, "".join(lines[:s-1]) + new + "".join(lines[e:]))
    return 2

if __name__ == "__main__":
    sys.exit(main())
