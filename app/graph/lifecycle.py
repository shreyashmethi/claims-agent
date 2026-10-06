"""Claim lifecycle state machine. Agents request transitions; only these guarded edges are possible."""
from __future__ import annotations

from app import db, trace

ALLOWED: dict[str, set[str]] = {
    "RECEIVED": {"CLASSIFIED", "ESCALATED"},
    "CLASSIFIED": {"UNDER_REVIEW", "PENDING_INFO", "ESCALATED"},
    "UNDER_REVIEW": {"PENDING_INFO", "RECOMMENDED", "ESCALATED"},
    "PENDING_INFO": {"UNDER_REVIEW", "ESCALATED"},
    "RECOMMENDED": {"PENDING_APPROVAL", "UNDER_REVIEW", "ESCALATED"},
    "PENDING_APPROVAL": {"APPROVED", "PARTIALLY_APPROVED", "REJECTED", "UNDER_REVIEW", "ESCALATED"},
    "APPROVED": {"SETTLING"},
    "PARTIALLY_APPROVED": {"SETTLING"},
    "REJECTED": {"CLOSED"},
    "SETTLING": {"SETTLED", "ESCALATED"},
    "SETTLED": {"CLOSED"},
    "CLOSED": set(),
    "ESCALATED": set(),
}


class IllegalTransition(RuntimeError):
    pass


def transition(claim_id: str, current: str, new: str, reason: str = "") -> str:
    if new == current:
        return current
    if new not in ALLOWED.get(current, set()):
        raise IllegalTransition(f"{claim_id}: {current} -> {new} is not allowed")
    db.update_claim(claim_id, status=new)
    trace.emit(claim_id, "system", "status_change", f"{current} -> {new}" + (f" ({reason})" if reason else ""),
               from_status=current, to_status=new)
    return new


def path(claim_id: str, current: str, *steps: str, reason: str = "") -> str:
    for s in steps:
        current = transition(claim_id, current, s, reason)
    return current
