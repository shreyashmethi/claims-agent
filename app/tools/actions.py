"""Mock enterprise services (write side): payments, credit notes, replacement orders, carrier recovery,
CAPA tickets and claimant messages.

Every financial or external action requires a signed approval token bound to the claim, the action type and
the amount, and an idempotency key so a resumed or retried graph can never execute twice.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app import approvals, db


class ApprovalRequired(PermissionError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _execute(claim_id: str, action: str, idempotency_key: str, payload: dict, ref_prefix: str) -> dict:
    prior = db.one("SELECT result FROM actions_log WHERE idempotency_key=?", (idempotency_key,))
    if prior:
        return {**db.jload(prior["result"]), "status": "duplicate_suppressed"}
    ref = f"{ref_prefix}-{uuid.uuid4().hex[:8].upper()}"
    result = {"status": "executed", "ref": ref, "action": action, **payload}
    db.ex("INSERT INTO actions_log VALUES (?,?,?,?,?,?)",
          (idempotency_key, claim_id, action, db.jdump(payload), db.jdump(result), _now()))
    return result


def _require(token: str | None, claim_id: str, action: str, amount: float) -> None:
    if not approvals.verify(token, claim_id, action, amount):
        raise ApprovalRequired(f"{action} for {claim_id} amount {amount} has no valid approval token")


def issue_payment(claim_id: str, payee_id: str, amount: float, approval_token: str, idempotency_key: str) -> dict:
    _require(approval_token, claim_id, "payment", amount)
    return _execute(claim_id, "payment", idempotency_key, {"payee_id": payee_id, "amount": amount,
                                                          "channel": "mock-NEFT"}, "PAY")


def issue_credit_note(claim_id: str, dealer_id: str, amount: float, approval_token: str,
                      idempotency_key: str) -> dict:
    _require(approval_token, claim_id, "credit_note", amount)
    return _execute(claim_id, "credit_note", idempotency_key, {"dealer_id": dealer_id, "amount": amount}, "CN")


def create_replacement_order(claim_id: str, part_no: str, qty: int, ship_to: str, value: float,
                             approval_token: str, idempotency_key: str) -> dict:
    _require(approval_token, claim_id, "replacement_order", value)
    return _execute(claim_id, "replacement_order", idempotency_key,
                    {"part_no": part_no, "qty": qty, "ship_to": ship_to, "value": value,
                     "return_authorisation": f"RMA-{claim_id[-4:]}"}, "RO")


def file_carrier_claim(claim_id: str, shipment_id: str, carrier: str, amount: float, approval_token: str,
                       idempotency_key: str) -> dict:
    _require(approval_token, claim_id, "carrier_recovery", amount)
    return _execute(claim_id, "carrier_recovery", idempotency_key,
                    {"shipment_id": shipment_id, "carrier": carrier, "amount": amount}, "CRC")


def create_capa_ticket(claim_id: str, title: str, body: str, kind: str, approval_token: str,
                       idempotency_key: str) -> dict:
    _require(approval_token, claim_id, "capa_ticket", 0)
    return _execute(claim_id, "capa_ticket", idempotency_key, {"title": title, "body": body, "kind": kind}, "CAPA")


def send_message(claim_id: str, recipient: str, subject: str, body: str, kind: str, idempotency_key: str,
                 approval_token: str | None = None) -> dict:
    if kind == "decision_letter":
        _require(approval_token, claim_id, "decision_letter", 0)
    res = _execute(claim_id, f"message:{kind}", idempotency_key, {"recipient": recipient, "subject": subject}, "MSG")
    if res["status"] == "executed":
        db.ex("INSERT INTO outbox VALUES (?,?,?,?,?,?,?)",
              (res["ref"], claim_id, recipient, subject, body, kind, _now()))
    return res


def request_information(claim_id: str, recipient: str, items: list[str], message: str, round: int) -> dict:
    """Templated, low-risk outbound request. Logged, no approval token required (see HITL matrix)."""
    return send_message(claim_id, recipient, f"Additional information needed for claim {claim_id}",
                        message, "info_request", idempotency_key=f"{claim_id}:info:{round}")


def record_correction(claim_id: str, field: str, value: str, evidence_ids: list[str], reason: str) -> dict:
    """Internal case update: correct an extracted field on documentary evidence. Original value is kept."""
    claim = db.get_claim(claim_id)
    data = claim["data"]
    old = db.effective_fields(claim).get(field)
    data.setdefault("corrections", {})[field] = {"value": value, "previous": old, "evidence_ids": evidence_ids,
                                                 "reason": reason}
    db.save_claim_data(claim_id, data)
    if field in ("serial", "invoice_no", "part_no"):
        db.update_claim(claim_id, **{field: value})
    return {"field": field, "previous": old, "value": value}
