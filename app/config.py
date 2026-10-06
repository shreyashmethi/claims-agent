"""Central configuration. Paths are resolved at call time so tests can redirect data via env vars."""
from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = ROOT / "data" / "policies"          # policies are source-controlled, not generated
RULE_PARAMS_FILE = ROOT / "app" / "rules" / "params.yaml"
MODELS_FILE = Path(os.getenv("CLAIMS_MODELS_FILE", ROOT / "models.yaml"))

# The simulation runs on a fixed "today" so the seeded scenarios are reproducible.
SIM_DATE = date.fromisoformat(os.getenv("CLAIMS_SIM_DATE", "2026-10-07"))
APPROVAL_SECRET = os.getenv("APPROVAL_SECRET", "dev-only-secret-change-me").encode()
MAX_AGENT_TURNS = int(os.getenv("MAX_AGENT_TURNS", "30"))
MAX_INFO_ROUNDS = int(os.getenv("MAX_INFO_ROUNDS", "2"))
MAX_VERIFY_RETRIES = 2


def data_dir() -> Path:
    return Path(os.getenv("CLAIMS_DATA_DIR", ROOT / "data"))


def db_path() -> Path:
    return Path(os.getenv("CLAIMS_DB", data_dir() / "claims.db"))


def docs_dir() -> Path:
    return data_dir() / "documents"


def checkpoint_path() -> Path:
    return data_dir() / "checkpoints.db"


@lru_cache
def rule_params() -> dict:
    return yaml.safe_load(RULE_PARAMS_FILE.read_text())


def models_config() -> dict:
    return yaml.safe_load(MODELS_FILE.read_text())
