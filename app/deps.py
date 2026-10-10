"""Fail fast with an actionable message when required packages are missing or too old."""
from __future__ import annotations

import os
import sys
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version

REQUIRED = [  # (distribution, import name, minimum major.minor)
    ("langgraph", "langgraph", (1, 2)),
    ("langgraph-checkpoint-sqlite", "langgraph.checkpoint.sqlite", (3, 1)),
    ("langchain", "langchain", (1, 4)),
    ("langchain-core", "langchain_core", (1, 6)),
    ("fastapi", "fastapi", (0, 110)),
    ("uvicorn", "uvicorn", (0, 30)),
    ("pydantic", "pydantic", (2, 10)),
    ("pyyaml", "yaml", (6, 0)),
    ("reportlab", "reportlab", (4, 0)),
    ("pypdf", "pypdf", (5, 0)),
    ("rank-bm25", "rank_bm25", (0, 2)),
    ("rich", "rich", (13, 0)),
    ("python-multipart", "multipart", (0, 0)),
]

PROVIDERS = {  # LLM_PROVIDER -> (adapter distribution, import name, API key env var)
    "anthropic": ("langchain-anthropic", "langchain_anthropic", "ANTHROPIC_API_KEY"),
    "openai": ("langchain-openai", "langchain_openai", "OPENAI_API_KEY"),
    "google_genai": ("langchain-google-genai", "langchain_google_genai", "GOOGLE_API_KEY"),
    "ollama": ("langchain-ollama", "langchain_ollama", None),
}


def _ver(dist: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(x) for x in version(dist).split(".")[:2] if x.isdigit())
    except PackageNotFoundError:
        return None


def problems() -> list[str]:
    out = []
    if sys.version_info < (3, 11):
        out.append(f"Python {sys.version.split()[0]} found; 3.11 or newer is required")
    for dist, mod, minimum in REQUIRED:
        v = _ver(dist)
        try:
            import_module(mod)
        except ImportError:
            out.append(f"{dist} is not installed" if v is None else f"{dist} {'.'.join(map(str, v))} cannot be imported")
            continue
        if v is not None and v < minimum:
            out.append(f"{dist} {'.'.join(map(str, v))} is older than {'.'.join(map(str, minimum))}")
    return out or _provider_problems()


def _provider_problems() -> list[str]:
    """The configured LLM provider needs its adapter and key, or every agent silently falls back to offline."""
    from app.llm.provider import provider_name
    provider = provider_name()
    if provider == "offline":
        return []
    if provider not in PROVIDERS:
        return [f"LLM_PROVIDER={provider} is not supported; use offline, " + ", ".join(PROVIDERS)]
    dist, mod, key = PROVIDERS[provider]
    out = []
    try:
        import_module(mod)
    except ImportError:
        out.append(f"{dist} is not installed (needed for LLM_PROVIDER={provider}); "
                   "pip install -r requirements-llm.txt, or set LLM_PROVIDER=offline")
    if key and not os.getenv(key):
        out.append(f"{key} is not set (needed for LLM_PROVIDER={provider})")
    return out


def check(exit_on_error: bool = True) -> bool:
    found = problems()
    if found:
        print("Missing or outdated dependencies:\n  - " + "\n  - ".join(found), file=sys.stderr)
        print("\nInstall them into a virtual environment from the project folder:\n"
              "  python3 -m venv .venv && source .venv/bin/activate\n"
              "  pip install -r requirements-dev.txt  (plus requirements-llm.txt for an LLM provider)\n"
              "or run: make setup", file=sys.stderr)
        if exit_on_error:
            sys.exit(1)
    return not found


if __name__ == "__main__":
    check()
    print("All dependencies present.")
