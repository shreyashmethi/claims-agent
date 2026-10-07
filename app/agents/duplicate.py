"""Duplicate and Pattern Agent: separates duplicate submissions from valid repeat claims and spots recurring
failures by unit, component batch and dealer, using the claims history as institutional memory."""
from __future__ import annotations

from datetime import date, timedelta

from app import trace
from app.agents.base import case_brief, merge_findings, run_react, with_fallback
from app.config import SIM_DATE, rule_params
from app.documents import to_number
from app.schemas import DuplicateAssessment, Finding
from app.tools.registry import ToolContext
from pydantic import BaseModel

PAID = {"settled", "partially_settled"}

SYSTEM = """You are the Duplicate and Pattern Agent on a manufacturer's claims desk.
Decide whether this claim duplicates an earlier submission (same invoice already reimbursed, same unit and
service event claimed twice), or is a valid repeat claim (a genuine new failure with its own service record and
returned part). Then look for patterns: the same component failing repeatedly on one unit, a supplier batch
failing across many units and dealers, or a claimant with prior duplicate submissions. Cite evidence IDs."""

TOOLS = ["search_claims", "similar_claims", "failure_stats", "service_history", "get_invoice", "parts_record"]


class DuplicateReport(BaseModel):
    assessment: DuplicateAssessment
    findings: list[Finding]


def _d(v) -> date:
    return date.fromisoformat(str(v)[:10])


def _offline(ctx: ToolContext, state: dict) -> DuplicateReport:
    claim = state["claim"]
    f = claim["fields"]
    p = rule_params()["patterns"]
    serial, part, inv, claimant = f.get("serial"), f.get("part_no"), f.get("invoice_no"), f.get("claimant_id")
    amount = to_number(f.get("claimed_amount"))
    findings, evs, patterns = [], [], []
    dup_of, repeat_claims, repeat_valid = None, [], None
    steps = [s for s, ok in (("invoice reuse", inv), ("unit repeat history", serial and part),
                             ("batch failure stats", part), ("claimant duplicate history", claimant),
                             ("narrative similarity", True)) if ok]
    trace.emit(ctx.claim_id, "duplicate_pattern", "plan", "; ".join(steps), steps=steps)

    if inv:
        r = ctx.call("search_claims", invoice_no=inv)
        evs.append(r["evidence_id"])
        paid = [h for h in r["result"] if h["outcome"] in PAID and h["claimant_id"] == claimant]
        if paid:
            h = paid[0]
            erp = ctx.call("get_invoice", invoice_no=inv)
            ev_ids = [r["evidence_id"], erp["evidence_id"]]
            same_amt = amount is not None and abs((h["amount"] or 0) - amount) < 1
            dup_of = h["claim_id"]
            if serial:
                sh = ctx.call("service_history", serial=serial)
                ev_ids.append(sh["evidence_id"])
            findings.append({
                "type": "duplicate_submission", "severity": "high", "evidence_ids": ev_ids,
                "description": f"Invoice {inv} was already reimbursed on {h['claim_id']} "
                               f"({h['submitted_on']}, {h['settled_amount'] or h['amount']:,.0f})"
                               + (", same amount" if same_amt else "") + "; resubmitted with a different date.",
                "needs": [], "resolved": True, "resolution": "Confirmed duplicate (DLR-3.1 one claim per invoice)."})

    if serial and part:
        r = ctx.call("search_claims", serial=serial, part_no=part)
        evs.append(r["evidence_id"])
        since = SIM_DATE - timedelta(days=p["recurring_window_days"])
        prior = [h for h in r["result"] if _d(h["submitted_on"]) >= since and h["type"] == "warranty"]
        if prior:
            job = f.get("job_code")
            sh = ctx.call("service_history", serial=serial, job_code=job)
            evs.append(sh["evidence_id"])
            records = sh["result"]
            repeat_claims = [h["claim_id"] for h in prior]
            # A valid repeat has its own service event with the failed part returned.
            matched = sum(1 for h in prior if any(abs((_d(x["date"]) - _d(h["submitted_on"])).days) <= 10
                                                  and x["failed_part_returned"] for x in records))
            current = [x for x in records if x["date"] not in {pr["submitted_on"] for pr in prior}]
            repeat_valid = matched == len(prior) and bool(current)
            n = len(prior) + 1
            if n >= p["recurring_unit_failures"]:
                patterns.append({"kind": "recurring_unit_failure", "count": n, "units": [serial],
                                 "description": f"{part} has failed {n} times on {serial} in the last "
                                                f"{p['recurring_window_days'] // 30} months"})
            findings.append({
                "type": "repeat_claim", "severity": "low" if repeat_valid else "high",
                "evidence_ids": [r["evidence_id"], sh["evidence_id"]],
                "description": f"{len(prior)} earlier claim(s) for {part} on {serial}: {', '.join(repeat_claims)}. "
                               + ("Each matches its own service record with the failed part returned: valid repeat."
                                  if repeat_valid else "Earlier claims do not map to distinct service events."),
                "needs": [] if repeat_valid else ["Service records for each repair"],
                "resolved": bool(repeat_valid), "resolution": "valid repeat failure" if repeat_valid else None})

    if part:
        batch = (next((x["fields"] for x in claim["docs"].values() if x["kind"] == "job_card"), {})
                 .get("failed_part_batch"))
        r = ctx.call("failure_stats", part_no=part, batch=batch)
        evs.append(r["evidence_id"])
        b = r["result"].get("batch")
        if b:
            others = [u for u in b["units"] if u != serial]
            dealers = sorted({b["unit_dealers"][u] for u in others})
            if len(others) >= p["batch_min_units"] and len(dealers) >= p["batch_min_dealers"]:
                patterns.append({"kind": "batch_failure", "count": len(others), "units": others,
                                 "dealers": dealers,
                                 "description": f"{part} batch {batch} failed on {len(others)} other units across "
                                                f"{len(dealers)} other dealers in 12 months"})
                findings.append({"type": "batch_failure_pattern", "severity": "medium",
                                 "evidence_ids": [r["evidence_id"]], "needs": [], "resolved": True,
                                 "description": patterns[-1]["description"],
                                 "resolution": "Not a reason to reject; recommend supplier corrective action."})

    if claimant:
        r = ctx.call("search_claims", claimant_id=claimant, outcome="rejected_duplicate")
        evs.append(r["evidence_id"])
        hist = [h["claim_id"] for h in r["result"]]
        if dup_of:
            hist = hist + [ctx.claim_id]
        if hist and not dup_of:
            patterns.append({"kind": "dealer_duplicate_history", "count": len(hist), "dealers": [claimant],
                             "description": f"{claimant} has {len(hist)} duplicate submission(s) on record: {', '.join(hist)}"})
            findings.append({"type": "claimant_duplicate_history", "severity": "medium",
                             "evidence_ids": [r["evidence_id"]], "needs": [], "resolved": True,
                             "description": patterns[-1]["description"],
                             "resolution": "Does not affect this claim's merits; recommend a submission audit."})

    r = ctx.call("similar_claims", k=3)
    evs.append(r["evidence_id"])
    near = [h for h in r["result"] if h["similarity"] >= 0.6 and h["claimant_id"] == claimant
            and h["claim_id"] != dup_of]
    if near and not dup_of:
        findings.append({"type": "possible_near_duplicate", "severity": "medium", "evidence_ids": [r["evidence_id"]],
                         "needs": [], "resolved": False,
                         "description": f"Narrative closely matches {', '.join(h['claim_id'] for h in near)}."})

    rationale = (f"duplicate of {dup_of}" if dup_of else
                 f"valid repeat of {', '.join(repeat_claims)}" if repeat_valid else "no duplicate found")
    if patterns:
        rationale += "; patterns: " + "; ".join(x["kind"].replace("_", " ") for x in patterns)
    return DuplicateReport(
        assessment=DuplicateAssessment(is_duplicate=bool(dup_of), duplicate_of=dup_of, repeat_claims=repeat_claims,
                                       repeat_valid=repeat_valid, patterns=patterns, rationale=rationale,
                                       evidence_ids=[e for e in evs if e]),
        findings=[Finding(**x) for x in findings])


def run(state: dict) -> dict:
    ctx = ToolContext(state, "duplicate_pattern")

    def llm():
        def validate(rep: DuplicateReport) -> list[str]:
            ids = ctx.evidence_ids()
            return [f"unknown evidence {e}" for e in rep.assessment.evidence_ids + [x for f in rep.findings
                                                                                   for x in f.evidence_ids]
                    if e not in ids]
        return run_react(ctx, "investigator", SYSTEM, "Assess this claim.\n" + case_brief(state), TOOLS,
                         DuplicateReport, validate=validate)

    rep = with_fallback(ctx, llm, lambda: _offline(ctx, state))
    for f in rep.findings:
        trace.emit(ctx.claim_id, "duplicate_pattern", "finding", f"[{f.severity}] {f.description}",
                   finding_type=f.type, evidence_ids=f.evidence_ids)
    trace.emit(ctx.claim_id, "duplicate_pattern", "reasoning", rep.assessment.rationale)
    return {"duplicate": rep.assessment.model_dump(),
            "findings": merge_findings(state.get("findings", []), [f.model_dump() for f in rep.findings]),
            "evidence": ctx.evidence}
