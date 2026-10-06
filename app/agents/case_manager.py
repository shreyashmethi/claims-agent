"""Case Manager (supervisor): decides the next step after every agent turn.

Two layers:
  * Hard guards (code) force the step whenever the process demands it: new documents must be extracted,
    a recommendation must be verified, a verified recommendation must go to approval, approved actions must be
    executed, an exhausted budget must escalate. The model cannot skip or reorder these.
  * In the investigative phase the supervisor chooses among the permitted options (which specialist next,
    whether to ask the claimant, whether to escalate). The LLM decides here; the offline planner applies the
    same reasoning heuristically.
"""
from __future__ import annotations

import json

from app import trace
from app.agents.base import structured, with_fallback
from app.agents.common import facts_version, open_needs, requested_items
from app.config import MAX_AGENT_TURNS, MAX_INFO_ROUNDS, MAX_VERIFY_RETRIES
from app.schemas import NextAction
from app.tools.registry import ToolContext
from app.tools.services import list_claim_documents

GOALS = {
    "intake": "extract new documents, classify and link the claim",
    "completeness": "check required documents and fields",
    "policy": "evaluate eligibility rules and find applicable clauses",
    "investigation": "cross-check the claim against records and resolve discrepancies",
    "duplicate_pattern": "check for duplicate submissions and recurring failures",
    "request_info": "ask the claimant for the evidence still needed",
    "adjudication": "recommend a disposition with an admissible amount and evidence",
    "verifier": "verify the recommendation",
    "route_approval": "route the verified recommendation to the approvers the matrix requires",
    "settlement": "execute approved actions and send the decision letter",
    "escalate": "hand the case to a claims manager with the open issues",
    "end": "close the case",
}

SYSTEM = """You are the Case Manager supervising a team of claims agents for a manufacturing company.
Choose the single best next step for this claim from the allowed options only. Adapt to the evidence: skip work
that cannot change the outcome (e.g. a confirmed duplicate does not need a full investigation), ask the claimant
only for evidence that resolves an open finding, and escalate when the evidence cannot support a decision.
Explain your rationale in one or two sentences and list the steps you expect to follow."""


def _decide(state: dict) -> tuple[str, str, list[str], bool]:
    """Returns (choice, rationale, allowed options, forced)."""
    fl, status = state["flags"], state["status"]
    v, rev, done = facts_version(state), fl["rev"], fl["done"]

    def forced(choice: str, why: str):
        return choice, why, [choice], True

    if fl["turns"] >= MAX_AGENT_TURNS:
        return forced("escalate", f"agent turn budget ({MAX_AGENT_TURNS}) exhausted")
    if status in ("SETTLED", "CLOSED", "ESCALATED"):
        return forced("end", f"case is {status}")
    appr = state.get("approval")
    if appr and appr.get("status") == "complete":
        if state.get("settlement_ok") is None:
            return forced("settlement", f"approvals complete ({appr['final']['decision']}); execute approved actions")
        if state.get("settlement_ok") is False:
            return forced("escalate", "one or more approved actions failed to execute")
        return forced("end", "settlement executed")
    if any(not d["processed"] for d in list_claim_documents(state["claim_id"])):
        return forced("intake", "unprocessed documents on the claim")
    if done.get("completeness") != rev:
        return forced("completeness", "documents changed since the last completeness check")
    rec, ver = state.get("recommendation"), state.get("verification")
    if rec and fl.get("rec_version") == v:
        if ver is None:
            return forced("verifier", "every recommendation is verified before approval routing")
        if ver["passed"]:
            if rec["decision"] == "escalate":
                return forced("escalate", "adjudication could not reach a decision on the evidence")
            return forced("route_approval", "verified recommendation; route per approval matrix")
        if fl["verify_failures"] > MAX_VERIFY_RETRIES:
            return forced("escalate", f"recommendation failed verification {fl['verify_failures']} times")
        return forced("adjudication", f"verification failed: {ver['errors']}")

    claim = state["claim"]
    needs, asked = open_needs(state), requested_items(state)
    rounds = len(state.get("info_requests", []))
    can_ask = rounds < MAX_INFO_ROUNDS
    dup = state.get("duplicate") or {}
    comp = state.get("completeness") or {}
    dup_current = done.get("duplicate_pattern") == v
    core_done = done.get("policy") == v and done.get("investigation") == v

    allowed = ["policy", "investigation", "duplicate_pattern", "escalate"]
    if needs and can_ask:
        allowed.append("request_info")
    if (core_done and not needs and dup_current) or (dup.get("is_duplicate") and dup_current):
        allowed.append("adjudication")

    if (claim.get("type_confidence") or 0) < 0.7:
        choice, why = "escalate", f"claim type uncertain (confidence {claim.get('type_confidence')})"
    elif not comp.get("complete", True):
        unasked = [n for n in needs if n not in asked]
        choice, why = (("request_info", f"required documents missing: {unasked or needs}") if can_ask else
                       ("escalate", f"documents still missing after {rounds} request(s)"))
    elif claim.get("signals", {}).get("prior_invoice_hits") and not dup_current:
        choice, why = "duplicate_pattern", (f"invoice already appears on {claim['signals']['prior_invoice_hits']}; "
                                            "check for a duplicate before a full review")
    elif dup.get("is_duplicate") and dup_current:
        choice, why = "adjudication", (f"confirmed duplicate of {dup['duplicate_of']}; further investigation "
                                       "cannot change the outcome")
    elif done.get("policy") != v:
        choice, why = "policy", "eligibility not yet evaluated on current facts"
    elif done.get("investigation") != v:
        choice, why = "investigation", "claim not yet cross-checked against records on current facts"
    elif needs:
        choice, why = (("request_info", f"open findings need claimant evidence: {needs}") if can_ask else
                       ("escalate", f"evidence still missing after {rounds} request(s): {needs}"))
    elif not dup_current:
        choice, why = "duplicate_pattern", "duplicate and recurring-failure check not yet run on current facts"
    else:
        choice, why = "adjudication", "eligibility, investigation and duplicate checks complete"
    if choice not in allowed:
        allowed.append(choice)
    return choice, why, allowed, False


def _remaining(state: dict, choice: str) -> list[str]:
    v, done = facts_version(state), state["flags"]["done"]
    seq = [s for s in ("policy", "investigation", "duplicate_pattern") if done.get(s) != v and s != choice]
    return [choice] + seq + [s for s in ("adjudication", "verifier", "route_approval", "settlement") if s != choice]


def _brief(state: dict) -> str:
    fl = state["flags"]
    v = facts_version(state)
    return json.dumps({
        "claim_id": state["claim_id"], "status": state["status"], "type": state["claim"].get("type"),
        "type_confidence": state["claim"].get("type_confidence"),
        "stale_or_not_run": [a for a in ("policy", "investigation", "duplicate_pattern") if fl["done"].get(a) != v],
        "completeness": {k: (state.get("completeness") or {}).get(k) for k in ("complete", "missing")},
        "eligibility": {k: (state.get("eligibility") or {}).get(k) for k in ("eligible", "with_exception", "blocking")},
        "duplicate": {k: (state.get("duplicate") or {}).get(k) for k in ("is_duplicate", "duplicate_of", "rationale")},
        "open_findings": [{"type": f["type"], "severity": f["severity"], "needs": f.get("needs")}
                          for f in state.get("findings", []) if not f.get("resolved")],
        "info_requests": [{"round": r["round"], "items": r["items"], "status": r["status"]}
                          for r in state.get("info_requests", [])],
        "signals": state["claim"].get("signals"), "turns_used": fl["turns"],
    }, default=str, indent=1)


def run(state: dict) -> dict:
    """Returns the next node and the updated flags; the graph turns this into a Command."""
    choice, why, allowed, is_forced = _decide(state)
    remaining = _remaining(state, choice)
    if not is_forced:
        ctx = ToolContext(state, "case_manager")

        def llm():
            na = structured("supervisor", SYSTEM, f"Case:\n{_brief(state)}\n\nAllowed next steps: {allowed}\n"
                            f"Default planner suggestion: {choice} ({why})", NextAction)
            if na.agent not in allowed:
                trace.emit(state["claim_id"], "case_manager", "guardrail",
                           f"supervisor chose '{na.agent}', not permitted now; using '{choice}'", allowed=allowed)
                return choice, why, remaining
            return na.agent, na.rationale, na.remaining_plan or remaining

        choice, why, remaining = with_fallback(ctx, llm, lambda: (choice, why, remaining))
    trace.emit(state["claim_id"], "case_manager", "delegate", f"-> {choice}: {why}", next=choice,
               goal=GOALS.get(choice), rationale=why, forced=is_forced, allowed=allowed, plan=remaining)
    flags = {**state["flags"], "turns": state["flags"]["turns"] + 1}
    return {"next": choice, "plan": remaining, "flags": flags}
