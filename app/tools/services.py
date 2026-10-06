"""Mock enterprise services (read side). The same functions back the agent tools and the /mock HTTP API."""
from __future__ import annotations

import re
from datetime import date, timedelta

from app import db
from app.config import SIM_DATE, rule_params
from app.documents import parse_text, pdf_text, scan_for_injection, file_hash
from app.tools import policy_store


def _row(table: str, key: str, val: str) -> dict | None:
    r = db.one(f"SELECT * FROM {table} WHERE {key}=?", (val,))
    if r:
        for k in ("covered", "exclusions", "authorised_services", "lines", "parts_used"):
            if k in r and isinstance(r[k], str):
                r[k] = db.jload(r[k], r[k])
    return r


# ---------------------------------------------------------------- master data
def lookup_equipment(serial: str) -> dict:
    eq = _row("equipment", "serial", serial)
    return eq or {"found": False, "serial": serial}


def lookup_party(party_id: str) -> dict:
    for table, key, kind in (("dealers", "dealer_id", "dealer"), ("customers", "customer_id", "customer"),
                             ("employees", "employee_id", "employee")):
        r = _row(table, key, party_id)
        if r:
            return {"party_type": kind, **r}
    return {"found": False, "party_id": party_id}


def warranty_contract(serial: str) -> dict:
    return _row("warranty_contracts", "serial", serial) or {"found": False, "serial": serial}


def service_history(serial: str, job_code: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM service_history WHERE serial=?", [serial]
    if job_code:
        sql += " AND job_code=?"
        params.append(job_code)
    rows = db.q(sql + " ORDER BY date", params)
    for r in rows:
        r["parts_used"] = db.jload(r["parts_used"], [])
    return rows


def get_invoice(invoice_no: str) -> dict:
    """Invoices known to the ERP (sales and previously reimbursed invoices)."""
    return _row("invoices", "invoice_no", invoice_no) or {"found": False, "invoice_no": invoice_no}


def parts_record(part_no: str | None = None, dispatch_id: str | None = None) -> dict:
    out: dict = {}
    if part_no:
        out["part"] = _row("parts", "part_no", part_no) or {"found": False}
    if dispatch_id:
        out["dispatch"] = _row("dispatches", "dispatch_id", dispatch_id) or {"found": False}
    return out


def shipment_record(shipment_id: str | None = None, dispatch_id: str | None = None) -> dict:
    if shipment_id:
        r = _row("shipments", "shipment_id", shipment_id)
    else:
        r = _row("shipments", "dispatch_id", dispatch_id or "")
    if r:
        r["dispatch"] = _row("dispatches", "dispatch_id", r["dispatch_id"])
    return r or {"found": False}


# ---------------------------------------------------------------- claims history (institutional memory)
def search_claims(claim_id: str, invoice_no: str | None = None, serial: str | None = None,
                  part_no: str | None = None, claimant_id: str | None = None, outcome: str | None = None,
                  limit: int = 20) -> list[dict]:
    sql = ("SELECT claim_id, type, claimant_id, serial, component, part_no, batch, invoice_no, amount, "
           "submitted_on, status, outcome, settled_amount, narrative FROM claims WHERE claim_id<>?")
    params: list = [claim_id]
    for col, val in (("invoice_no", invoice_no), ("serial", serial), ("part_no", part_no),
                     ("claimant_id", claimant_id), ("outcome", outcome)):
        if val:
            sql += f" AND {col}=?"
            params.append(val)
    sql += " AND (is_historical=1 OR outcome IS NOT NULL) ORDER BY submitted_on DESC LIMIT ?"
    return db.q(sql, params + [limit])


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9\-]{3,}", (s or "").lower()))


def similar_claims(claim_id: str, k: int = 5) -> list[dict]:
    """Narrative similarity (Jaccard over tokens). Swap for an embedding index in production."""
    me = db.get_claim(claim_id)
    text = (me or {}).get("narrative") or db.effective_fields(me or {"data": {}}).get("description", "")
    mine = _tokens(text)
    rows = db.q("SELECT claim_id, claimant_id, serial, invoice_no, amount, submitted_on, outcome, narrative "
                "FROM claims WHERE claim_id<>? AND (is_historical=1 OR outcome IS NOT NULL)", (claim_id,))
    scored = []
    for r in rows:
        other = _tokens(r["narrative"])
        if mine and other:
            s = len(mine & other) / len(mine | other)
            if s > 0.15:
                scored.append({**r, "similarity": round(s, 2)})
    return sorted(scored, key=lambda x: -x["similarity"])[:k]


def failure_stats(part_no: str, batch: str | None = None, months: int = 12) -> dict:
    since = (SIM_DATE - timedelta(days=30 * months)).isoformat()
    sql = ("SELECT claim_id, serial, claimant_id, batch, submitted_on, outcome FROM claims "
           "WHERE part_no=? AND type='warranty' AND submitted_on>=? AND (is_historical=1 OR outcome IS NOT NULL)")
    params: list = [part_no, since]
    rows = db.q(sql, params)
    by_batch: dict[str, set] = {}
    for r in rows:
        by_batch.setdefault(r["batch"] or "unknown", set()).add(r["serial"])
    out = {"part_no": part_no, "window_months": months, "claims": len(rows),
           "units": sorted({r["serial"] for r in rows}), "by_batch": {b: len(s) for b, s in by_batch.items()}}
    if batch:
        b = [r for r in rows if r["batch"] == batch]
        out["batch"] = {"batch": batch, "claims": len(b), "units": sorted({r["serial"] for r in b}),
                        "dealers": sorted({r["claimant_id"] for r in b}),
                        "unit_dealers": {r["serial"]: r["claimant_id"] for r in b}}
    return out


def search_policy(query: str, k: int = 3) -> list[dict]:
    return policy_store.search(query, k)


def get_precedents(claim_type: str, finding_types: list[str] | None = None, k: int = 3) -> list[dict]:
    """Approver overrides on similar cases (feedback memory). Context only, never an automatic rule."""
    rows = db.q("SELECT * FROM overrides WHERE claim_type=? ORDER BY id DESC LIMIT 50", (claim_type,))
    ft = set(finding_types or [])
    for r in rows:
        r["finding_types"] = db.jload(r["finding_types"], [])
        r["overlap"] = len(ft & set(r["finding_types"]))
    return sorted(rows, key=lambda r: -r["overlap"])[:k]


# ---------------------------------------------------------------- documents
def list_claim_documents(claim_id: str) -> list[dict]:
    claim = db.get_claim(claim_id)
    processed = (claim["data"] if claim else {}).get("docs", {})
    rows = db.q("SELECT doc_id, filename, round, uploaded_on FROM documents WHERE claim_id=? ORDER BY doc_id",
                (claim_id,))
    for r in rows:
        r["processed"] = r["doc_id"] in processed
        r["kind"] = processed.get(r["doc_id"], {}).get("kind")
    return rows


def read_document(claim_id: str, doc_id: str) -> dict:
    """Raw text layer of a document plus the deterministic parse and an injection scan."""
    row = db.one("SELECT * FROM documents WHERE doc_id=? AND claim_id=?", (doc_id, claim_id))
    if not row:
        return {"found": False, "doc_id": doc_id}
    text = pdf_text(row["path"])
    parsed = parse_text(text)
    return {"doc_id": doc_id, "filename": row["filename"], "round": row["round"], "sha": file_hash(row["path"]),
            "text": text, "parsed": parsed, "injection_hits": scan_for_injection(text)}


def classify_claim(fields: dict, doc_kinds: list[str]) -> dict:
    """Rule classifier: the declared claim type corroborated by document mix and keywords."""
    declared = (fields.get("claim_type") or "").strip().lower().replace(" ", "_")
    text = " ".join(str(v) for v in fields.values()).lower()
    votes = {
        "warranty": ("job_code" in fields) + ("failure_date" in fields) + ("warranty" in text),
        "parts_replacement": ("received_part_no" in fields) + ("issue" in fields) + ("dispatch_id" in fields),
        "transit_damage": ("shipment_id" in fields) + ("delivered_on" in fields) + ("transit" in text),
        "dealer_reimbursement": ("service_type" in fields) + ("travel_km" in fields) + ("field" in text),
        "employee_reimbursement": ("employee_id" in fields) + ("trip_end" in fields) + ("purpose" in fields),
    }
    best = max(votes, key=votes.get)
    if declared in votes:
        conf = 0.97 if best == declared or votes[declared] >= 2 else 0.7
        return {"type": declared, "confidence": conf, "declared": declared, "signal_votes": votes}
    return {"type": best, "confidence": 0.6 if votes[best] >= 2 else 0.4, "declared": declared or None,
            "signal_votes": votes}


# ---------------------------------------------------------------- completeness
REQUIRED_DOCS = {
    "warranty": ["claim_form", "invoice", "job_card"],
    "parts_replacement": ["claim_form", "delivery_note", "photo"],
    "transit_damage": ["claim_form", "invoice", "delivery_note", "photo"],
    "dealer_reimbursement": ["claim_form", "invoice", "job_card"],
    "employee_reimbursement": ["claim_form"],
}
REQUIRED_FIELDS = {
    "claim_form": ["claim_type", "claimant_id", "claimed_amount"],
    "invoice": ["invoice_no", "date", "total"],
    "job_card": ["serial", "date", "job_code"],
    "delivery_note": ["delivered_on"],
    "photo": ["photo_of"],
    "receipt": ["for_line", "amount"],
    "carrier_ack": ["shipment_id", "acknowledges_damage"],
}
DOC_LABELS = {"claim_form": "completed claim form", "invoice": "invoice", "job_card": "dealer job card",
              "delivery_note": "delivery note / proof of delivery", "photo": "photographs", "receipt": "receipt",
              "pre_auth": "pre-authorisation letter"}


def required_docs(claim_id: str) -> dict:
    claim = db.get_claim(claim_id)
    t = claim["data"].get("type")
    req = [{"kind": k, "label": DOC_LABELS.get(k, k), "rule": "base"} for k in REQUIRED_DOCS.get(t, [])]
    fields = db.effective_fields(claim)
    p = rule_params()
    if t == "dealer_reimbursement":
        from app.documents import to_number
        if (to_number(fields.get("claimed_amount")) or 0) > p["dealer"]["pre_auth_threshold"]:
            req.append({"kind": "pre_auth", "label": DOC_LABELS["pre_auth"], "rule": "DLR-2.2"})
    if t == "employee_reimbursement":
        form = next((x for x in claim["data"].get("docs", {}).values() if x["kind"] == "claim_form"), None)
        for ln in (form or {}).get("lines", []):
            if ln["code"] not in {"PER_DIEM", "MILEAGE_KM"} and (ln["amount"] or 0) >= p["employee"]["receipt_threshold"]:
                req.append({"kind": "receipt", "for_line": ln["code"], "label": f"receipt for {ln['description']}",
                            "rule": "EMP-2.2"})
    return {"claim_type": t, "required": req}


def check_doc_quality(claim_id: str, doc_id: str) -> dict:
    claim = db.get_claim(claim_id)
    doc = claim["data"].get("docs", {}).get(doc_id)
    if not doc:
        return {"doc_id": doc_id, "found": False}
    need = REQUIRED_FIELDS.get(doc["kind"], [])
    missing = [f for f in need if not str(doc["fields"].get(f, "")).strip()]
    return {"doc_id": doc_id, "kind": doc["kind"], "missing_fields": missing, "ok": not missing,
            "line_items": len(doc.get("lines", []))}


def today() -> str:
    return SIM_DATE.isoformat()


def as_date(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None
