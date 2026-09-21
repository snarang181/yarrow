"""LLM agent layer: backend construction, one-turn dispatch, footer parsing.

Prompts are rendered with string.Template.safe_substitute (never str.format —
RTL/shell braces would break it). Decision footers are line-oriented and
regex-parsed, taking the LAST match, per the CHIA circt_issue_solver pattern.
"""
import os
import pathlib
import re
from string import Template

from chia.base.ChiaFunction import ChiaFunction, get

from yarrow import config

_PROMPT_CACHE = {}


def load_prompt(name: str) -> str:
    if name not in _PROMPT_CACHE:
        _PROMPT_CACHE[name] = (config.PROMPTS_DIR / f"{name}.md").read_text()
    return _PROMPT_CACHE[name]


def render(name: str, **kw) -> str:
    return Template(load_prompt(name)).safe_substitute(**kw)


def make_llm(backend: str, model=None, system_message=None, phase="analyze"):
    """Construct a backend LLM for one phase (per-phase timeout, like the
    CHIA circt example). LLM objects are cheap; make one per turn."""
    model = model or config.BACKEND_DEFAULT_MODEL[backend]
    timeout_s = config.LLM_TIMEOUTS.get(phase, 900)
    if backend == "claude":
        from chia.models.claude import ClaudeCodeLLM
        return ClaudeCodeLLM(model=model, system_message=system_message,
                             dangerously_skip_permissions=True, retries=3,
                             timeout_seconds=timeout_s)
    if backend == "gemini":
        from chia.models.vertex import VertexGeminiLLM
        # 32k output cap: gemini-2.5-pro's thinking tokens count against
        # max_output_tokens, and the 16k chia default truncated real turns
        # (H04 MaxOutputTokensError, 2026-08-31).
        return VertexGeminiLLM(model=model, system_message=system_message,
                               project=config.GCP_PROJECT or None,
                               location=config.VERTEX_LOCATION,
                               timeout_seconds=timeout_s,
                               max_tokens=32000)
    if backend == "opencode":
        from chia.models.opencode import AdditionalModelProvider, OpenCodeLLM
        vertex = AdditionalModelProvider(
            id="google-vertex", npm="@ai-sdk/google-vertex",
            name="Google Vertex AI", models=[model.split("/", 1)[1]],
            options={"project": config.GCP_PROJECT,
                     "location": config.VERTEX_LOCATION})
        return OpenCodeLLM(model=model, system_message=system_message,
                           additional_providers=[vertex],
                           timeout_seconds=timeout_s)
    raise ValueError(f"unknown backend {backend}")


@ChiaFunction(resources={"llm": 1.0}, num_cpus=0.5, max_retries=0)
def _prompt_with_usage(llm, prompt_text: str, tools) -> dict:
    """Run llm.prompt in-process on the llm pool and return result AND the
    backend's usage metadata. Backends stash token/cost accounting on
    `self._last_metadata`, which is lost across a chia_remote boundary
    (worker-side self mutations are discarded) — running the call locally
    inside this wrapper keeps it."""
    # llm.prompt is a bound method for local calls (explicit self is only
    # needed for the .chia_remote dispatch form)
    import random
    import time

    from chia.models.openai_compat import RateLimitError as OAIRateLimit
    from chia.models.vertex import RateLimitError as VertexRateLimit

    # 429s (per-minute quota, hit hardest by concurrent runs of one model
    # turns) must not error out a whole mutant run. A rate-limited attempt
    # produced no tokens, so retrying here keeps this a single llm_call in
    # the budget accounting; wall budget still ticks.
    delay = 10.0
    for attempt in range(8):
        try:
            cli = llm.prompt(prompt_text, tools=list(tools or []))
            break
        except (OAIRateLimit, VertexRateLimit):
            if attempt == 7:
                raise
            time.sleep(delay + random.uniform(0, delay / 2))
            delay = min(delay * 2, 120.0)
    usage = getattr(cli, "usage", None)
    meta = getattr(llm, "_last_metadata", None) or {}
    return {"result": cli.result or "", "success": bool(cli.success),
            "usage": dict(usage) if isinstance(usage, dict) else {},
            "meta": {k: v for k, v in meta.items()
                     if isinstance(v, (int, float, str))}}


def estimate_cost_usd(model: str, usage: dict, meta: dict) -> float:
    """Prefer backend-reported cost; else price tokens via config table."""
    for src in (usage, meta):
        c = src.get("cost_usd")
        if c:
            return float(c)
    inp = usage.get("input_tokens") or meta.get("input_tokens") or 0
    out = usage.get("output_tokens") or meta.get("output_tokens") or 0
    price = config.PRICE_PER_MTOK.get(model or meta.get("model") or "", None)
    if price is None:
        price = config.PRICE_PER_MTOK.get("_default")
    return round(inp / 1e6 * price[0] + out / 1e6 * price[1], 6)


def agent_turn(backend: str, model, system_message: str, prompt_text: str,
               tools, phase: str) -> dict:
    """One LLM turn on the llm resource pool. Returns a plain dict:
    {result, success, usage, cost_usd}."""
    llm = make_llm(backend, model, system_message, phase)
    t = get(_prompt_with_usage.chia_remote(llm, prompt_text,
                                           list(tools or [])))
    model_name = model or config.BACKEND_DEFAULT_MODEL[backend] or ""
    t["cost_usd"] = estimate_cost_usd(model_name, t["usage"], t["meta"])
    t["usage"] = t["usage"] or t["meta"]
    return t


# --- footer parsing ----------------------------------------------------------

_STATUS_RE = re.compile(r"(?im)^\s*STATUS:\s*(PATCH_READY|STUCK)\b")


def parse_status(text: str) -> str:
    hits = _STATUS_RE.findall(text or "")
    return hits[-1].upper() if hits else "PATCH_READY"
