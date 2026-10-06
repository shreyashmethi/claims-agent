"""Runs and resumes claim graphs. One LangGraph thread per claim; state survives restarts in SQLite."""
from __future__ import annotations

import sqlite3
import threading
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

import app.tools.catalog  # noqa: F401  (registers tools)
from app import config, db
from app.graph.build_graph import build
from app.graph.state import initial_state

_graphs: dict[str, Any] = {}
_locks: dict[str, threading.Lock] = {}
_glock = threading.Lock()


def graph():
    key = str(config.checkpoint_path())
    with _glock:
        if key not in _graphs:
            config.checkpoint_path().parent.mkdir(parents=True, exist_ok=True)
            _graphs[key] = build(SqliteSaver(sqlite3.connect(key, check_same_thread=False)))
        return _graphs[key]


def reset_graphs() -> None:
    _graphs.clear()


def _cfg(claim_id: str) -> dict:
    return {"configurable": {"thread_id": claim_id}, "recursion_limit": 300}


def _lock(claim_id: str) -> threading.Lock:
    with _glock:
        return _locks.setdefault(claim_id, threading.Lock())


def start(claim_id: str) -> dict:
    claim = db.get_claim(claim_id)
    if not claim:
        raise KeyError(claim_id)
    with _lock(claim_id):
        if graph().get_state(_cfg(claim_id)).values:
            raise RuntimeError(f"{claim_id} has already been started; use resume")
        graph().invoke(initial_state(claim_id, claim["status"] or "RECEIVED"), _cfg(claim_id))
    return status(claim_id)


def resume(claim_id: str, value: Any) -> dict:
    with _lock(claim_id):
        if not pending(claim_id):
            raise RuntimeError(f"{claim_id} is not waiting for input")
        graph().invoke(Command(resume=value), _cfg(claim_id))
    return status(claim_id)


def pending(claim_id: str) -> dict | None:
    snap = graph().get_state(_cfg(claim_id))
    for task in snap.tasks:
        for intr in task.interrupts:
            return intr.value
    return None


def state(claim_id: str) -> dict:
    return graph().get_state(_cfg(claim_id)).values


def status(claim_id: str) -> dict:
    s = state(claim_id)
    return {"claim_id": claim_id, "status": s.get("status"), "waiting_for": pending(claim_id),
            "recommendation": s.get("recommendation"), "final": (s.get("approval") or {}).get("final"),
            "outcome": s.get("outcome")}
