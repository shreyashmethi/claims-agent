"""Shared case state: one per claim, persisted by the LangGraph checkpointer between pauses."""
from __future__ import annotations

from typing import Any, TypedDict


class CaseState(TypedDict, total=False):
    claim_id: str
    status: str
    claim: dict[str, Any]               # type, effective fields, extracted docs, corrections, signals, parties
    plan: list[str]                     # supervisor's current plan, revised every turn
    evidence: list[dict]                # evidence ledger (EV-###): tool, args, data, hash
    findings: list[dict]                # discrepancies, matches, risks (with needs / resolution)
    completeness: dict | None
    eligibility: dict | None
    investigation: dict | None
    duplicate: dict | None
    calc: dict | None
    recommendation: dict | None
    verification: dict | None
    approval: dict | None               # approval_id, required_roles, records, final, tokens
    actions: list[dict]
    info_requests: list[dict]
    flags: dict[str, Any]               # rev, done{agent: facts_version}, turns, verify_failures, rec_version
    next: str
    settlement_ok: bool | None
    outcome: str | None
    escalation: dict | None


def initial_state(claim_id: str, status: str = "RECEIVED") -> CaseState:
    return CaseState(
        claim_id=claim_id, status=status, claim={}, plan=[], evidence=[], findings=[], completeness=None,
        eligibility=None, investigation=None, duplicate=None, calc=None, recommendation=None, verification=None,
        approval=None, actions=[], info_requests=[], next="", settlement_ok=None, outcome=None, escalation=None,
        flags={"rev": 0, "done": {}, "turns": 0, "verify_failures": 0, "rec_version": None, "sent_back": 0},
    )
