"""Settlement and Communications Agent: executes approved actions with their approval tokens, idempotently,
then writes the decision letter. It has no path to money without a token."""
from __future__ import annotations

from app import db, trace
from app.agents.base import with_fallback
from app.llm.provider import get_chat_model
from app.tools.registry import ToolContext


CLAIMANT_FACING = {"payment", "credit_note", "replacement_order"}


def _letter(state: dict, final: dict, results: list[dict]) -> tuple[str, str]:
    """Claimant-facing letter built from structured data. Internal findings, evidence IDs, fraud signals and
    recovery actions against third parties are deliberately left out."""
    c = state["claim"]
    name = (c.get("parties", {}).get("claimant") or {}).get("name", "Claimant")
    calc = state.get("calc") or {}
    d, amt = final["decision"], final["amount"]
    head = {"approve": "has been approved in full", "partial": "has been approved in part",
            "reject": "has not been approved"}[d]
    lines = [f"Dear {name},", "", f"Your claim {state['claim_id']} {head}."]
    reasons: list[str] = []
    if d != "reject":
        lines.append(f"Approved amount: INR {amt:,.2f} (claimed INR {calc.get('claimed_amount', amt):,.2f}).")
        refs = [f"{r['action'].replace('_', ' ')} {r['ref']}" for r in results
                if r.get("ref") and r["action"] in CLAIMANT_FACING]
        if refs:
            lines.append("Reference: " + ", ".join(refs) + ".")
        for ln in calc.get("lines", []):
            if ln["admitted"] < ln["claimed"]:
                reasons.append(f"{ln['item']}: INR {ln['claimed']:,.0f} claimed, INR {ln['admitted']:,.0f} "
                               f"allowed under policy clause {ln['rule_id']} ({ln['note']}).")
        for field, corr in c.get("corrections", {}).items():
            reasons.append(f"We corrected the {field} on your claim from {corr['previous']} to {corr['value']} "
                           f"based on the documents you supplied.")
    else:
        dup = state.get("duplicate") or {}
        elig = state.get("eligibility") or {}
        if dup.get("is_duplicate"):
            reasons.append("This invoice has already been reimbursed under an earlier claim (policy clause DLR-3.1).")
        for r in elig.get("results", []):
            if not r["passed"] and not r["waivable"]:
                reasons.append(f"{r['clause']}: {r['detail']}.")
    if final.get("approver_reason") and d != "approve":
        reasons.append(f"Reviewer note: {final['approver_reason']}")
    if reasons:
        lines += ["", "Details:"] + [f"  - {r}" for r in reasons]
    lines += ["", "If you disagree with this decision you may reply within 30 days with additional evidence.",
              "", "Claims Desk"]
    return f"Decision on claim {state['claim_id']}", "\n".join(lines)


def run(state: dict) -> dict:
    ctx = ToolContext(state, "settlement")
    cid = ctx.claim_id
    appr = state["approval"]
    final, tokens, aid = appr["final"], appr["tokens"], appr["approval_id"]
    rec = state["recommendation"]
    results = []
    letter_idx = None
    for i, act in enumerate(rec["actions"]):
        key = f"{cid}:{aid}:{i}:{act['type']}"
        tok = tokens.get(str(i))
        if tok is None:
            trace.emit(cid, "settlement", "action", f"skipped {act['type']}: not approved", action=act["type"],
                       status="not_approved")
            continue
        p, amt = act["params"], final["amount"]
        if act["type"] == "decision_letter":
            letter_idx = i
            continue
        if act["type"] == "payment":
            r = ctx.call("issue_payment", payee_id=p["payee_id"], amount=amt, approval_token=tok, idempotency_key=key)
        elif act["type"] == "credit_note":
            r = ctx.call("issue_credit_note", dealer_id=p["dealer_id"], amount=amt, approval_token=tok,
                         idempotency_key=key)
        elif act["type"] == "replacement_order":
            r = ctx.call("create_replacement_order", part_no=p["part_no"], qty=p["qty"], ship_to=p["ship_to"],
                         value=amt, approval_token=tok, idempotency_key=key)
        elif act["type"] == "carrier_recovery":
            r = ctx.call("file_carrier_claim", shipment_id=p["shipment_id"], carrier=p["carrier"], amount=amt,
                         approval_token=tok, idempotency_key=key)
        elif act["type"] == "capa_ticket":
            r = ctx.call("create_capa_ticket", title=p["title"], body=p["body"], kind=p["kind"], approval_token=tok,
                         idempotency_key=key)
        else:
            continue
        res = r.get("result", r)
        results.append({**res, "action": act["type"]})
        trace.emit(cid, "settlement", "action", f"{act['type']} -> {res.get('status')} {res.get('ref', '')}",
                   action=act["type"], status=res.get("status"), ref=res.get("ref"), error=res.get("error"))
    if letter_idx is not None:
        subject, body = _letter(state, final, results)

        def llm():
            msg = get_chat_model("writer").invoke(
                "Polish this decision letter for a claimant. Keep every amount, reference and reason exactly; do "
                "not add promises. Return only the letter body.\n\n" + body)
            return msg.content if isinstance(msg.content, str) else body
        body = with_fallback(ctx, llm, lambda: body)
        party = (state["claim"].get("parties", {}).get("claimant") or {})
        r = ctx.call("send_message", recipient=party.get("email") or rec["actions"][letter_idx]["params"]["recipient"],
                     subject=subject, body=body, kind="decision_letter", approval_token=tokens[str(letter_idx)],
                     idempotency_key=f"{cid}:{aid}:{letter_idx}:decision_letter")
        res = r.get("result", r)
        results.append({**res, "action": "decision_letter"})
        trace.emit(cid, "settlement", "action", f"decision_letter -> {res.get('status')} {res.get('ref', '')}",
                   action="decision_letter", status=res.get("status"), ref=res.get("ref"), error=res.get("error"))
    ok = all(r.get("status") in ("executed", "duplicate_suppressed") for r in results)
    outcome = {"approve": "settled", "partial": "partially_settled", "reject": "rejected"}[final["decision"]]
    dup = state.get("duplicate") or {}
    if final["decision"] == "reject" and dup.get("is_duplicate"):
        outcome = "rejected_duplicate"
    if ok:
        db.update_claim(cid, outcome=outcome, settled_amount=final["amount"])
    return {"actions": state.get("actions", []) + results, "evidence": ctx.evidence,
            "settlement_ok": ok, "outcome": outcome if ok else None}
