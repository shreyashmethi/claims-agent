"""Append-only execution trace. Every plan, delegation, tool call, rule firing, approval and action is an event."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable

from app import db

_listeners: list[Callable[[dict], None]] = []
_lock = threading.Lock()

EVENT_TYPES = {
    "plan", "delegate", "tool_call", "tool_error", "rule_fired", "finding", "info_request", "info_response",
    "recommendation", "verification", "approval_request", "approval", "action", "status_change", "escalation",
    "reasoning", "llm_fallback", "guardrail", "memory",
}


def subscribe(fn: Callable[[dict], None]) -> None:
    _listeners.append(fn)


def unsubscribe(fn: Callable[[dict], None]) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def emit(claim_id: str, agent: str, type: str, summary: str = "", **payload: Any) -> dict:
    assert type in EVENT_TYPES, type
    with _lock:
        row = db.one("SELECT COALESCE(MAX(seq),0) AS s FROM trace WHERE claim_id=?", (claim_id,))
        seq = row["s"] + 1
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        db.ex("INSERT INTO trace VALUES (?,?,?,?,?,?,?)",
              (claim_id, seq, ts, agent, type, summary, db.jdump(payload)))
    event = {"claim_id": claim_id, "seq": seq, "ts": ts, "agent": agent, "type": type,
             "summary": summary, **payload}
    for fn in list(_listeners):
        try:
            fn(event)
        except Exception:  # a broken listener must never break the case
            pass
    return event


def get_trace(claim_id: str, after: int = 0) -> list[dict]:
    rows = db.q("SELECT * FROM trace WHERE claim_id=? AND seq>? ORDER BY seq", (claim_id, after))
    out = []
    for r in rows:
        payload = db.jload(r.pop("payload"), {})
        out.append({**r, **payload})
    return out
