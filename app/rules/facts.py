"""Builds the fact set the rules evaluate. Rules are pure functions over these facts."""
from __future__ import annotations

from datetime import date

from app import db
from app.documents import to_number


def d(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def docs_by_kind(claim: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for doc in (claim.get("data") or {}).get("docs", {}).values():
        out.setdefault(doc["kind"], []).append(doc)
    return out


def build_facts(claim_id: str) -> dict:
    claim = db.get_claim(claim_id)
    if not claim:
        raise ValueError(f"unknown claim {claim_id}")
    data = claim["data"]
    fields = db.effective_fields(claim)
    kinds = docs_by_kind(claim)
    f: dict = {
        "claim_id": claim_id,
        "type": data.get("type"),
        "fields": fields,
        "docs": kinds,
        "submitted_on": d(claim["submitted_on"]),
        "claimed_amount": to_number(fields.get("claimed_amount")) or 0.0,
        "claimant_id": fields.get("claimant_id") or claim["claimant_id"],
    }
    serial = fields.get("serial")
    if serial:
        f["equipment"] = db.one("SELECT * FROM equipment WHERE serial=?", (serial,))
        f["contract"] = db.one("SELECT * FROM warranty_contracts WHERE serial=?", (serial,))
        if f["contract"]:
            f["contract"]["covered"] = db.jload(f["contract"]["covered"], [])
            f["contract"]["exclusions"] = db.jload(f["contract"]["exclusions"], [])
    f["dealer"] = db.one("SELECT * FROM dealers WHERE dealer_id=?", (f["claimant_id"],))
    if f["dealer"]:
        f["dealer"]["authorised_services"] = db.jload(f["dealer"]["authorised_services"], [])
    f["employee"] = db.one("SELECT * FROM employees WHERE employee_id=?", (f["claimant_id"],))
    disp_id = fields.get("dispatch_id")
    if not disp_id and fields.get("shipment_id"):
        sh = db.one("SELECT dispatch_id FROM shipments WHERE shipment_id=?", (fields["shipment_id"],))
        disp_id = sh and sh["dispatch_id"]
    if disp_id:
        f["dispatch"] = db.one("SELECT * FROM dispatches WHERE dispatch_id=?", (disp_id,))
        f["shipment"] = db.one("SELECT * FROM shipments WHERE dispatch_id=?", (disp_id,))
    part_no = fields.get("part_no")
    if part_no:
        f["part"] = db.one("SELECT * FROM parts WHERE part_no=?", (part_no,))
    # Line items: from the invoice (warranty, dealer) or the expense report (employee claim form).
    src = (kinds.get("invoice") or kinds.get("claim_form") or [{}])[0]
    if f["type"] == "employee_reimbursement":
        src = (kinds.get("claim_form") or [{}])[0]
    f["lines"] = src.get("lines", [])
    return f
