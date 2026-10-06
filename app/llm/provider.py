"""Provider-agnostic model factory. Agents ask for a role; config decides provider and model."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from app.config import models_config

_override: dict[str, Any] = {}


def provider_name() -> str:
    return os.getenv("LLM_PROVIDER") or models_config().get("provider", "offline")


def set_model_override(model: Any | None, role: str = "*") -> None:
    """Inject a chat model (tests use a scripted fake). None clears the override."""
    if model is None:
        _override.pop(role, None)
    else:
        _override[role] = model


def llm_enabled() -> bool:
    return bool(_override) or provider_name() != "offline"


@lru_cache
def _build(provider: str, model: str, temperature: float, options: tuple) -> Any:
    from langchain.chat_models import init_chat_model
    return init_chat_model(model, model_provider=provider, temperature=temperature, **dict(options))


def get_chat_model(role: str) -> Any | None:
    if role in _override:
        return _override[role]
    if "*" in _override:
        return _override["*"]
    provider = provider_name()
    if provider == "offline":
        return None
    cfg = models_config()
    pcfg = cfg["providers"][provider]
    model = os.getenv(f"LLM_MODEL_{role.upper()}") or pcfg.get(role) or os.getenv("LLM_MODEL") or pcfg["default"]
    options = tuple(sorted((pcfg.get("options") or {}).items()))
    return _build(provider, model, cfg.get("settings", {}).get("temperature", 0), options)


def max_react_steps() -> int:
    return models_config().get("settings", {}).get("max_react_steps", 10)
