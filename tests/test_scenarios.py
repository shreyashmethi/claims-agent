"""End-to-end scenarios on the seeded data (offline planner)."""
from app import db, trace
from tests.conftest import drive


def _types(cid):
    return [e["type"] for e in trace.get_trace(cid)]


def test_s1_warranty_end_to_end(fresh):
    s = drive("CLM-1001")
    assert s["status"] == "CLOSED"
    assert s["approval"]["final"] == {"decision": "partial", "amount": 20600.0, "approver_reason": ""}
    assert s["approval"]["required_roles"] == ["claims_officer"]
    acts = {a["action"]: a for a in s["actions"]}
    assert acts["credit_note"]["amount"] == 20600.0 and "decision_letter" in acts
    assert db.get_claim("CLM-1001")["outcome"] == "partially_settled"
    for t in ("plan", "delegate", "tool_call", "rule_fired", "recommendation", "verification", "approval", "action"):
        assert t in _types("CLM-1001")


def test_s2_conflicts_info_request_and_exception(fresh):
    s = drive("CLM-2001")
    f = {x["type"]: x for x in s["findings"]}
    assert f["serial_mismatch"]["resolved"] and "GB-7718" in f["serial_mismatch"]["resolution"]
    assert f["missing_pod_remark"]["resolved"] and f["amount_mismatch"]["resolved"]
    assert f["suspicious_instruction_in_document"]["severity"] == "medium"
    assert len(s["info_requests"]) == 1 and s["info_requests"][0]["status"] == "answered"
    assert s["claim"]["corrections"]["serial"]["previous"] == "GB-7781"
    rec = s["recommendation"]
    assert rec["decision"] == "partial" and rec["amount"] == 303500.0 and rec["exception"]
    assert s["approval"]["required_roles"] == ["claims_manager", "finance_controller"]
    assert {a["action"] for a in s["actions"]} >= {"payment", "carrier_recovery", "decision_letter"}
    assert "guardrail" in _types("CLM-2001")


def test_s2_no_reply_escalates_after_two_rounds(fresh):
    s = drive("CLM-2001", reply=False)
    assert s["status"] == "ESCALATED" and len(s["info_requests"]) == 2
    assert all(r["status"] == "expired" for r in s["info_requests"])
    assert not db.q("SELECT * FROM actions_log WHERE claim_id='CLM-2001' AND action IN ('payment','carrier_recovery')")
    assert db.q("SELECT * FROM escalations WHERE claim_id='CLM-2001'")


def test_s3_duplicate_then_valid_repeat_with_patterns(fresh):
    a = drive("CLM-3001")
    assert a["duplicate"]["is_duplicate"] and a["duplicate"]["duplicate_of"] == "CLM-H-0457"
    assert a["approval"]["final"]["decision"] == "reject"
    assert "investigation" not in a["flags"]["done"], "confirmed duplicate should skip the full investigation"
    assert db.get_claim("CLM-3001")["outcome"] == "rejected_duplicate"
    b = drive("CLM-3002")
    assert b["duplicate"]["repeat_valid"] and not b["duplicate"]["is_duplicate"]
    kinds = {p["kind"] for p in b["duplicate"]["patterns"]}
    assert kinds == {"recurring_unit_failure", "batch_failure", "dealer_duplicate_history"}
    assert b["approval"]["final"] == {"decision": "approve", "amount": 9200.0, "approver_reason": ""}
    assert "quality_lead" in b["approval"]["required_roles"]
    assert sum(1 for x in b["actions"] if x["action"] == "capa_ticket") == 2


def test_quality_lead_can_decline_capa_without_blocking_payment(fresh):
    drive("CLM-3001")
    b = drive("CLM-3002", approvals={"quality_lead": {"decision": "rejected", "reason": "already tracked"}})
    acts = [x["action"] for x in b["actions"]]
    assert "credit_note" in acts and "capa_ticket" not in acts


def test_parts_and_employee(fresh):
    p = drive("CLM-4001")
    assert p["approval"]["final"]["amount"] == 12400.0
    assert [x for x in p["actions"] if x["action"] == "replacement_order"][0]["qty"] == 4
    e = drive("CLM-5001")
    assert e["approval"]["final"] == {"decision": "partial", "amount": 32700.0, "approver_reason": ""}


def test_approver_modify_is_executed_and_remembered(fresh):
    s = drive("CLM-1001", approvals={"claims_officer": {"decision": "modified", "amount": 19000.0,
                                                        "reason": "seal kit dealer-supplied"}})
    assert s["approval"]["final"]["amount"] == 19000.0
    assert [a for a in s["actions"] if a["action"] == "credit_note"][0]["amount"] == 19000.0
    assert db.q("SELECT * FROM overrides WHERE claim_id='CLM-1001'")[0]["new_amount"] == 19000.0


def test_approver_send_back_reruns_review(fresh):
    s = drive("CLM-1001", approvals={"claims_officer": [
        {"decision": "sent_back", "reason": "check hours against telematics"}, {"decision": "approved"}]})
    delegates = [e["next"] for e in trace.get_trace("CLM-1001") if e["type"] == "delegate"]
    assert delegates.count("investigation") == 2 and delegates.count("route_approval") == 2
    assert s["status"] == "CLOSED"


def test_approver_reject_overrides_recommendation(fresh):
    s = drive("CLM-1001", approvals={"claims_officer": {"decision": "rejected", "reason": "fraud suspicion"}})
    assert s["approval"]["final"]["decision"] == "reject"
    assert {a["action"] for a in s["actions"]} == {"decision_letter"}


def test_write_tools_refuse_without_token_and_are_idempotent(fresh):
    from app import approvals
    from app.tools.actions import ApprovalRequired, issue_payment
    import pytest
    with pytest.raises(ApprovalRequired):
        issue_payment("CLM-1001", "D-014", 100.0, approval_token="APR-X.forged", idempotency_key="k1")
    tok = approvals.sign("APR-X", "CLM-1001", "payment", 100.0)
    first = issue_payment("CLM-1001", "D-014", 100.0, tok, "k1")
    again = issue_payment("CLM-1001", "D-014", 100.0, tok, "k1")
    assert first["status"] == "executed" and again["status"] == "duplicate_suppressed"
    assert again["ref"] == first["ref"]


def test_agent_tool_permissions_enforced(fresh):
    import app.tools.catalog  # noqa: F401
    from app.tools.registry import ToolContext
    ctx = ToolContext({"claim_id": "CLM-1001", "evidence": []}, "investigation")
    assert "not permitted" in ctx.call("issue_payment", payee_id="x", amount=1)["error"]
    assert "guardrail" in _types("CLM-1001")


def test_lifecycle_rejects_illegal_transition(fresh):
    import pytest
    from app.graph.lifecycle import IllegalTransition, transition
    with pytest.raises(IllegalTransition):
        transition("CLM-1001", "RECEIVED", "SETTLED")


def test_approver_can_pay_on_a_rejection_recommendation(fresh):
    s = drive("CLM-3001", approvals={"claims_manager": {"decision": "modified", "amount": 5000.0,
                                                        "reason": "goodwill for the second visit"}})
    assert s["approval"]["final"] == {"decision": "partial", "amount": 5000.0,
                                      "approver_reason": "goodwill for the second visit"}
    pay = [a for a in s["actions"] if a["action"] == "payment"]
    assert pay and pay[0]["amount"] == 5000.0
