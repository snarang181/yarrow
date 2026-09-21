"""YARROW configuration.

Following the CHIA example convention, all tuning lives here as module-level
constants; secrets (GOOGLE_CLOUD_PROJECT etc.) come from the environment.
"""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RTL_GOLDEN = pathlib.Path(os.environ.get("YARROW_GOLDEN",
                                         ROOT / "rtl" / "cache_ctrl.sv"))
TB_DIR = ROOT / "tb"
BENCH_DIR = ROOT / "bench"
MUTANTS_DIR = BENCH_DIR / "mutants"
SUITE_JSON = BENCH_DIR / "suite.json"
SCRIPTS_DIR = ROOT / "scripts"
PROMPTS_DIR = pathlib.Path(__file__).resolve().parent / "prompts"
RUNS_DIR = ROOT / "runs"

# --- LLM backends -----------------------------------------------------------
# "claude"  -> ClaudeCodeLLM (claude CLI on PATH, already authenticated)
# "gemini"  -> VertexGeminiLLM (needs gcloud ADC + GOOGLE_CLOUD_PROJECT)
# "opencode"-> OpenCodeLLM with google-vertex provider (needs ADC + project)
BACKEND_DEFAULT_MODEL = {
    "claude": None,  # CLI default model
    "gemini": "gemini-3.1-pro-preview",
    "opencode": "google-vertex/gemini-3.1-pro-preview",
}
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "global")


def gcp_project() -> str:
    """GOOGLE_CLOUD_PROJECT env, falling back to the active gcloud config."""
    p = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if p:
        return p
    import shutil
    import subprocess
    if shutil.which("gcloud"):
        try:
            r = subprocess.run(
                ["gcloud", "config", "get-value", "project"],
                capture_output=True, text=True, timeout=15)
            p = r.stdout.strip()
            if p and p != "(unset)":
                return p
        except Exception:
            pass
    return ""


GCP_PROJECT = gcp_project()

# USD per million (input, output) tokens, used only when the backend does
# not report cost_usd itself. gemini-3.1-pro-preview priced at published
# gemini-2.5-pro rates as an estimate until official pricing is confirmed.
PRICE_PER_MTOK = {
    "gemini-3.1-pro-preview": (1.25, 10.0),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "aws/anthropic/bedrock-claude-opus-5": (0.0, 0.0),
    "aws/anthropic/bedrock-claude-sonnet-5": (0.0, 0.0),
    "azure/openai/gpt-5.4-mini": (0.0, 0.0),
    "azure/anthropic/claude-haiku-4-5": (0.0, 0.0),
    "_default": (1.25, 10.0),
}

# --- Loop defaults ----------------------------------------------------------
LLM_TIMEOUTS = {"flat": 1200, "feedback": 900, "cause_retry": 900}
BASH_TOOL_TIMEOUT_S = 240

# Ray resource pools (single-machine defaults; see driver.py)
RAY_RESOURCES = {
    "orch": 64,          # loop orchestration functions (num_cpus=0.1 each)
    "sim": 4,            # concurrent verilator build+run slots
    # concurrent LLM turns (YARROW_LLM_SLOTS overrides)
    "llm": int(os.environ.get("YARROW_LLM_SLOTS", "2")),
    # credential gates used by chia.models backends
    "claude_creds": 1,
    "vertex_creds": 1,
    "opencode_creds": 1,
}
