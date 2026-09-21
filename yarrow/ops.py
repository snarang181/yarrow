"""Deterministic (non-LLM) operations: workspaces, builds, verification.

The verification path never involves the agent: `build_and_test` takes RTL
*content*, builds it in a private temp dir, and runs named suite configs.
Agents work in a sandbox that contains their own copy of the RTL plus
`test.sh`/`sim.sh`/`probe.sh` wrappers which bump a sim-usage counter file.
"""
import json
import pathlib
import shutil
import subprocess
import tempfile

from chia.base.ChiaFunction import ChiaFunction

from yarrow import config
from yarrow.design import Design

WALL_TIMEOUT_S = 120


def _run_config_binary(sim: str, args: dict, cwd=None) -> dict:
    argv = [sim] + [f"+{k}={v}" for k, v in args.items()]
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=WALL_TIMEOUT_S, cwd=cwd)
        rc, out = p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        rc, out = 3, "FAIL: wall-clock timeout"
    return {"returncode": rc, "passed": rc == 0, "output_tail": out[-600:]}


def suite_configs() -> dict:
    return {c["name"]: c["args"]
            for c in json.loads(config.SUITE_JSON.read_text())["configs"]}


def _stage_design(tmp: pathlib.Path, design: Design, rtl_texts: dict) -> None:
    """Materialize a tier-R design for building: rtl/<sources>, tb/<tables>,
    tb_table.cpp.  Used by both the verifier tmp dir and the agent sandbox."""
    (tmp / "rtl").mkdir(exist_ok=True)
    (tmp / "tb").mkdir(exist_ok=True)
    for name in design.sources:
        (tmp / "rtl" / name).write_text(rtl_texts[name])
    for t in design.tables:
        shutil.copy(design.root / "tb" / t["file"], tmp / "tb" / t["file"])
    shutil.copy(design.tb_cpp, tmp / "tb_table.cpp")


def _build_design(tmp: pathlib.Path, design: Design, out="obj") -> subprocess.CompletedProcess:
    srcs = [str(tmp / "rtl" / s) for s in design.sources]
    return subprocess.run(
        [str(config.SCRIPTS_DIR / "build_design.sh"), str(tmp / out),
         str(tmp / "tb_table.cpp"), design.top] + srcs,
        capture_output=True, text=True)


@ChiaFunction(resources={"sim": 1}, num_cpus=2, max_retries=0)
def build_and_test(rtl_text, config_names=None, design_dir=None) -> dict:
    """Build the given RTL content and run the named suite configs
    (default: all). Returns {built, build_log, results: {name: {...}},
    all_pass, failing}.

    Legacy tiers: `rtl_text` is the cache_ctrl.sv text.  Tier R
    (`design_dir` given, docs D6): `rtl_text` is {source file: text} and the
    suite is the design's table testbenches."""
    design = Design.load(design_dir) if design_dir else None
    cfgs = design.suite_configs() if design else suite_configs()
    names = list(config_names) if config_names else list(cfgs)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="yarrow_bt_"))
    try:
        if design:
            _stage_design(tmp, design, rtl_text)
            b = _build_design(tmp, design)
        else:
            rtl = tmp / "cache_ctrl.sv"
            rtl.write_text(rtl_text)
            b = subprocess.run(
                [str(config.SCRIPTS_DIR / "build_sim.sh"), str(rtl), str(tmp / "obj")],
                capture_output=True, text=True)
        if b.returncode != 0:
            return {"built": False, "build_log": (b.stdout + b.stderr)[-1500:],
                    "results": {}, "all_pass": False, "failing": []}
        sim = str(tmp / "obj" / "sim")
        results = {n: _run_config_binary(sim, cfgs[n], cwd=str(tmp) if design else None)
                   for n in names}
        failing = [n for n, r in results.items() if not r["passed"]]
        return {"built": True, "build_log": "", "results": results,
                "all_pass": not failing, "failing": failing}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_TEST_SH = """#!/usr/bin/env bash
# Build this sandbox's rtl/cache_ctrl.sv and run named suite configs.
# Usage: ./test.sh [config ...]   (default: {default_configs})
set -o pipefail
cd "$(dirname "$0")"
CONFIGS="$*"
[ -z "$CONFIGS" ] && CONFIGS="{default_configs}"
bash {root}/scripts/build_sim.sh rtl/cache_ctrl.sv obj || {{ echo BUILD_FAILED; exit 3; }}
N=0; for c in $CONFIGS; do N=$((N+1)); done
echo $(( $(cat .sim_count 2>/dev/null || echo 0) + N )) > .sim_count
{python} {root}/scripts/run_suite.py obj/sim --configs "$(echo $CONFIGS | tr ' ' ',')"
"""

_SIM_SH = """#!/usr/bin/env bash
# Build this sandbox's rtl/cache_ctrl.sv and run the sim with raw plusargs.
# Usage: ./sim.sh +seed=42 +ops=200 +verbose ...
set -o pipefail
cd "$(dirname "$0")"
bash {root}/scripts/build_sim.sh rtl/cache_ctrl.sv obj || {{ echo BUILD_FAILED; exit 3; }}
echo $(( $(cat .sim_count 2>/dev/null || echo 0) + 1 )) > .sim_count
# Cap what the agent sees: an unbounded (e.g. +verbose) run must not flood
# the model's context — the tail keeps the PASS/FAIL verdict either way.
./obj/sim "$@" > .sim_out 2>&1
rc=$?
if [ "$(wc -c < .sim_out)" -gt 16000 ]; then
  echo "[sim output truncated to the last 16000 bytes]"
fi
tail -c 16000 .sim_out
exit $rc
"""

_PROBE_SH = """#!/usr/bin/env bash
# Trace one failing-config run of this sandbox's RTL and query its VCD.
# Usage: ./probe.sh <signal-substring> [start_cycle] [end_cycle]
#                   [+seed=... +ops=... ...]
set -o pipefail
cd "$(dirname "$0")"
[ $# -ge 1 ] || {{ echo "usage: ./probe.sh <signal-substring> [start_cycle] [end_cycle] [+seed=... +ops=... ...]" >&2; exit 2; }}
NEEDLE="$1"; shift
START=0
END=""
if [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]+$ ]]; then START="$1"; shift; fi
if [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]+$ ]]; then END="$1"; shift; fi
echo $(( $(cat .sim_count 2>/dev/null || echo 0) + 1 )) > .sim_count
bash {root}/scripts/build_sim.sh rtl/cache_ctrl.sv obj_probe tb/tb_replay.cpp || {{ echo BUILD_FAILED; exit 3; }}
rm -f .probe.vcd .probe.inputs .probe_out
./obj_probe/sim +record=.probe.inputs +trace=.probe.vcd {probe_args} "$@" > .probe_out 2>&1
rc=$?
if [ ! -f .probe.vcd ]; then
  tail -c 4000 .probe_out
  exit $rc
fi
if [ -n "$END" ]; then
  {python} {root}/scripts/vcd_query.py .probe.vcd "$NEEDLE" "$START" "$END"
else
  {python} {root}/scripts/vcd_query.py .probe.vcd "$NEEDLE" "$START"
fi
exit $rc
"""


# --- tier R (docs D6): multi-file designs verified by table testbenches ----
# Same contract as the cache_ctrl templates (exit codes, .sim_count, output
# cap); the build is scripts/build_design.sh and configs are tables.

_TEST_SH_DESIGN = """#!/usr/bin/env bash
# Build this sandbox's rtl/ and run named table configs (see suite.json).
# Usage: ./test.sh [config ...]   (default: {default_configs})
set -o pipefail
cd "$(dirname "$0")"
CONFIGS="$*"
[ -z "$CONFIGS" ] && CONFIGS="{default_configs}"
bash {root}/scripts/build_design.sh obj tb_table.cpp {top} {srcs} || {{ echo BUILD_FAILED; exit 3; }}
N=0; for c in $CONFIGS; do N=$((N+1)); done
echo $(( $(cat .sim_count 2>/dev/null || echo 0) + N )) > .sim_count
{python} {root}/scripts/run_suite.py obj/sim --suite suite.json --configs "$(echo $CONFIGS | tr ' ' ',')"
"""

_SIM_SH_DESIGN = """#!/usr/bin/env bash
# Build this sandbox's rtl/ and run ONE table config, printing the verdict.
# Usage: ./sim.sh <config>          (configs are listed in suite.json)
#        ./sim.sh +table=tb/x.csv   (raw plusargs pass-through)
set -o pipefail
cd "$(dirname "$0")"
bash {root}/scripts/build_design.sh obj tb_table.cpp {top} {srcs} || {{ echo BUILD_FAILED; exit 3; }}
echo $(( $(cat .sim_count 2>/dev/null || echo 0) + 1 )) > .sim_count
if [ $# -ge 1 ] && [[ "$1" != +* ]]; then
  {python} {root}/scripts/run_suite.py obj/sim --suite suite.json --config "$1" > .sim_out 2>&1
else
  ./obj/sim "$@" > .sim_out 2>&1
fi
rc=$?
if [ "$(wc -c < .sim_out)" -gt 16000 ]; then
  echo "[sim output truncated to the last 16000 bytes]"
fi
tail -c 16000 .sim_out
exit $rc
"""

_PROBE_SH_DESIGN = """#!/usr/bin/env bash
# Trace the failing table on this sandbox's RTL and query its VCD by row.
# Usage: ./probe.sh <signal-substring> [start_row] [end_row] [+table=tb/other.csv]
set -o pipefail
cd "$(dirname "$0")"
[ $# -ge 1 ] || {{ echo "usage: ./probe.sh <signal-substring> [start_row] [end_row] [+table=tb/x.csv]" >&2; exit 2; }}
NEEDLE="$1"; shift
START=0
END=""
if [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]+$ ]]; then START="$1"; shift; fi
if [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]+$ ]]; then END="$1"; shift; fi
TABLE="+table={probe_table}"
if [ $# -gt 0 ] && [[ "$1" == +table=* ]]; then TABLE="$1"; shift; fi
echo $(( $(cat .sim_count 2>/dev/null || echo 0) + 1 )) > .sim_count
bash {root}/scripts/build_design.sh obj tb_table.cpp {top} {srcs} || {{ echo BUILD_FAILED; exit 3; }}
rm -f .probe.vcd .probe_out
./obj/sim "$TABLE" +trace=.probe.vcd "$@" > .probe_out 2>&1
rc=$?
if [ ! -f .probe.vcd ]; then
  tail -c 4000 .probe_out
  exit $rc
fi
if [ -n "$END" ]; then
  {python} {root}/scripts/vcd_query.py .probe.vcd "$NEEDLE" "$START" "$END"
else
  {python} {root}/scripts/vcd_query.py .probe.vcd "$NEEDLE" "$START"
fi
exit $rc
"""


_EDIT_SH = """#!/usr/bin/env bash
# Lint-gated edit tool. Usage: ./edit replace FILE 'OLD' 'NEW' | ./edit lines FILE START END <<'EOF' ... EOF | ./edit lint
cd "$(dirname "$0")"
exec {python} {root}/scripts/sandbox_edit.py "$@"
"""

_RESET_SH = """#!/usr/bin/env bash
# Restore original (buggy) source files. Usage: ./reset.sh [FILE ...]  (default: all)
cd "$(dirname "$0")"
if [ $# -eq 0 ]; then set -- {sources}; fi
for f in "$@"; do b=$(basename "$f"); cp ".orig/$b" "rtl/$b" && echo "restored rtl/$b"; done
"""


_FIRSTDIFF_SH = """#!/usr/bin/env bash
# Self-replay: simulate the ORIGINAL design (.orig/) and YOUR current rtl/ on the
# same table, trace both, and print the first internal signal that differs.
# Usage: ./firstdiff.sh [config]     (default: {default_config})
set -o pipefail
cd "$(dirname "$0")"
CFG="${{1:-{default_config}}}"
TABLE=$({python} -c "import json,sys; c=json.load(open('suite.json'))['configs']; print([x['args']['table'] for x in c if x['name']==sys.argv[1]][0])" "$CFG") || {{ echo "unknown config $CFG"; exit 2; }}
mkdir -p .orig_rtl && cp .orig/* .orig_rtl/
ORIG_SRCS=$(for s in {sources}; do printf ".orig_rtl/%s " "$s"; done)
bash {root}/scripts/build_design.sh obj_orig tb_table.cpp {top} $ORIG_SRCS > /dev/null || {{ echo "ORIGINAL BUILD FAILED (should not happen)"; exit 3; }}
bash {root}/scripts/build_design.sh obj tb_table.cpp {top} {srcs} || {{ echo BUILD_FAILED; exit 3; }}
echo $(( $(cat .sim_count 2>/dev/null || echo 0) + 2 )) > .sim_count
rm -f .fd_orig.vcd .fd_new.vcd
./obj_orig/sim "+table=$TABLE" +trace=.fd_orig.vcd > /dev/null 2>&1
./obj/sim "+table=$TABLE" +trace=.fd_new.vcd > /dev/null 2>&1
{python} {root}/scripts/first_divergence.py .fd_orig.vcd .fd_new.vcd --tb tb_table.cpp --top {top} --src {srcs}
"""


def _make_design_sandbox(dest: pathlib.Path, design: Design, rtl_texts: dict,
                         default_configs: list, hygiene: bool = False) -> pathlib.Path:
    import sys
    dest.mkdir(parents=True, exist_ok=True)
    _stage_design(dest, design, rtl_texts)
    if hygiene:
        # arm H (docs D8): pristine copies, design descriptor for the edit
        # tool, and the edit/reset wrappers.
        (dest / ".orig").mkdir(exist_ok=True)
        for s in design.sources:
            (dest / ".orig" / s).write_text(rtl_texts[s])
        (dest / ".design.json").write_text(json.dumps(
            {"top": design.top, "sources": list(design.sources)}))
        defaults0 = (list(default_configs) or [design.tables[0]["name"]])[0]
        for name, tpl in (("edit", _EDIT_SH), ("reset.sh", _RESET_SH),
                          ("firstdiff.sh", _FIRSTDIFF_SH)):
            p = dest / name
            p.write_text(tpl.format(root=config.ROOT, python=sys.executable,
                                    sources=" ".join(design.sources), top=design.top,
                                    srcs=" ".join(f"rtl/{s}" for s in design.sources),
                                    default_config=defaults0))
            p.chmod(0o755)
    (dest / "suite.json").write_text(design.suite_json())
    defaults = list(default_configs) or [design.tables[0]["name"]]
    subst = dict(root=config.ROOT, python=sys.executable, top=design.top,
                 srcs=" ".join(f"rtl/{s}" for s in design.sources),
                 default_configs=" ".join(defaults),
                 probe_table=design.suite_configs()[defaults[0]]["table"])
    for name, tpl in (("test.sh", _TEST_SH_DESIGN), ("sim.sh", _SIM_SH_DESIGN),
                      ("probe.sh", _PROBE_SH_DESIGN)):
        p = dest / name
        p.write_text(tpl.format(**subst))
        p.chmod(0o755)
    (dest / ".sim_count").write_text("0\n")
    return dest


def make_sandbox(dest: pathlib.Path, mutant_rtl_text,
                 default_configs: list, design_dir=None,
                 hygiene: bool = False) -> pathlib.Path:
    """Create an agent sandbox: mutant RTL, read-only view of the TB, and
    test/sim/probe wrapper scripts that track simulator usage in .sim_count.
    Tier R (`design_dir`): `mutant_rtl_text` is {source: text}."""
    if design_dir:
        return _make_design_sandbox(dest, Design.load(design_dir),
                                    mutant_rtl_text, default_configs, hygiene)
    import sys
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "rtl").mkdir(exist_ok=True)
    (dest / "rtl" / "cache_ctrl.sv").write_text(mutant_rtl_text)
    shutil.copytree(config.TB_DIR, dest / "tb", dirs_exist_ok=True)
    shutil.copy(config.SUITE_JSON, dest / "suite.json")
    defaults = list(default_configs) or ["smoke"]
    probe_cfg = suite_configs().get(defaults[0], suite_configs()["smoke"])
    subst = dict(root=config.ROOT, python=sys.executable,
                 default_configs=" ".join(defaults),
                 probe_args=" ".join(f"+{k}={v}" for k, v in probe_cfg.items()))
    for name, tpl in (("test.sh", _TEST_SH), ("sim.sh", _SIM_SH),
                      ("probe.sh", _PROBE_SH)):
        p = dest / name
        p.write_text(tpl.format(**subst))
        p.chmod(0o755)
    (dest / ".sim_count").write_text("0\n")
    return dest


def read_sim_count(sandbox: pathlib.Path) -> int:
    try:
        return int((sandbox / ".sim_count").read_text().strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0


def unified_diff(a, b, label: str = "cache_ctrl.sv") -> str:
    """Diff two RTL texts; for tier R both are {source: text} and the result
    is the concatenation of per-file diffs (files in source order)."""
    import difflib
    if isinstance(a, dict):
        return "".join(unified_diff(a[f], b.get(f, ""), label=f) for f in a)
    return "".join(difflib.unified_diff(
        a.splitlines(keepends=True), b.splitlines(keepends=True),
        fromfile=f"a/{label}", tofile=f"b/{label}"))
