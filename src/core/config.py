"""Configuration and model registry.

Loads API keys from a repo-root `.env` (never committed) and exposes a logical model registry. Code refers to
models by stable *aliases* ("opus", "gpt-5.6", ...) so experiment code and stored records never embed raw model
ids or keys. `_REGISTRY` is the single source of truth for alias -> exact API model id.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def repo_root() -> Path:
    """Walk up from this file to the repo root (the dir containing pyproject.toml)."""
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


ROOT = repo_root()

# Load .env once, from the repo root, without clobbering already-set env vars.
load_dotenv(ROOT / ".env", override=False)


# Providers and their connection details. base_url=None means the SDK default.
PROVIDERS = {
    "anthropic": {"base_url": None},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1"},
    # OpenAI direct (GPT-5.x). The GPT-5 reasoning family requires `max_completion_tokens` (not `max_tokens`)
    # and only supports the default temperature — the client/loop key that behaviour off provider=="openai".
    "openai": {"base_url": None},
    # Google Gemini via its OpenAI-compatible endpoint (uses GEMINI_API_KEY directly, not OpenRouter).
    "google": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
}


@dataclass(frozen=True)
class ModelSpec:
    """A logical model the harness can call. `api_key_env` is the NAME of an env var, resolved lazily, so a
    ModelSpec is safe to log — it never holds a secret."""

    alias: str
    provider: str  # "anthropic" | "openrouter" | "openai" | "google"
    role: str  # "judge" | "target"
    model_id: str
    api_key_env: str = "CLAUDE_API_KEY"
    default_extra_body: dict = field(default_factory=dict)  # merged into every call's extra_body (caller wins)
    timeout: float = 120.0  # per-request SDK timeout (s); raised for slow / reasoning endpoints
    omit_temperature: bool = False  # some models reject an explicit temperature param
    thinking_budget: int = 0  # >0 = request Anthropic extended thinking with this budget; captured into res.thinking
    responses_api: bool = False  # GPT-5.x reasoning family: use /v1/responses (chat.completions rejects
    #                              reasoning_effort + function tools together). Keeps reasoning ON.
    reasoning_effort: str = "medium"  # effort for the responses API (reasoning models)
    reasoning_summary: str = "auto"   # native reasoning-summary verbosity: "auto"|"detailed"|"concise". Which mode
    #     actually emits a summary is model-specific: gpt-5.6 -> "detailed"; gpt-5-mini -> ONLY "concise".
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    def resolve_model_id(self) -> str:
        return self.model_id

    def resolve_api_key(self) -> str:
        val = os.environ.get(self.api_key_env)
        if not val and self.api_key_env == "CLAUDE_API_KEY":
            # The Anthropic SDK's own variable name for the same key.
            val = os.environ.get("ANTHROPIC_API_KEY")
        if not val:
            raise RuntimeError(
                f"Model '{self.alias}' needs env var {self.api_key_env}, which is unset. "
                "Put it in a repo-root `.env` (template: .env.example)."
            )
        return val

    @property
    def base_url(self) -> str | None:
        return PROVIDERS[self.provider]["base_url"]


# Google's OpenAI-compatible endpoint returns NO `reasoning` field by default. `thinking_config.include_thoughts`
# makes it return the thought summary inline in content, which `agent/loop.py` moves into res.thinking and strips
# out of the visible text. The budget is deliberately left at the model's default (observability only).
_GEMINI_THOUGHTS = {"extra_body": {"google": {"thinking_config": {"include_thoughts": True}}}}

# 300s rather than the 120s default for the reasoning models: on long agentic sessions with a large output budget
# a frontier reasoning model can exceed 120s per request, and a timeout costs the whole rep.
_SLOW = 300.0

# --- The registry. Aliases are the stable contract used by configs, drivers and stored records. ---
_REGISTRY: dict[str, ModelSpec] = {
    # THE JUDGE. Every judge in the study runs on this alias (configs/panel.yaml `roles:`).
    "opus": ModelSpec(alias="opus", provider="anthropic", role="judge", model_id="claude-opus-4-8",
                      omit_temperature=True, notes="Claude Opus 4.8 — the judge model.", tags=("claude", "judge")),
    # THE 11 EVALUATED MODELS (configs/panel.yaml `panel:`).
    "haiku": ModelSpec(alias="haiku", provider="anthropic", role="target", model_id="claude-haiku-4-5-20251001",
                       notes="Claude Haiku 4.5.", tags=("claude", "target")),
    "sonnet-5": ModelSpec(alias="sonnet-5", provider="anthropic", role="target", model_id="claude-sonnet-5",
                          omit_temperature=True, thinking_budget=4096, timeout=_SLOW,
                          notes="Claude Sonnet 5. Extended thinking captured.", tags=("claude", "target")),
    "opus-5": ModelSpec(alias="opus-5", provider="anthropic", role="target", model_id="claude-opus-5",
                        omit_temperature=True, thinking_budget=4096, timeout=_SLOW,
                        notes="Claude Opus 5. Extended thinking captured.", tags=("claude", "target")),
    "fable-5": ModelSpec(alias="fable-5", provider="anthropic", role="target", model_id="claude-fable-5",
                         omit_temperature=True, thinking_budget=4096,
                         notes="Claude Fable 5. Extended thinking captured.", tags=("claude", "target")),
    "gpt-5.6": ModelSpec(alias="gpt-5.6", provider="openai", role="target", model_id="gpt-5.6-sol",
                         api_key_env="OPENAI_API_KEY", omit_temperature=True,
                         responses_api=True, reasoning_effort="medium", reasoning_summary="detailed",
                         notes="GPT-5.6 Sol, via the Responses API (reasoning ON + function tools).",
                         tags=("openai", "target")),
    "gpt-5-mini": ModelSpec(alias="gpt-5-mini", provider="openai", role="target", model_id="gpt-5-mini-2025-08-07",
                            api_key_env="OPENAI_API_KEY", omit_temperature=True,
                            responses_api=True, reasoning_effort="medium", reasoning_summary="concise",
                            notes="GPT-5 mini, via the Responses API.", tags=("openai", "target")),
    "gemini-3.1-pro": ModelSpec(alias="gemini-3.1-pro", provider="google", role="target",
                                model_id="gemini-3.1-pro-preview", api_key_env="GEMINI_API_KEY",
                                default_extra_body=_GEMINI_THOUGHTS, timeout=_SLOW,
                                notes="Gemini 3.1 Pro (preview), via the OpenAI-compatible endpoint.",
                                tags=("google", "target")),
    "gemini-3.6-flash": ModelSpec(alias="gemini-3.6-flash", provider="google", role="target",
                                  model_id="gemini-3.6-flash", api_key_env="GEMINI_API_KEY",
                                  default_extra_body=_GEMINI_THOUGHTS, timeout=_SLOW,
                                  notes="Gemini 3.6 Flash, via the OpenAI-compatible endpoint.",
                                  tags=("google", "target")),
    "deepseek-v4": ModelSpec(alias="deepseek-v4", provider="openrouter", role="target",
                             model_id="deepseek/deepseek-v4-pro", api_key_env="OPENROUTER_API_KEY",
                             notes="DeepSeek V4 Pro, via OpenRouter.", tags=("open", "target")),
    "mistral-medium": ModelSpec(alias="mistral-medium", provider="openrouter", role="target",
                                model_id="mistralai/mistral-medium-3-5", api_key_env="OPENROUTER_API_KEY",
                                notes="Mistral Medium 3.5, via OpenRouter.", tags=("open", "target")),
    "kimi-k3": ModelSpec(alias="kimi-k3", provider="openrouter", role="target", model_id="moonshotai/kimi-k3",
                         api_key_env="OPENROUTER_API_KEY", notes="Kimi K3, via OpenRouter.",
                         tags=("open", "target")),
}


def get_model(alias: str) -> ModelSpec:
    if alias not in _REGISTRY:
        raise KeyError(f"Unknown model alias '{alias}'. Known: {sorted(_REGISTRY)}")
    return _REGISTRY[alias]


def list_models() -> list[ModelSpec]:
    return list(_REGISTRY.values())


def available_models() -> dict[str, bool]:
    """Which registry models have their API key set right now. No API call; never prints a value."""
    status: dict[str, bool] = {}
    for spec in _REGISTRY.values():
        try:
            spec.resolve_api_key()
            status[spec.alias] = True
        except RuntimeError:
            status[spec.alias] = False
    return status
