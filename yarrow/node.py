"""Per-attempt state for the YARROW loop: the agent sandbox, its bash tool,
budget accounting, transcripts, and the independent verifier call.

Isolation: the agent only touches its sandbox copy of the RTL; verification
always rebuilds the candidate from text with `ops.build_and_test` on the sim
pool. Budgets (LLM calls, sim runs, wall clock) live in one BudgetActor per
attempt.
"""
import json
import pathlib
import re
import time

from chia.base.ChiaFunction import get

from yarrow import agent, config, ops

_EXPL_RE = re.compile(r"(?ims)^\s*EXPLANATION:\s*(.+?)(?=^\s*STATUS:|\Z)")


def _extract(rx, text, default=""):
    m = rx.search(text or "")
    return (m.group(1).strip() if m else default)[:2000]


def _result(task, status, *, patch="", explanation="", usage=None, t0=None):
    return {
        "task_id": task["id"], "status": status,
        "patch": patch, "explanation": explanation,
        "usage": usage or {},
        "wall_s": round(time.time() - t0, 1) if t0 else 0.0,
    }


def total_usage(result: dict) -> dict:
    """Usage of one attempt result."""
    u = result.get("usage") or {}
    return {"llm_calls": u.get("llm_calls", 0), "sims": u.get("sims", 0),
            "cost_usd": round(u.get("cost_usd", 0.0), 4)}


class _Node:
    """Per-node helpers: sandbox, tool, accounting, transcripts."""

    def __init__(self, task):
        self.task = task
        self.cfg = task["cfg"]
        self.budget = task["budget"]
        self.dir = pathlib.Path(task["run_dir"]) / f"task_{task['id']}"
        # A design descriptor makes the sandbox, verifier and prompts
        # multi-file/table-driven; absent, this is the single-file
        # cache_ctrl path.
        self.design_dir = task.get("design_dir")
        from yarrow.design import Design
        self.design = Design.load(self.design_dir) if self.design_dir else None
        self.hygiene = bool(task["cfg"].get("hygiene")) and bool(self.design)
        self.sandbox = ops.make_sandbox(
            self.dir / "sandbox", task["mutant_rtl"], task["failing"],
            design_dir=self.design_dir, hygiene=self.hygiene)
        self.usage = {"llm_calls": 0, "sims": 0, "cost_usd": 0.0}
        self._sim_seen = 0
        self._phase_n = {}
        from chia.base.tools.BashTool import BashTool
        safe = re.sub(r"[^A-Za-z0-9_]", "_", task["id"])
        name = f"bash_{safe}"
        if len(name) > 64:
            # Some providers reject tool names > 64 chars; keep a hash
            # suffix so attempts with a shared prefix stay distinct.
            import hashlib
            name = name[:56] + "_" + hashlib.sha1(
                name.encode()).hexdigest()[:7]
        self.bash = BashTool(name, str(self.sandbox),
                             timeout_seconds=config.BASH_TOOL_TIMEOUT_S)

    def stop(self):
        try:
            self.bash.stop()
        except Exception:
            pass

    def spend_llm(self) -> bool:
        return get(self.budget.try_spend.remote("llm_calls", 1))

    def spend_sims(self, n) -> bool:
        return get(self.budget.try_spend.remote("sims", n))

    def system_prompt(self) -> str:
        if not self.design:
            return agent.load_prompt("system")
        bullets = "\n".join(f"- `{p}`" for p in self.design.rtl_paths)
        return agent.render("system_hyg_design" if self.hygiene else "system_design",
                            design_blurb=self.design.blurb(), rtl_files_bullets=bullets)

    def prompt_vars(self) -> dict:
        """Extra Template variables for the *_design prompt variants; harmless
        for legacy prompts (safe_substitute ignores unused keys)."""
        if not self.design:
            return {}
        return {"rtl_files": ", ".join(f"`{p}`" for p in self.design.rtl_paths),
                "full_suite": " ".join(self.design.suite_configs()),
                "top": self.design.top}

    def prompt_name(self, base: str) -> str:
        if self.design and self.hygiene:
            return f"{base}_hyg_design"   # edit/reset/firstdiff tools
        return f"{base}_design" if self.design else base

    def turn(self, phase: str, prompt_text: str) -> dict:
        t = agent.agent_turn(self.cfg["backend"], self.cfg.get("model"),
                             self.system_prompt(), prompt_text,
                             [self.bash], phase)
        self.usage["llm_calls"] += 1
        self.usage["cost_usd"] += t["cost_usd"]
        self.budget.charge.remote("cost_usd", t["cost_usd"])
        # post-hoc: charge agent sim usage from the sandbox counter
        now = ops.read_sim_count(self.sandbox)
        delta = max(0, now - self._sim_seen)
        self._sim_seen = now
        if delta:
            self.usage["sims"] += delta
            self.budget.charge.remote("sims", delta)
        n = self._phase_n.get(phase, 0)
        self._phase_n[phase] = n + 1
        suffix = f"_{n}" if n else ""
        (self.dir / f"{phase}{suffix}.md").write_text(
            f"# prompt\n\n{prompt_text}\n\n# reply\n\n{t['result']}\n")
        return t

    def sandbox_rtl(self):
        # An agent can delete or rename its own source files (observed: flash
        # on axis-async-fifo-c4, 2026-09-15). A missing file is an empty
        # source: the independent build fails and the cell proceeds through
        # the ordinary red-feedback path instead of crashing to an `error` row.
        def read(p: pathlib.Path) -> str:
            return p.read_text() if p.exists() else ""
        if self.design:
            return {s: read(self.sandbox / "rtl" / s) for s in self.design.sources}
        return read(self.sandbox / "rtl" / "cache_ctrl.sv")

    def verify(self) -> dict:
        """Independent full-suite verification of the sandbox RTL."""
        n_cfg = len(self.design.suite_configs() if self.design
                    else ops.suite_configs())
        if not self.spend_sims(n_cfg):
            return {"budget_refused": True, "all_pass": False}
        self.usage["sims"] += n_cfg
        v = get(ops.build_and_test.chia_remote(self.sandbox_rtl(), None,
                                               self.design_dir))
        v["budget_refused"] = False
        return v


def _verify_report(v: dict) -> str:
    if v.get("budget_refused"):
        return "verification refused: budget exhausted"
    if not v.get("built"):
        return f"BUILD FAILED:\n{v.get('build_log', '')[-1200:]}"
    lines = []
    for name, r in v.get("results", {}).items():
        lines.append(f"{'PASS' if r['passed'] else 'FAIL'} {name} "
                     f"rc={r['returncode']}")
        if not r["passed"]:
            lines.append(r["output_tail"][-300:])
    return "\n".join(lines)


def _verify_report(v: dict) -> str:
    if v.get("budget_refused"):
        return "verification refused: budget exhausted"
    if not v.get("built"):
        return f"BUILD FAILED:\n{v.get('build_log', '')[-1200:]}"
    lines = []
    for name, r in v.get("results", {}).items():
        lines.append(f"{'PASS' if r['passed'] else 'FAIL'} {name} "
                     f"rc={r['returncode']}")
        if not r["passed"]:
            lines.append(r["output_tail"][-300:])
    return "\n".join(lines)
