"""Helpers shared by agents and graph nodes."""
from __future__ import annotations

from app import db


def claim_snapshot(claim_id: str) -> dict:
    """The claim as agents see it: type, effective fields, extracted documents, corrections, signals."""
    c = db.get_claim(claim_id)
    data = c["data"]
    return {
        "type": data.get("type"),
        "type_confidence": data.get("type_confidence"),
        "fields": db.effective_fields(c),
        "docs": data.get("docs", {}),
        "corrections": data.get("corrections", {}),
        "signals": data.get("signals", {}),
        "parties": data.get("parties", {}),
        "submitted_on": c["submitted_on"],
    }


def facts_version(state: dict) -> str:
    """Changes when new documents arrive (rev) or a field is corrected; stale analyses must re-run."""
    flags = state.get("flags", {})
    n = len(state.get("claim", {}).get("corrections", {}))
    return f"{flags.get('rev', 0)}.{n}.{flags.get('sent_back', 0)}"


def requested_items(state: dict) -> set[str]:
    return {i for r in state.get("info_requests", []) for i in r["items"]}


def open_needs(state: dict) -> list[str]:
    needs: list[str] = []
    for f in state.get("findings", []):
        if not f.get("resolved"):
            needs += [n for n in f.get("needs", []) if n not in needs]
    return needs
