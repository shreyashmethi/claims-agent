"""Policy Agent: runs the deterministic eligibility rules, retrieves the clauses behind every failed rule and
explains the result. It cannot change a rule outcome; it can only identify waivable rules (policy exceptions)."""
from __future__ import annotations

from app import trace
from app.agents.base import merge_findings, with_fallback
from app.llm.provider import get_chat_model
from app.tools.registry import ToolContext


def run(state: dict) -> dict:
    ctx = ToolContext(state, "policy")
    cid = ctx.claim_id
    r = ctx.call("rules_evaluate")
    elig, ev = r["result"], r["evidence_id"]
    clause_ev = {}
    for rr in elig["results"]:
        trace.emit(cid, "policy", "rule_fired",
                   f"{rr['rule_id']} {'PASS' if rr['passed'] else ('EXCEPTION?' if rr['waivable'] else 'FAIL')}"
                   f" - {rr['detail']}", rule_id=rr["rule_id"], clause=rr["clause"], passed=rr["passed"],
                   waivable=rr["waivable"], evidence_id=ev)
        if not rr["passed"]:
            c = ctx.call("search_policy", query=rr["rule_id"], k=1)
            clause_ev[rr["rule_id"]] = c["evidence_id"]
    elig["evidence_id"] = ev
    elig["clause_evidence"] = clause_ev

    # Each failed rule is a finding; a rule that now passes resolves its earlier finding.
    new = []
    prior_types = {f["type"] for f in state.get("findings", [])}
    for rr in elig["results"]:
        ftype = f"rule_failed:{rr['rule_id']}"
        if not rr["passed"]:
            new.append({"type": ftype, "severity": "medium" if rr["waivable"] else "high",
                        "evidence_ids": [ev, clause_ev[rr["rule_id"]]],
                        "description": f"{rr['clause']}: {rr['detail']}" +
                                       (f" (waivable as exception: {rr['waiver_basis']})" if rr["waivable"] else ""),
                        "needs": [], "resolved": False})
        elif ftype in prior_types:
            new.append({"type": ftype, "severity": "info", "evidence_ids": [ev], "needs": [],
                        "description": f"{rr['clause']}: {rr['detail']}", "resolved": True,
                        "resolution": "rule passes on re-evaluation"})
    findings = merge_findings(state.get("findings", []), new)

    summary = (f"eligible={elig['eligible']} exception={elig['with_exception']} blocking={elig['blocking']}")

    def llm():
        model = get_chat_model("writer")
        msg = model.invoke("In two sentences, explain this eligibility result to a claims approver. Name rule IDs."
                           f"\n{elig['results']}")
        return msg.content if isinstance(msg.content, str) else summary

    elig["explanation"] = with_fallback(ctx, llm, lambda: summary)
    trace.emit(cid, "policy", "reasoning", elig["explanation"][:300], evidence_id=ev)
    return {"eligibility": elig, "findings": findings, "evidence": ctx.evidence}
