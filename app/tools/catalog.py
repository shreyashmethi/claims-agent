"""Registers every tool with its side-effect class and the agents allowed to use it."""
from __future__ import annotations

from app import db
from app.rules import engine
from app.tools import actions, checks, services
from app.tools.registry import register


def classify_claim(claim_id: str) -> dict:
    """Classify the claim type from the extracted claim form and the mix of supporting documents."""
    claim = db.get_claim(claim_id)
    docs = claim["data"].get("docs", {}).values()
    form = next((d for d in docs if d["kind"] == "claim_form"), None)
    if not form:
        return {"type": None, "confidence": 0.0, "reason": "no claim form extracted yet"}
    return services.classify_claim(form["fields"], [d["kind"] for d in docs])


def rules_evaluate(claim_id: str) -> dict:
    """Run every eligibility rule for this claim type. Returns rule IDs, clauses, pass/fail and waivability."""
    return engine.evaluate(claim_id).model_dump()


def rules_calculate_settlement(claim_id: str) -> dict:
    """Deterministic admissible-amount calculation with a line-by-line breakdown and the rule behind each line."""
    return engine.calculate_settlement(claim_id).model_dump()


INV = {"investigation"}

READ = [
    ("list_claim_documents", services.list_claim_documents, {"intake", "completeness", "investigation"},
     "List the documents attached to this claim and whether each has been extracted."),
    ("read_document", services.read_document, {"intake"},
     "Read one claim document: raw text, deterministic parse and prompt-injection scan."),
    ("classify_claim", classify_claim, {"intake"}, classify_claim.__doc__),
    ("lookup_equipment", services.lookup_equipment, {"intake", "investigation"},
     "Equipment registry record for a serial number (model, sale date, owner, selling dealer)."),
    ("lookup_party", services.lookup_party, {"intake", "investigation", "settlement"},
     "Dealer, customer or employee master record by ID."),
    ("warranty_contract", services.warranty_contract, {"policy", "investigation"},
     "Warranty contract for a serial: plan, period, hours cap, covered component groups, exclusions."),
    ("service_history", services.service_history, {"investigation", "duplicate_pattern"},
     "Dealer service records for a serial, optionally filtered by job code."),
    ("get_invoice", services.get_invoice, {"investigation", "duplicate_pattern"},
     "ERP invoice by number: party, date, total, lines. Includes invoices already reimbursed."),
    ("parts_record", services.parts_record, {"investigation", "duplicate_pattern"},
     "Parts catalog entry and/or a dispatch record (ordered vs packed part, batch, value)."),
    ("shipment_record", services.shipment_record, {"investigation"},
     "Logistics record: carrier, dispatch/delivery dates, POD remark, insured value, shipped serial."),
    ("search_claims", services.search_claims, {"intake", "duplicate_pattern", "investigation"},
     "Search past claims by invoice number, serial, part number, claimant or outcome."),
    ("similar_claims", services.similar_claims, {"duplicate_pattern"},
     "Past claims with similar narratives (near-duplicate detection)."),
    ("failure_stats", services.failure_stats, {"duplicate_pattern", "investigation"},
     "Failure counts for a part number over a window, by supplier batch, with affected units and dealers."),
    ("search_policy", services.search_policy, {"policy", "adjudication", "investigation", "completeness"},
     "Retrieve policy clauses by clause ID or free-text query."),
    ("get_precedents", services.get_precedents, {"adjudication"},
     "Approver overrides on similar past cases (precedents; context only, never automatic exceptions)."),
    ("required_docs", services.required_docs, {"completeness"},
     "Documents required for this claim type, including conditional ones (pre-auth, receipts)."),
    ("check_doc_quality", services.check_doc_quality, {"completeness"},
     "Check one extracted document for its mandatory fields."),
    ("run_check", checks.run_check, INV,
     f"Run a named deterministic cross-check: one of {checks.CHECKS}."),
    ("rules_evaluate", rules_evaluate, {"policy"}, rules_evaluate.__doc__),
    ("rules_calculate_settlement", rules_calculate_settlement, {"adjudication"},
     rules_calculate_settlement.__doc__),
]
for name, fn, agents, desc in READ:
    register(name, fn, "read", agents, desc)

register("record_correction", actions.record_correction, "write_internal", {"investigation"},
         "Correct an extracted claim field on documentary evidence (e.g. serial typo shown by a plate photo). "
         "Requires evidence_ids and a reason; the original value is preserved.")
register("request_information", actions.request_information, "write_external", {"system"},
         "Send a templated information request to the claimant.")
register("send_message", actions.send_message, "write_external", {"settlement"},
         "Send a message to the claimant. Decision letters require an approval token.")
register("issue_payment", actions.issue_payment, "financial", {"settlement"},
         "Pay an approved amount to a claimant. Requires an approval token.")
register("issue_credit_note", actions.issue_credit_note, "financial", {"settlement"},
         "Issue a credit note to a dealer account. Requires an approval token.")
register("create_replacement_order", actions.create_replacement_order, "financial", {"settlement"},
         "Dispatch replacement parts at no charge. Requires an approval token.")
register("file_carrier_claim", actions.file_carrier_claim, "write_external", {"settlement"},
         "File a recovery claim against the carrier. Requires an approval token.")
register("create_capa_ticket", actions.create_capa_ticket, "write_internal", {"settlement"},
         "Open a corrective-action ticket in the quality system. Requires an approval token.")
