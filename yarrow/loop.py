"""The YARROW repair-and-diagnosis loop for one attempt.

flat_solve: one agent, one sandbox, repeated propose -> independent verify ->
feedback rounds until the patch passes or the attempt budget is exhausted;
then, if enabled, the diagnosis check against the reference label with
bounded "re-examine" feedback (cause_loop A/B) or, for the ablations, the
diagnosis verdict inside the failing-patch feedback (cause_loop C/D).
"""
import re
import time

from chia.base.ChiaFunction import ChiaFunction, get

from yarrow import agent, config
from yarrow.node import _Node, _extract, _result, _EXPL_RE, _verify_report

# CAUSE_* footer fields; line-oriented,
# LAST match wins, same as every other footer in this codebase.
_CAUSE_RES = {
    "signal": re.compile(r"(?im)^\s*CAUSE_SIGNAL:\s*(.+)$"),
    "module": re.compile(r"(?im)^\s*CAUSE_MODULE:\s*(.+)$"),
    "event": re.compile(r"(?im)^\s*CAUSE_EVENT:\s*(.+)$"),
    "mechanism": re.compile(r"(?im)^\s*CAUSE_MECHANISM:\s*(.+)$"),
}


def _diag_block(cause: dict, label: dict, arm: str) -> str:
    """Ablation: diagnosis feedback in the failing-patch (red) path.
    Arm C: binary verdict on the agent's stated cause. Arm D: verdict plus
    the first-divergence set itself (oracle localization; the ceiling)."""
    from yarrow.causal import claim_exact
    sig = (cause or {}).get("signal", "").strip()
    lines = ["## Diagnosis check (golden-reference replay)", ""]
    if not sig or sig.upper().startswith("N/A"):
        lines.append("You did not state a usable CAUSE_SIGNAL. Localize the FIRST "
                     "corrupted signal before editing further.")
    elif claim_exact(cause, label):
        lines.append(f"Your stated root cause `{sig}` was checked mechanically against a "
                     "golden-reference replay of the original failure: it IS the first "
                     "signal to diverge from correct behavior. Your localization is "
                     "correct; the remaining problem is the fix itself. Do not "
                     "re-investigate the cause — make the change to that signal's logic "
                     "correct and complete.")
    else:
        lines.append(f"Your stated root cause `{sig}` was checked mechanically against a "
                     "golden-reference replay of the original failure: it is NOT the "
                     "first signal to diverge from correct behavior. Re-examine the "
                     "failure before editing further: find the earliest corrupted state, "
                     "then fix the logic that produces it.")
    if arm == "D":
        fd = label["first_divergence"]
        names = []
        for e in label.get("first_divergence_set", []) or [fd]:
            s = (e["signal"] if isinstance(e, dict) else e)
            s = s[4:] if s.startswith("TOP.") else s
            m = e.get("module") if isinstance(e, dict) else None
            names.append(f"`{s}`" + (f" (module {m})" if m else ""))
        lines += ["", f"The first divergence from the golden reference occurs at cycle "
                  f"{fd['cycle']} on: " + ", ".join(names) + "."]
    return "\n".join(lines)


def _extract_cause(text: str) -> dict:
    out = {}
    for k, rx in _CAUSE_RES.items():
        m = rx.findall(text or "")
        if m:
            out[k] = m[-1].strip().strip("`")
    return out


@ChiaFunction(resources={"orch": 1}, num_cpus=0.1, max_retries=0)
def flat_solve(task: dict) -> dict:
    """Flat agentic repair loop: single agent, iterated verify/feedback."""
    t0 = time.time()
    node = _Node(task)
    # --cause-footer swaps in the CAUSE_*-footer prompt variant; the
    # default flat prompt stays byte-identical for comparability.
    flat_prompt = ("flat_cause" if task["cfg"].get("cause_footer")
                   else "flat")
    # Tier R (docs D6) selects the *_design prompt variants; pv is empty for
    # the cache_ctrl tiers so their renders stay byte-identical.
    pv = node.prompt_vars()
    cause = {}
    try:
        if not node.spend_llm():
            return _result(task, "exhausted", usage=node.usage, t0=t0)
        # One flat turn (investigate+fix), then the shared
        # verify->feedback->verify loop, with feedback rounds bounded by the
        # attempt budget: keep looping while budget lasts.
        turn = node.turn("flat", agent.render(node.prompt_name(flat_prompt),
                                              evidence=task["evidence"], **pv))
        explanation = _extract(_EXPL_RE, turn["result"])
        cause = _extract_cause(turn["result"]) or cause
        while True:
            v = node.verify()
            if v.get("budget_refused"):
                r = _result(task, "exhausted", explanation=explanation,
                            usage=node.usage, t0=t0)
                r["cause"] = cause
                return r
            if v["all_pass"]:
                from yarrow import ops
                patch = ops.unified_diff(task["mutant_rtl"], node.sandbox_rtl())
                r = _result(task, "fixed", patch=patch,
                            explanation=explanation, usage=node.usage,
                            t0=t0)
                # Diagnosis check: patch is accepted; the causal
                # claim must additionally verify against the mechanical
                # first-divergence label. Bounded retries with tiered
                # minimal feedback (A: wrong; B: + true first-divergence
                # cycle, never the signal name).
                label = task.get("cause_label")
                tier = task["cfg"].get("cause_loop")
                rounds = 0
                if label and tier in ("C", "D"):
                    # C/D arms act only in the red path; record the claim's
                    # grade with no green-path retry so repair is isolated.
                    from yarrow.causal import claim_exact
                    r["cause_verified"] = claim_exact(cause, label)
                    r["cause_rounds"] = 0
                    r["usage"] = node.usage
                elif label and tier:
                    from yarrow.causal import claim_exact
                    while (rounds < 2 and not claim_exact(cause, label)
                           and node.spend_llm()):
                        hint = ""
                        if tier == "B":
                            hint = ("The true first divergence occurs at "
                                    f"cycle {label['first_divergence']['cycle']}.")
                        rt = node.turn("cause_retry", agent.render(
                            node.prompt_name("cause_retry"), cycle_hint=hint, **pv))
                        cause = _extract_cause(rt["result"]) or cause
                        rounds += 1
                    r["cause_verified"] = claim_exact(cause, label)
                    r["cause_rounds"] = rounds
                    r["usage"] = node.usage
                r["cause"] = cause
                return r
            if not node.spend_llm():
                r = _result(task, "failed", explanation=explanation,
                            usage=node.usage, t0=t0)
                r["cause"] = cause
                return r
            fb_prompt = ("feedback_cause" if task["cfg"].get("cause_footer")
                         else "feedback")
            fb_vars = {}
            if task["cfg"].get("cause_loop") in ("C", "D") and task.get("cause_label"):
                # Ablation: diagnosis feedback where it can affect repair.
                fb_prompt = "feedback_diag"
                fb_vars["diag_block"] = _diag_block(
                    cause, task["cause_label"], task["cfg"]["cause_loop"])
            fb = node.turn("feedback", agent.render(
                node.prompt_name(fb_prompt), verify_report=_verify_report(v),
                **fb_vars, **pv))
            explanation = _extract(_EXPL_RE, fb["result"], explanation)
            cause = _extract_cause(fb["result"]) or cause
    finally:
        node.stop()
