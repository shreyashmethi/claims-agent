"""Verifier: deterministic checks on a recommendation before it can reach an approver. Code, not an LLM."""
from __future__ import annotations

from app.schemas import Verification

FINANCIAL = {"payment", "credit_note", "replacement_order", "carrier_recovery"}


def verify(state: dict, rec: dict, calc: dict | None, evidence_ids: set[str]) -> list[str]:
    errs: list[str] = []
    elig = state.get("eligibility") or {}
    dup = state.get("duplicate") or {}
    d, amt = rec["decision"], rec["amount"]
    if d in ("approve", "partial"):
        if not calc:
            errs.append("approve/partial requires a rules_calculate_settlement result")
        else:
            if abs(amt - calc["admissible_amount"]) > 0.01:
                errs.append(f"amount {amt} != calculator admissible_amount {calc['admissible_amount']}")
            full = abs(calc["admissible_amount"] - calc["claimed_amount"]) < 0.01
            if d == "approve" and not full:
                errs.append("admissible amount is below claimed: decision must be 'partial'")
            if d == "partial" and full:
                errs.append("admissible amount equals claimed: decision must be 'approve'")
        if amt <= 0:
            errs.append("approve/partial requires a positive amount")
        if dup.get("is_duplicate"):
            errs.append(f"claim is a duplicate of {dup.get('duplicate_of')}: cannot approve")
        if elig and not elig.get("eligible"):
            errs.append(f"blocking rules failed {elig.get('blocking')}: cannot approve")
    if d == "reject" and amt != 0:
        errs.append("reject must carry amount 0")
    if elig.get("with_exception") and d in ("approve", "partial") and not rec.get("exception"):
        errs.append("eligibility requires a policy exception: set exception=true with a reason")
    if not rec["reasons"]:
        errs.append("at least one reason is required")
    for r in rec["reasons"]:
        if not r["evidence_ids"]:
            errs.append(f"reason without evidence: '{r['text'][:60]}'")
        for e in r["evidence_ids"]:
            if e not in evidence_ids:
                errs.append(f"reason cites unknown evidence {e}")
    for a in rec.get("actions", []):
        if a["type"] in FINANCIAL:
            v = a["params"].get("amount", a["params"].get("value"))
            if v is not None and abs(v - amt) > 0.01:
                errs.append(f"{a['type']} amount {v} != recommended {amt}")
        if a["type"] in FINANCIAL and d not in ("approve", "partial"):
            errs.append(f"{a['type']} not allowed on decision {d}")
    return errs


def run(state: dict) -> dict:
    rec = state["recommendation"]
    ids = {e["id"] for e in state.get("evidence", [])}
    errs = verify(state, rec, state.get("calc"), ids)
    return {"verification": Verification(passed=not errs, errors=errs).model_dump()}
