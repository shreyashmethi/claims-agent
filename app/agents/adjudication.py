"""Adjudication Agent: weighs eligibility, findings and duplicate analysis into a recommendation with evidence.
The amount always comes from the rules engine; the Verifier rejects anything else."""
from __future__ import annotations

from app import trace
from app.agents.base import case_brief, run_react, with_fallback
from app.agents.verifier import verify
from app.schemas import ProposedAction, Reason, Recommendation
from app.tools.registry import ToolContext

SYSTEM = """You are the Adjudication Agent on a manufacturer's claims desk.
Recommend approve, partial (admissible amount below claimed), reject or escalate, using the eligibility result,
the investigation findings and the duplicate/pattern assessment. Call `rules_calculate_settlement` and use its
admissible_amount exactly as `amount` (0 for reject). Every reason must cite evidence IDs. Set exception=true when
eligibility.with_exception is true, and explain it. Escalate only if an unresolved high-severity finding prevents
a decision. Propose capa_ticket actions for recurring or batch failures and for claimants with duplicate history;
settlement and letter actions are added by policy."""

TOOLS = ["rules_calculate_settlement", "get_precedents", "search_policy"]


def build_actions(state: dict, decision: str, amount: float, capa: list[ProposedAction]) -> list[ProposedAction]:
    c = state["claim"]
    f, t = c["fields"], c["type"]
    claimant = f.get("claimant_id")
    acts: list[ProposedAction] = []
    if decision in ("approve", "partial") and amount > 0:
        if t == "warranty":
            acts.append(ProposedAction(type="credit_note", params={"dealer_id": claimant, "amount": amount}))
        elif t == "parts_replacement":
            from app.tools.services import parts_record
            disp = parts_record(dispatch_id=f.get("dispatch_id")).get("dispatch", {})
            acts.append(ProposedAction(type="replacement_order", params={
                "part_no": f.get("part_no"), "qty": int(float(f.get("qty") or disp.get("qty") or 1)),
                "ship_to": claimant, "value": amount}))
        else:
            acts.append(ProposedAction(type="payment", params={"payee_id": claimant, "amount": amount}))
        if t == "transit_damage":
            from app.tools.services import shipment_record
            sh = shipment_record(shipment_id=f.get("shipment_id"))
            acts.append(ProposedAction(type="carrier_recovery", params={
                "shipment_id": f.get("shipment_id"), "carrier": sh.get("carrier"), "amount": amount}))
    seen = set()
    for a in capa:
        k = a.params.get("kind")
        if k not in seen:
            seen.add(k)
            acts.append(a)
    acts.append(ProposedAction(type="decision_letter", params={"recipient": claimant}))
    return acts


def _capa_from_patterns(state: dict) -> list[ProposedAction]:
    dup = state.get("duplicate") or {}
    c = state["claim"]["fields"]
    out = []
    for p in dup.get("patterns", []):
        if p["kind"] in ("batch_failure", "recurring_unit_failure"):
            out.append(ProposedAction(type="capa_ticket", params={
                "kind": "supplier_quality", "title": f"Recurring failures of {c.get('part_no')}",
                "body": p["description"] + f". Raised from claim {state['claim_id']}."}))
        elif p["kind"] == "dealer_duplicate_history":
            out.append(ProposedAction(type="capa_ticket", params={
                "kind": "dealer_audit", "title": f"Submission audit for {p['dealers'][0]}",
                "body": p["description"] + f". Raised from claim {state['claim_id']}."}))
    return out


def _offline(ctx: ToolContext, state: dict) -> Recommendation:
    elig = state.get("eligibility") or {}
    dup = state.get("duplicate") or {}
    findings = state.get("findings", [])
    ftypes = sorted({f["type"] for f in findings})
    prec = ctx.call("get_precedents", claim_type=state["claim"]["type"], finding_types=ftypes)
    if prec["result"]:
        trace.emit(ctx.claim_id, "adjudication", "memory",
                   f"{len(prec['result'])} approver precedent(s) retrieved as context", evidence_id=prec["evidence_id"])
    capa = _capa_from_patterns(state)
    find_ev = lambda typ: next((f["evidence_ids"] for f in findings if f["type"] == typ), [])  # noqa: E731
    blocking_open = [f for f in findings if not f.get("resolved") and f["severity"] == "high"
                     and not f["type"].startswith("rule_failed")]
    if dup.get("is_duplicate"):
        reasons = [Reason(text=f"Duplicate of {dup['duplicate_of']}: the same invoice was already reimbursed "
                               f"(DLR-3.1).", evidence_ids=find_ev("duplicate_submission") or dup["evidence_ids"])]
        return Recommendation(decision="reject", amount=0, reasons=reasons, confidence=0.97,
                              actions=build_actions(state, "reject", 0, capa))
    if blocking_open:
        reasons = [Reason(text=f"Unresolved: {f['description']}", evidence_ids=f["evidence_ids"])
                   for f in blocking_open]
        return Recommendation(decision="escalate", amount=0, reasons=reasons, confidence=0.5)
    if not elig.get("eligible"):
        failed = [r for r in elig["results"] if not r["passed"] and not r["waivable"]]
        reasons = [Reason(text=f"{r['clause']}: {r['detail']}",
                          evidence_ids=[elig["evidence_id"], elig["clause_evidence"].get(r["rule_id"])])
                   for r in failed]
        for r in reasons:
            r.evidence_ids = [e for e in r.evidence_ids if e]
        return Recommendation(decision="reject", amount=0, reasons=reasons, confidence=0.93,
                              actions=build_actions(state, "reject", 0, capa))
    calc_r = ctx.call("rules_calculate_settlement")
    calc, cev = calc_r["result"], calc_r["evidence_id"]
    amount, claimed = calc["admissible_amount"], calc["claimed_amount"]
    decision = "approve" if abs(amount - claimed) < 0.01 else "partial"
    reasons = [Reason(text=f"Eligible under {', '.join(r['rule_id'] for r in elig['results'] if r['passed'])}.",
                      evidence_ids=[elig["evidence_id"]])]
    for ln in calc["lines"]:
        if ln["admitted"] < ln["claimed"]:
            reasons.append(Reason(text=f"{ln['item']}: {ln['claimed']:,.0f} claimed, {ln['admitted']:,.0f} admitted "
                                       f"({ln['rule_id']}: {ln['note']}).", evidence_ids=[cev]))
    for f in findings:
        if f.get("resolved") and f["severity"] in ("high", "medium") and f.get("resolution") \
                and not f["type"].startswith("rule_failed"):
            reasons.append(Reason(text=f"{f['description']} Resolution: {f['resolution']}",
                                  evidence_ids=f["evidence_ids"]))
    if dup.get("repeat_valid"):
        reasons.append(Reason(text=f"Repeat failure, not a duplicate: earlier claims {dup['repeat_claims']} each "
                                   f"have their own service event.", evidence_ids=dup["evidence_ids"][:3]))
    exc = bool(elig.get("with_exception"))
    exc_reason = None
    if exc:
        waived = [r for r in elig["results"] if not r["passed"] and r["waivable"]]
        exc_reason = "; ".join(f"{r['rule_id']} not met, waiver basis: {r['waiver_basis']}" for r in waived)
        reasons.append(Reason(text=f"Policy exception required: {exc_reason}.", evidence_ids=[elig["evidence_id"]]))
    medium_open = sum(1 for f in findings if not f.get("resolved") and f["severity"] == "medium")
    conf = round(max(0.5, 0.95 - 0.1 * medium_open - (0.1 if exc else 0)), 2)
    return Recommendation(decision=decision, amount=amount, reasons=reasons, exception=exc,
                          exception_reason=exc_reason, confidence=conf,
                          actions=build_actions(state, decision, amount, capa))


def run(state: dict) -> dict:
    ctx = ToolContext(state, "adjudication")
    errors = (state.get("verification") or {}).get("errors") if not (state.get("verification") or {}).get("passed", True) else None

    def llm():
        task = ("Recommend a disposition.\n" + case_brief(state, ("eligibility", "duplicate")) +
                (f"\nYour previous recommendation failed verification: {errors}" if errors else ""))

        def validate(rec: Recommendation) -> list[str]:
            calc_ev = [e for e in ctx.evidence if e["tool"] == "rules_calculate_settlement"]
            calc = calc_ev[-1]["data"] if calc_ev else None
            return verify(state, rec.model_dump(), calc, ctx.evidence_ids())
        rec = run_react(ctx, "reasoner", SYSTEM, task, TOOLS, Recommendation, validate=validate)
        capa = [a for a in rec.actions if a.type == "capa_ticket"] or _capa_from_patterns(state)
        rec.actions = build_actions(state, rec.decision, rec.amount, capa)
        return rec

    rec = with_fallback(ctx, llm, lambda: _offline(ctx, state))
    calc_ev = [e for e in ctx.evidence if e["tool"] == "rules_calculate_settlement"]
    trace.emit(ctx.claim_id, "adjudication", "recommendation",
               f"{rec.decision.upper()} {rec.amount:,.2f}" + (" (policy exception)" if rec.exception else "")
               + f", confidence {rec.confidence}", decision=rec.decision, amount=rec.amount,
               reasons=[r.model_dump() for r in rec.reasons], actions=[a.model_dump() for a in rec.actions])
    return {"recommendation": rec.model_dump(), "evidence": ctx.evidence,
            "calc": calc_ev[-1]["data"] if calc_ev else state.get("calc")}
