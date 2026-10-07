"""Fail fast with an actionable message when required packages are missing or too old."""
from __future__ import annotations

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
    return out


def check(exit_on_error: bool = True) -> bool:
    found = problems()
    if found:
        print("Missing or outdated dependencies:\n  - " + "\n  - ".join(found), file=sys.stderr)
        print("\nInstall them into a virtual environment from the project folder:\n"
              "  python3 -m venv .venv && source .venv/bin/activate\n"
              "  pip install -r requirements-dev.txt\n"
              "or run: make setup", file=sys.stderr)
        if exit_on_error:
            sys.exit(1)
    return not found


if __name__ == "__main__":
    check()
    print("All dependencies present.")
