"""Which model and call options a chat request actually uses.

Precedence (highest first):
  1. the request: `model` (primary alias only) and `options.reasoning`
  2. the alias's stored options (admin, providers.options column)
  3. the recommended defaults below (ALIAS_DEFAULTS)
Provider-specific translation (reasoning_effort / thinking / temperature rules)
lives in each service; this module only decides the portable values.
"""
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .config import AIConfig, ProviderConfig, get_setting, heal_model_id

REASONING_LEVELS = ("off", "low", "medium", "high")

# Recommended defaults when neither the request nor the admin sets an option
# (2026-09-19 benchmark: Korean Bible-QA prompt, blind-judged quality, latency).
ALIAS_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "gemini-flash": {"reasoning": "low",
                     "fallback_model": "gemini-3.5-flash-lite", "fallback_after_s": 6},
    "chatgpt": {"reasoning": "off"},
    "openai": {"reasoning": "off"},
    "claude-sonnet": {"reasoning": "off"},
    "claude-haiku": {"reasoning": "off"},
}

# Model-ID prefixes each alias family can serve (a request may only pick a
# model of the alias's own vendor, since the alias picks the service class).
_FAMILY_PREFIXES = {
    "anthropic": ("claude-",),
    "openai": ("gpt-", "o1", "o3", "o4", "chatgpt-"),
    "google": ("gemini-",),
    "moonshot": ("kimi-", "moonshot-"),
    "perplexity": ("sonar",),
}
ALIAS_FAMILY = {
    "claude-sonnet": "anthropic", "claude-haiku": "anthropic",
    "chatgpt": "openai", "openai": "openai",
    "gemini-pro": "google", "gemini-flash": "google",
    "moonshot": "moonshot", "perplexity": "perplexity",
}


# Models a request may pick with `model` (besides every alias's configured model
# and its same-family fallback, which are always allowed). Admin-editable via
# the `allowed_models` setting; empty/unset means this default list.
DEFAULT_ALLOWED_MODELS = [
    "claude-sonnet-5", "claude-haiku-4-5", "claude-haiku-4-5-20251001",
    "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol",
    "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-pro-latest",
]


class ModelFamilyMismatch(ValueError):
    pass


class ModelNotAllowed(ValueError):
    pass


def configured_allowed_models() -> list:
    """The admin list, or the default list when unset."""
    raw = get_setting("allowed_models")
    if raw:
        try:
            value = json.loads(raw)
            if isinstance(value, list):
                return [str(m) for m in value]
        except ValueError:
            pass
    return list(DEFAULT_ALLOWED_MODELS)


def always_allowed_models(config: AIConfig) -> list:
    """Every chat alias's configured model and same-family fallback model."""
    models = set()
    for alias, p in config.providers.items():
        if p.service_type == "chat":
            models.add(p.model)
            fb = effective_options(alias, p).get("fallback_model")
            if fb:
                models.add(heal_model_id(fb))
    return sorted(models)


def check_model_allowed(model: str, config: AIConfig) -> None:
    if model in configured_allowed_models() or model in always_allowed_models(config):
        return
    raise ModelNotAllowed(
        f"Model '{model}' is not in the gateway's allowed list; ask the gateway admin to add it")


@dataclass
class CallPlan:
    alias: str
    model: str                       # model for the first attempt
    reasoning: Optional[str]         # None = provider default
    fallback_model: Optional[str]    # same-family model tried if the first attempt fails
    fallback_after_s: Optional[float]


def effective_options(alias: str, provider: Optional[ProviderConfig]) -> Dict[str, Any]:
    """Alias defaults merged with the admin-stored options (for calls and the UI)."""
    merged = dict(ALIAS_DEFAULTS.get(alias, {}))
    if provider is not None:
        merged.update({k: v for k, v in (provider.options or {}).items() if v is not None})
    return merged


def plan_call(alias: str, provider: ProviderConfig, request_model: Optional[str],
              request_reasoning: Optional[str], allowed: Optional[AIConfig] = None) -> CallPlan:
    """`allowed`: the config to check a request model against (None: no check)."""
    opts = effective_options(alias, provider)
    model = provider.model
    if request_model:
        requested = heal_model_id(request_model.strip())
        family = ALIAS_FAMILY.get(alias)
        if family and not requested.startswith(_FAMILY_PREFIXES[family]):
            raise ModelFamilyMismatch(
                f"Model '{request_model}' cannot be served by provider '{alias}' ({family})")
        if requested != provider.model and allowed is not None:
            check_model_allowed(requested, allowed)
        model = requested

    reasoning = request_reasoning or opts.get("reasoning")
    if reasoning not in REASONING_LEVELS:
        reasoning = None

    # Same-family fallback only protects the alias's configured model
    fb = opts.get("fallback_model")
    if fb:
        fb = heal_model_id(fb)
    if not fb or model != provider.model or fb == model:
        return CallPlan(alias, model, reasoning, None, None)
    return CallPlan(alias, model, reasoning, fb, float(opts.get("fallback_after_s") or 6))
