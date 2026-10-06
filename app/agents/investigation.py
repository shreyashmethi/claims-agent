"""Investigation Agent: plans its own investigation for the claim at hand, cross-checks documents against
systems of record, follows up on discrepancies, corrects clerical errors on evidence, and reports findings."""
from __future__ import annotations

from collections import deque

from pydantic import BaseModel, Field

from app import trace
from app.agents.base import case_brief, merge_findings, run_react, with_fallback
from app.agents.common import claim_snapshot, requested_items
from app.schemas import Finding
from app.tools.checks import CHECKS
from app.tools.registry import ToolContext

BASE_PLAN = {
    "warranty": ["coverage", "labour_vs_standard", "amount_vs_records", "serial_consistency", "service_timeline"],
    "transit_damage": ["serial_consistency", "pod_remark", "amount_vs_records"],
    "parts_replacement": ["dispatch_match", "amount_vs_records"],
    "dealer_reimbursement": ["serial_consistency", "service_timeline", "labour_vs_standard", "amount_vs_records"],
    "employee_reimbursement": ["expense_receipts", "amount_vs_records"],
}

TOOLS = ["run_check", "service_history", "get_invoice", "parts_record", "shipment_record", "lookup_equipment",
         "warranty_contract", "failure_stats", "search_claims", "search_policy", "list_claim_documents",
         "record_correction"]

SYSTEM = f"""You are the Investigation Agent on a manufacturer's claims desk.
Investigate the claim by cross-checking its documents against systems of record. Plan your own investigation:
choose which named checks to run with `run_check` (available: {CHECKS}) and which records to pull, and follow up
on anything that does not agree (e.g. a serial mismatch -> pull the shipment or registry record; an amount gap ->
pull the dispatch record). If a check fails but documentary evidence (such as a serial-plate photo agreeing with
the system record) proves a clerical error, call `record_correction` citing that evidence.
For each discrepancy submit a finding. Set `needs` to the documents the claimant must supply to resolve it.
Mark a finding resolved when evidence resolves it, when a policy cap (e.g. TRN-4.1, WAR-6.2) handles it, or when
a waivable rule has a documented exception basis (state that it needs approval). Do not decide the claim."""


class InvestigationReport(BaseModel):
    plan: list[str] = Field(description="The checks and lookups you chose, in order")
    findings: list[Finding]
    summary: str


def _offline(ctx: ToolContext, state: dict) -> InvestigationReport:
    claim = state["claim"]
    t = claim["type"]
    asked = requested_items(state)
    answered = {i for r in state.get("info_requests", []) if r["status"] in ("answered", "expired") for i in r["items"]}
    plan = list(BASE_PLAN.get(t, []))
    if t == "warranty" and claim["fields"].get("job_code"):
        plan.append("prior_repairs")
    trace.emit(ctx.claim_id, "investigation", "plan", f"checks for {t}: {', '.join(plan)}", steps=plan)
    queue, done, findings, steps = deque(plan), set(), [], []
    prior = {f["type"]: f for f in state.get("findings", [])}
    while queue:
        chk = queue.popleft()
        if chk in done:
            continue
        done.add(chk)
        steps.append(chk)
        r = ctx.call("run_check", check=chk)
        res, ev = r["result"], r["evidence_id"]
        f = res.get("finding")
        if res["status"] == "pass":
            # A check that now passes resolves the finding it raised earlier.
            for p in prior.values():
                if p.get("source_check") == chk and not p.get("resolved"):
                    findings.append({**p, "resolved": True, "resolution": f"re-check passed: {res['detail']}",
                                     "evidence_ids": p["evidence_ids"] + [ev], "needs": []})
            continue
        if res["status"] != "fail" or not f:
            continue
        f = {**f, "evidence_ids": [ev], "source_check": chk, "needs": list(f.get("needs", []))}
        f.pop("resolution_available", None)
        if chk == "serial_consistency":
            d = res["data"]
            fu = (ctx.call("shipment_record", shipment_id=claim["fields"]["shipment_id"])
                  if claim["fields"].get("shipment_id") else
                  ctx.call("lookup_equipment", serial=d.get("record_value") or claim["fields"].get("serial", "")))
            steps.append("follow-up: system record")
            f["evidence_ids"].append(fu["evidence_id"])
            if d.get("photo_confirms_record"):
                photo_ev = next((x["evidence_id"] for k, x in claim["docs"].items()
                                 if f"photo:{k}" in d["sources"]), ev)
                ctx.call("record_correction", field="serial", value=d["record_value"],
                         evidence_ids=[ev, photo_ev, fu["evidence_id"]],
                         reason="serial-plate photo and system record agree; claim form value is a clerical error")
                f.update(resolved=True, needs=[], resolution=f"Claim-form serial corrected to {d['record_value']} "
                         f"on photo {photo_ev} and system record {fu['evidence_id']}.")
                f["evidence_ids"].append(photo_ev)
        elif chk == "amount_vs_records" and f.get("capped_by"):
            if t == "transit_damage":
                disp = ctx.call("shipment_record", shipment_id=claim["fields"].get("shipment_id"))
                f["evidence_ids"].append(disp["evidence_id"])
            unmet = [n for n in f["needs"] if n in answered]
            if not f["needs"] or unmet:
                f.update(resolved=True, needs=[],
                         resolution=f"{'No supporting document received after request; ' if unmet else ''}"
                                    f"amount is limited by {f['capped_by']} in the settlement calculation.")
        elif chk == "labour_vs_standard":
            f.update(resolved=True, resolution=f"Hours above standard are not reimbursed; the calculator applies "
                                               f"{f.get('capped_by')}.")
        elif chk == "pod_remark" and f.get("exception_basis"):
            f.update(resolved=True, needs=[], resolution=f"{f['exception_basis']} received; acceptable only as a "
                     f"TRN-2.3 policy exception with Claims Manager approval.")
        elif chk == "coverage":
            # A confirmed fact, not uncertainty: the eligibility rules carry the consequence.
            f.update(resolved=True, resolution="Confirmed against the warranty contract; decided by WAR-2.x/3.1 rules.")
        elif chk == "prior_repairs":
            f.update(resolved=True, resolution="Referred to duplicate and pattern analysis.")
        f.pop("capped_by", None)
        f.pop("exception_basis", None)
        findings.append(f)
        trace.emit(ctx.claim_id, "investigation", "finding",
                   f"[{f['severity']}] {f['description']}" + (" (resolved)" if f.get("resolved") else
                                                              f" needs: {f['needs']}" if f["needs"] else ""),
                   finding_type=f["type"], evidence_ids=f["evidence_ids"])
    open_items = [n for x in findings if not x.get("resolved") for n in x.get("needs", [])]
    summary = (f"{len(steps)} checks; {sum(1 for x in findings if not x.get('resolved'))} open finding(s)"
               + (f"; still needed: {open_items}" if open_items else "") + (f"; already requested: {sorted(asked)}"
                                                                          if asked else ""))
    return InvestigationReport(plan=[s for s in steps if s], findings=[Finding(**x) for x in findings],
                               summary=summary)


def run(state: dict) -> dict:
    ctx = ToolContext(state, "investigation")
    question = state.get("flags", {}).get("approver_question")

    def llm():
        task = ("Investigate this claim.\n" + case_brief(state, ("eligibility",)) +
                f"\nInformation already requested from claimant: {sorted(requested_items(state))}" +
                (f"\nThe approver sent the case back with this question: {question}" if question else ""))

        def validate(rep: InvestigationReport) -> list[str]:
            ids = ctx.evidence_ids()
            return [f"finding '{f.type}' cites unknown evidence {e}" for f in rep.findings
                    for e in f.evidence_ids if e not in ids]
        return run_react(ctx, "investigator", SYSTEM, task, TOOLS, InvestigationReport, validate=validate)

    report = with_fallback(ctx, llm, lambda: _offline(ctx, state))
    new = [f.model_dump() for f in report.findings]
    findings = merge_findings(state.get("findings", []), new)
    trace.emit(ctx.claim_id, "investigation", "reasoning", report.summary, plan=report.plan)
    return {"findings": findings, "evidence": ctx.evidence, "claim": claim_snapshot(ctx.claim_id),
            "investigation": {"plan": report.plan, "summary": report.summary}}
