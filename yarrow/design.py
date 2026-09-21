"""Design descriptor for non-cache_ctrl benchmarks (tier R, docs D6).

The S/hard/extreme/cluster tiers are single-file `cache_ctrl.sv` mutants with
the plusarg-driven tb_main suite; that path is unchanged and is selected
whenever no `design.json` is present.  A `design.json` (written by
scripts/import_rtlrepair.py) describes a multi-file design whose regression
suite is a set of CSV table testbenches:

    {"id": ..., "sources": [...], "top": ..., "bug_file": ...,
     "tables": [{"name": ..., "file": ..., "rows": N}], "rtl_lines": N,
     "provenance": {...}}

Everything the loop needs — golden/buggy texts, the suite as
{config: plusargs}, the generated table TB, prompt text — comes from here so
the driver, sandbox and verifier stay design-agnostic.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib


@dataclasses.dataclass(frozen=True)
class Design:
    root: pathlib.Path          # bench/real/<id>
    id: str
    sources: tuple              # file names, in build order
    top: str
    bug_file: str
    tables: tuple               # ({"name","file","rows"}, ...)
    rtl_lines: int

    @staticmethod
    def load(dir_: pathlib.Path) -> "Design | None":
        p = pathlib.Path(dir_) / "design.json"
        if not p.exists():
            return None
        d = json.loads(p.read_text())
        return Design(root=pathlib.Path(dir_), id=d["id"], sources=tuple(d["sources"]),
                      top=d["top"], bug_file=d["bug_file"],
                      tables=tuple(d["tables"]), rtl_lines=int(d.get("rtl_lines", 0)))

    # --- files -----------------------------------------------------------
    def golden_texts(self) -> dict:
        return {s: (self.root / "rtl" / s).read_text() for s in self.sources}

    def buggy_texts(self) -> dict:
        return {s: (self.root / "rtl_buggy" / s).read_text() for s in self.sources}

    @property
    def tb_cpp(self) -> pathlib.Path:
        return self.root / "tb_table.cpp"

    def table_path(self, name: str) -> pathlib.Path:
        for t in self.tables:
            if t["name"] == name:
                return self.root / "tb" / t["file"]
        raise KeyError(name)

    # --- suite -----------------------------------------------------------
    def suite_configs(self) -> dict:
        """{config name: plusargs} — one config per table.  Paths are
        sandbox-relative (`tb/<file>`); callers run the sim with cwd set to a
        directory that holds a `tb/` copy (sandbox or verifier tmp dir)."""
        return {t["name"]: {"table": f"tb/{t['file']}"} for t in self.tables}

    def suite_json(self) -> str:
        return json.dumps({"configs": [{"name": n, "args": a}
                                       for n, a in self.suite_configs().items()]},
                          indent=2) + "\n"

    # --- prompt text -------------------------------------------------------
    @property
    def rtl_paths(self) -> list:
        return [f"rtl/{s}" for s in self.sources]

    def blurb(self) -> str:
        """Neutral description for the agent: what it is, not what is wrong."""
        files = ", ".join(f"`{p}`" for p in self.rtl_paths)
        return (f"a production open-source Verilog design, top module `{self.top}` "
                f"({self.rtl_lines} lines across {len(self.sources)} file(s): {files}). "
                f"Its regression suite is a set of cycle-accurate table testbenches "
                f"recorded from a correct reference: each CSV row is the value of "
                f"every port at one rising clock edge (inputs driven, outputs checked "
                f"before the edge).")
