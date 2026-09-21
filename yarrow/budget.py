"""Budget accounting for one attempt on one bug.

A plain Ray actor; its handle is passed inside the task dict so every stage
of the attempt charges the same ledger. Budgets are hard: try_spend refuses
once the ceiling is hit, and callers must treat refusal as exhaustion.
"""
import time

import ray


@ray.remote(num_cpus=0)
class BudgetActor:
    def __init__(self, llm_calls: int, sims: int, wall_s: float):
        self._limits = {"llm_calls": llm_calls, "sims": sims}
        self._spent = {"llm_calls": 0, "sims": 0, "cost_usd": 0.0}
        self._t0 = time.time()
        self._wall_s = wall_s

    def try_spend(self, kind: str, n: int = 1) -> bool:
        """Reserve n units of kind; False if over budget (nothing charged)."""
        if time.time() - self._t0 > self._wall_s:
            return False
        if self._spent[kind] + n > self._limits[kind]:
            return False
        self._spent[kind] += n
        return True

    def charge(self, kind: str, n) -> None:
        """Unconditional post-hoc accounting (e.g. cost_usd, agent sim use)."""
        self._spent[kind] = self._spent.get(kind, 0) + n

    def exhausted(self) -> bool:
        return (time.time() - self._t0 > self._wall_s
                or self._spent["llm_calls"] >= self._limits["llm_calls"]
                or self._spent["sims"] >= self._limits["sims"])

    def snapshot(self) -> dict:
        return {"limits": dict(self._limits), "spent": dict(self._spent),
                "elapsed_s": round(time.time() - self._t0, 1),
                "wall_limit_s": self._wall_s}
