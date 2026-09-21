"""Causal-claim truth semantics — single source shared by the offline
scorer (scripts/score_causal_claims.py) and the in-loop check
(baselines.flat_solve --cause-loop). Frozen with the D1b set-based truth
definition; see docs/goal_causal_fidelity.md."""
import re


def base_name(sig: str) -> str:
    """Strip hierarchy, indices, and markup: 'top.u_bank0.dirty_arr[3][1]'
    -> 'dirty_arr'."""
    s = (sig or "").strip().strip("`").split()[0] if sig else ""
    s = s.split(".")[-1]
    return re.sub(r"\[.*", "", s).strip()


def truth_set(label: dict) -> set:
    """Acceptable 'exact' base names: every signal divergent at the first
    divergent cycle (combinational sources included), the state-first
    label, and each composed part's own first divergence."""
    names = {base_name(label["first_divergence"]["signal"])}
    for e in label.get("first_divergence_set", []):
        names.add(base_name(e["signal"] if isinstance(e, dict) else e))
    for p in label.get("parts", []):
        fd = p.get("first_divergence") or {}
        if fd.get("signal"):
            names.add(base_name(fd["signal"]))
        for e in p.get("first_divergence_set", []):
            names.add(base_name(e["signal"] if isinstance(e, dict) else e))
    return {n for n in names if n}


def claim_exact(claim: dict, label: dict) -> bool:
    got = base_name((claim or {}).get("signal", ""))
    return bool(got) and got in truth_set(label)
