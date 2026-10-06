"""Completeness Agent: required documents per claim type, mandatory fields, and the information request draft."""
from __future__ import annotations

from app import trace
from app.agents.base import merge_findings, with_fallback
from app.agents.common import claim_snapshot
from app.llm.provider import get_chat_model
from app.tools.registry import ToolContext


def _draft(claimant: str, claim_id: str, items: list[str]) -> str:
    bullets = "\n".join(f"  - {i}" for i in items)
    return (f"Dear {claimant},\n\nThank you for submitting claim {claim_id}. To complete our assessment we need "
            f"the following:\n{bullets}\n\nPlease reply with these documents within 7 days. If we do not receive "
            f"them, the claim will be referred to a claims manager for review.\n\nClaims Desk")


def draft_request(ctx: ToolContext, state: dict, items: list[str]) -> str:
    claimant = (state["claim"].get("parties", {}).get("claimant") or {}).get("name", "Claimant")
    base = _draft(claimant, ctx.claim_id, items)

    def llm():
        model = get_chat_model("writer")
        msg = model.invoke(
            "Rewrite this information request so it is clear and courteous. Keep every requested item, the claim "
            "ID and the 7-day deadline. Do not promise any outcome. Return only the message.\n\n" + base)
        return msg.content if isinstance(msg.content, str) else base

    return with_fallback(ctx, llm, lambda: base)


def run(state: dict) -> dict:
    ctx = ToolContext(state, "completeness")
    claim = state["claim"]
    req = ctx.call("required_docs")
    ev = req["evidence_id"]
    docs = list(claim["docs"].values())
    present = {d["kind"] for d in docs}
    receipt_lines = {(d["fields"].get("for_line") or "").upper() for d in docs if d["kind"] == "receipt"}
    missing = []
    for r in req["result"]["required"]:
        if r["kind"] == "receipt":
            if r["for_line"].upper() not in receipt_lines:
                missing.append(r["label"])
        elif r["kind"] not in present:
            missing.append(r["label"])
    weak = []
    for d in docs:
        q = ctx.call("check_doc_quality", doc_id=d["doc_id"])["result"]
        if not q.get("ok", True):
            weak.append(f"{d['filename']}: missing {', '.join(q['missing_fields'])}")
    items = missing + [f"Complete document {w}" for w in weak]
    finding = {"type": "missing_documents", "severity": "high" if missing else "medium", "evidence_ids": [ev],
               "description": "Required documents or fields missing: " + "; ".join(items) if items
               else "All required documents present.", "needs": items, "resolved": not items,
               "resolution": None if items else "complete"}
    prior = [f for f in state.get("findings", []) if f["type"] == "missing_documents"]
    findings = state.get("findings", [])
    if items or prior:
        findings = merge_findings(findings, [finding])
    result = {"required": req["result"]["required"], "present": sorted(present), "missing": missing,
              "weak": weak, "complete": not items}
    if items:
        result["request_draft"] = draft_request(ctx, state, items)
    trace.emit(ctx.claim_id, "completeness", "finding" if items else "reasoning",
               f"{'incomplete: ' + '; '.join(items) if items else 'all required documents present'}",
               evidence_id=ev)
    return {"completeness": result, "findings": findings, "evidence": ctx.evidence,
            "claim": claim_snapshot(ctx.claim_id)}
