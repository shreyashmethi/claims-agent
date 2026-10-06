"""LangGraph wiring: one supervisor, specialist nodes that always return to it, and two interrupt nodes
(claimant reply, human approval) that park the case durably until a person acts."""
from __future__ import annotations

from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app import approvals as tokens
from app import db, trace
from app.agents import adjudication, case_manager, completeness, duplicate, intake, investigation, policy, settlement, verifier
from app.agents.common import claim_snapshot, facts_version, open_needs
from app.agents.completeness import draft_request
from app.graph.lifecycle import path, transition
from app.graph.state import CaseState
from app.rules.engine import required_approvals
from app.schemas import ApprovalDecision, InfoRequest
from app.tools.registry import ToolContext

FINANCIAL = {"payment", "credit_note", "replacement_order", "carrier_recovery"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------------------ supervisor
def case_manager_node(state: CaseState) -> Command:
    upd = case_manager.run(state)
    nxt = upd["next"]
    return Command(goto=END if nxt == "end" else nxt, update=upd)


# ------------------------------------------------------------------------------ specialists
def _specialist(name: str, fn):
    def node(state: CaseState) -> dict:
        cid, status = state["claim_id"], state["status"]
        if name != "intake" and status in ("CLASSIFIED", "RECOMMENDED"):
            status = transition(cid, status, "UNDER_REVIEW", f"{name} reviewing")
        upd = fn({**state, "status": status})
        merged = {**state, **upd}
        merged["claim"] = claim_snapshot(cid)
        flags = {**state["flags"], "done": {**state["flags"]["done"]}}
        flags["done"][name] = flags["rev"] if name == "completeness" else facts_version(merged)
        out = {**upd, "claim": merged["claim"], "flags": flags, "status": status}
        if name == "intake" and status == "RECEIVED" and merged["claim"].get("type"):
            out["status"] = transition(cid, status, "CLASSIFIED", merged["claim"]["type"])
        if name == "adjudication":
            flags["rec_version"] = facts_version(merged)
            out["verification"] = None
            out["status"] = transition(cid, out["status"], "RECOMMENDED", upd["recommendation"]["decision"])
        return out
    node.__name__ = f"{name}_node"
    return node


def verifier_node(state: CaseState) -> dict:
    upd = verifier.run(state)
    v = upd["verification"]
    trace.emit(state["claim_id"], "verifier", "verification",
               "passed" if v["passed"] else f"failed: {v['errors']}", passed=v["passed"], errors=v["errors"])
    flags = dict(state["flags"])
    status = state["status"]
    if not v["passed"]:
        flags["verify_failures"] += 1
        status = transition(state["claim_id"], status, "UNDER_REVIEW", "verification failed")
    return {**upd, "flags": flags, "status": status}


# ------------------------------------------------------------------------------ claimant loop
def request_info_node(state: CaseState) -> dict:
    cid = state["claim_id"]
    items = open_needs(state)
    rnd = len(state.get("info_requests", [])) + 1
    ctx = ToolContext(state, "system")
    comp = state.get("completeness") or {}
    msg = comp.get("request_draft") if comp.get("missing") and set(comp["missing"]) >= set(items) else \
        draft_request(ctx, state, items)
    party = state["claim"].get("parties", {}).get("claimant") or {}
    ctx.call("request_information", recipient=party.get("email") or party.get("party_id", "claimant"),
             items=items, message=msg, round=rnd)
    req = InfoRequest(id=f"IR-{cid}-{rnd}", round=rnd, items=items, message=msg).model_dump()
    trace.emit(cid, "system", "info_request", f"round {rnd}: requested {items}", request_id=req["id"], items=items)
    status = transition(cid, state["status"], "PENDING_INFO", f"awaiting claimant (round {rnd})")
    return {"info_requests": state.get("info_requests", []) + [req], "status": status}


def await_claimant_node(state: CaseState) -> dict:
    req = state["info_requests"][-1]
    reply = interrupt({"type": "info_request", "claim_id": state["claim_id"], "request": req})
    cid = state["claim_id"]
    reqs = [dict(r) for r in state["info_requests"]]
    flags = dict(state["flags"])
    if reply and reply.get("doc_ids"):
        reqs[-1]["status"] = "answered"
        flags["rev"] += 1
        trace.emit(cid, "claimant", "info_response", f"{len(reply['doc_ids'])} document(s) received"
                   + (f": {reply.get('note')}" if reply.get("note") else ""), doc_ids=reply["doc_ids"])
    else:
        reqs[-1]["status"] = "expired"
        trace.emit(cid, "claimant", "info_response", "no response by the deadline")
    status = transition(cid, state["status"], "UNDER_REVIEW", "claimant round closed")
    return {"info_requests": reqs, "flags": flags, "status": status}


# ------------------------------------------------------------------------------ approvals (HITL)
def route_approval_node(state: CaseState) -> dict:
    cid, rec = state["claim_id"], state["recommendation"]
    roles = required_approvals(state["claim"]["type"], rec["decision"], rec["amount"], rec["exception"],
                               rec["confidence"], [a["type"] for a in rec["actions"]])
    n = (db.one("SELECT COUNT(*) AS n FROM approvals WHERE claim_id=?", (cid,)) or {"n": 0})["n"] + 1
    aid = f"APR-{cid}-{n}"
    db.ex("INSERT INTO approvals VALUES (?,?,?,?,?,?,?)",
          (aid, cid, db.jdump(roles), "[]", "pending", db.jdump({"recommendation": rec, "calc": state.get("calc")}),
           _now()))
    trace.emit(cid, "system", "approval_request", f"{aid}: {' -> '.join(roles)}", approval_id=aid, roles=roles,
               decision=rec["decision"], amount=rec["amount"], exception=rec["exception"])
    status = transition(cid, state["status"], "PENDING_APPROVAL", f"awaiting {roles[0]}")
    return {"approval": {"approval_id": aid, "required_roles": roles, "records": [], "status": "pending"},
            "status": status}


def await_approval_node(state: CaseState) -> Command:
    cid, appr = state["claim_id"], dict(state["approval"])
    decided = {r["role"] for r in appr["records"]}
    role = next(r for r in appr["required_roles"] if r not in decided)
    rec = state["recommendation"]
    raw = interrupt({"type": "approval", "claim_id": cid, "approval_id": appr["approval_id"], "role": role,
                     "recommendation": rec, "calc": state.get("calc"),
                     "findings": [f for f in state.get("findings", []) if f["severity"] != "info"],
                     "prior_decisions": appr["records"]})
    d = ApprovalDecision.model_validate({"role": role, **(raw or {})})
    if d.role != role:
        d.role = role  # approvals are taken strictly in matrix order
    appr["records"] = appr["records"] + [d.model_dump()]
    trace.emit(cid, d.approver, "approval", f"{role}: {d.decision}" + (f" {d.amount:,.2f}" if d.amount is not None else "")
               + (f" - {d.reason}" if d.reason else ""), role=role, decision=d.decision, amount=d.amount)
    db.ex("UPDATE approvals SET records=? WHERE approval_id=?", (db.jdump(appr["records"]), appr["approval_id"]))

    if d.decision in ("modified", "rejected") and role != "quality_lead":
        db.ex("INSERT INTO overrides(claim_id, claim_type, role, original_decision, original_amount, new_decision, "
              "new_amount, reason, finding_types, ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (cid, state["claim"]["type"], role, rec["decision"], rec["amount"], d.decision,
               d.amount if d.decision == "modified" else 0, d.reason,
               db.jdump(sorted({f["type"] for f in state.get("findings", [])})), _now()))
        trace.emit(cid, "system", "memory", "approver override stored as a precedent for similar cases")

    if d.decision == "sent_back":
        flags = {**state["flags"], "sent_back": state["flags"]["sent_back"] + 1, "approver_question": d.reason}
        db.ex("UPDATE approvals SET status='sent_back' WHERE approval_id=?", (appr["approval_id"],))
        status = transition(cid, state["status"], "UNDER_REVIEW", f"sent back by {role}")
        return Command(goto="case_manager", update={"approval": None, "recommendation": None,
                                                     "verification": None, "flags": flags, "status": status})
    remaining = [r for r in appr["required_roles"] if r not in {x["role"] for x in appr["records"]}]
    if d.decision == "rejected" and role != "quality_lead":
        remaining = [r for r in remaining if r == "quality_lead"]  # claim decided; CAPA still reviewed
    if remaining:
        return Command(goto="await_approval", update={"approval": appr})
    return Command(goto="case_manager", update=_finalize(state, appr))


def _finalize(state: CaseState, appr: dict) -> dict:
    cid, rec = state["claim_id"], state["recommendation"]
    claimed = (state.get("calc") or {}).get("claimed_amount") or rec["amount"]
    decision, amount, note, capa_ok = rec["decision"], rec["amount"], "", True
    for r in appr["records"]:
        if r["role"] == "quality_lead":
            capa_ok = r["decision"] in ("approved", "modified")
            continue
        if r["decision"] == "modified" and r["amount"] is not None:
            amount, note = r["amount"], r["reason"]
            decision = "reject" if amount <= 0 else ("approve" if amount >= claimed - 0.01 else "partial")
        elif r["decision"] == "rejected":
            decision, amount, note = "reject", 0.0, r["reason"]
    signed = {}
    for i, a in enumerate(rec["actions"]):
        if a["type"] == "capa_ticket" and not capa_ok:
            continue
        if a["type"] in FINANCIAL and decision == "reject":
            continue
        signed[str(i)] = tokens.sign(appr["approval_id"], cid, a["type"], amount if a["type"] in FINANCIAL else 0)
    appr = {**appr, "status": "complete", "tokens": signed,
            "final": {"decision": decision, "amount": amount, "approver_reason": note}}
    db.ex("UPDATE approvals SET status='complete' WHERE approval_id=?", (appr["approval_id"],))
    new_status = {"approve": "APPROVED", "partial": "PARTIALLY_APPROVED", "reject": "REJECTED"}[decision]
    status = transition(cid, state["status"], new_status, f"final {decision} {amount:,.2f}")
    trace.emit(cid, "system", "approval", f"approval complete: {decision} {amount:,.2f}; "
               f"{len(signed)} action(s) authorised", final=appr["final"], authorised=sorted(signed))
    return {"approval": appr, "status": status}


# ------------------------------------------------------------------------------ execution and exits
def settlement_node(state: CaseState) -> dict:
    cid, status = state["claim_id"], state["status"]
    if status in ("APPROVED", "PARTIALLY_APPROVED"):
        status = transition(cid, status, "SETTLING")
    upd = settlement.run({**state, "status": status})
    if upd["settlement_ok"]:
        status = path(cid, status, "SETTLED", "CLOSED") if status == "SETTLING" else transition(cid, status, "CLOSED")
    return {**upd, "status": status}


def escalate_node(state: CaseState) -> dict:
    cid = state["claim_id"]
    open_items = [f for f in state.get("findings", []) if not f.get("resolved")]
    last = trace.get_trace(cid)[-1]
    reason = last.get("rationale") or last.get("summary") or "escalated"
    db.ex("INSERT INTO escalations(claim_id, reason, open_items, ts) VALUES (?,?,?,?)",
          (cid, reason, db.jdump(open_items), _now()))
    db.update_claim(cid, outcome="escalated")
    trace.emit(cid, "case_manager", "escalation", f"escalated to claims manager: {reason}",
               open_findings=[{"type": f["type"], "needs": f.get("needs")} for f in open_items])
    status = transition(cid, state["status"], "ESCALATED", reason[:80])
    return {"status": status, "escalation": {"reason": reason, "open_findings": open_items}, "outcome": "escalated"}


def build(checkpointer=None):
    g = StateGraph(CaseState)
    g.add_node("case_manager", case_manager_node,
               destinations=("intake", "completeness", "policy", "investigation", "duplicate_pattern",
                             "adjudication", "verifier", "request_info", "route_approval", "settlement",
                             "escalate", END))
    for name, mod in (("intake", intake), ("completeness", completeness), ("policy", policy),
                      ("investigation", investigation), ("duplicate_pattern", duplicate),
                      ("adjudication", adjudication)):
        g.add_node(name, _specialist(name, mod.run))
        g.add_edge(name, "case_manager")
    g.add_node("verifier", verifier_node)
    g.add_edge("verifier", "case_manager")
    g.add_node("request_info", request_info_node)
    g.add_edge("request_info", "await_claimant")
    g.add_node("await_claimant", await_claimant_node)
    g.add_edge("await_claimant", "case_manager")
    g.add_node("route_approval", route_approval_node)
    g.add_edge("route_approval", "await_approval")
    g.add_node("await_approval", await_approval_node, destinations=("await_approval", "case_manager"))
    g.add_node("settlement", settlement_node)
    g.add_edge("settlement", "case_manager")
    g.add_node("escalate", escalate_node)
    g.add_edge("escalate", END)
    g.add_edge(START, "case_manager")
    return g.compile(checkpointer=checkpointer)
