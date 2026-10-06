"""Deterministic rules engine: eligibility, admissible amount, approval routing.

LLM agents never compute money. Each rule returns the clause it implements, so the trace can show exactly
why a claim is eligible and why an amount is what it is.
"""
from __future__ import annotations

from typing import Callable

from app.config import rule_params
from app.documents import to_number
from app.rules.facts import build_facts, d
from app.schemas import CalcLine, EligibilityResult, RuleResult, SettlementCalc

RULES: dict[str, list[tuple[str, str, Callable]]] = {}


def rule(claim_type: str, rule_id: str, clause: str):
    def deco(fn):
        RULES.setdefault(claim_type, []).append((rule_id, clause, fn))
        return fn
    return deco


def _yes(v) -> bool:
    return str(v or "").strip().lower() in {"yes", "y", "true", "1"}


# ---------------------------------------------------------------- warranty
@rule("warranty", "WAR-2.1", "Warranty policy §2.1 Coverage period")
def war_period(f, p):
    c, fail = f.get("contract"), d(f["fields"].get("failure_date"))
    if not c:
        return False, "no warranty contract for this serial", False
    ok = fail is not None and d(c["start_date"]) <= fail <= d(c["end_date"])
    return ok, f"failure {fail} vs coverage {c['start_date']}..{c['end_date']}", False


@rule("warranty", "WAR-2.2", "Warranty policy §2.2 Operating-hours cap")
def war_hours(f, p):
    c = f.get("contract")
    hrs = to_number(f["fields"].get("hours_reading"))
    if not c or hrs is None:
        return False, "hours reading or contract missing", False
    return hrs <= c["hours_cap"], f"{hrs:.0f} h vs cap {c['hours_cap']} h", False


@rule("warranty", "WAR-3.1", "Warranty policy §3.1 Covered components")
def war_component(f, p):
    c, part = f.get("contract"), f.get("part")
    if not c or not part:
        return False, "contract or part not found", False
    ok = part["component_group"] in c["covered"]
    return ok, f"{part['part_no']} group '{part['component_group']}' covered={ok}", False


@rule("warranty", "WAR-2.4", "Warranty policy §2.4 Reporting window")
def war_report(f, p):
    fail = d(f["fields"].get("failure_date"))
    if not fail:
        return False, "failure date missing", False
    days = (f["submitted_on"] - fail).days
    lim = p["warranty"]["report_within_days_of_failure"]
    return days <= lim, f"reported {days} d after failure (limit {lim})", False


@rule("warranty", "WAR-5.2", "Warranty policy §5.2 Failed part returned")
def war_return(f, p):
    jc = (f["docs"].get("job_card") or [{}])[0].get("fields", {})
    ok = _yes(jc.get("failed_part_returned"))
    return ok, "job card confirms failed part returned" if ok else "failed part not returned", False


# ---------------------------------------------------------------- parts replacement
@rule("parts_replacement", "PRT-2.1", "Parts policy §2.1 Return window")
def prt_window(f, p):
    disp = f.get("dispatch")
    if not disp:
        return False, "dispatch record not found", False
    days = (f["submitted_on"] - d(disp["dispatch_date"])).days
    lim = p["parts"]["return_window_days"]
    return days <= lim, f"claimed {days} d after dispatch (limit {lim})", False


@rule("parts_replacement", "PRT-3.1", "Parts policy §3.1 Defect or wrong part confirmed")
def prt_confirmed(f, p):
    disp, fld = f.get("dispatch"), f["fields"]
    issue = (fld.get("issue") or "").lower()
    if not disp:
        return False, "dispatch record not found", False
    if issue == "wrong_part":
        ok = disp["packed_part_no"] != disp["part_no"] and fld.get("received_part_no") == disp["packed_part_no"]
        return ok, f"ordered {disp['part_no']}, warehouse packed {disp['packed_part_no']}, " \
                   f"dealer received {fld.get('received_part_no')}", False
    ok = bool(f["docs"].get("photo"))
    return ok, "defect photo on file" if ok else "no defect evidence", False


# ---------------------------------------------------------------- transit damage
@rule("transit_damage", "TRN-2.1", "Transit policy §2.1 Notification window")
def trn_window(f, p):
    sh = f.get("shipment")
    if not sh:
        return False, "shipment not found", False
    days = (f["submitted_on"] - d(sh["delivered_on"])).days
    lim = p["transit"]["notify_days"]
    return days <= lim, f"reported {days} d after delivery (limit {lim})", False


@rule("transit_damage", "TRN-2.2", "Transit policy §2.2 Claim matches shipment")
def trn_match(f, p):
    sh = f.get("shipment")
    if not sh:
        return False, "shipment not found", False
    ok = sh["serial"] == f["fields"].get("serial")
    return ok, f"claim serial {f['fields'].get('serial')} vs shipped serial {sh['serial']}", False


@rule("transit_damage", "TRN-2.3", "Transit policy §2.3 Damage noted on proof of delivery")
def trn_pod(f, p):
    sh = f.get("shipment")
    remark = (sh or {}).get("pod_remark") or ""
    dn = (f["docs"].get("delivery_note") or [{}])[0].get("fields", {})
    remark = remark or dn.get("pod_remark", "")
    if remark.strip() not in {"", "-", "—", "none"}:
        return True, f"POD remark: {remark}", False
    return False, "POD carries no damage remark", True  # waivable only with a waiver basis, see evaluate()


# ---------------------------------------------------------------- dealer reimbursement
@rule("dealer_reimbursement", "DLR-2.1", "Dealer policy §2.1 Authorised service")
def dlr_auth(f, p):
    dealer, svc = f.get("dealer"), (f["fields"].get("service_type") or "").lower()
    if not dealer:
        return False, "dealer not found", False
    ok = svc in dealer["authorised_services"]
    return ok, f"service '{svc}' authorised for {dealer['dealer_id']}={ok}", False


@rule("dealer_reimbursement", "DLR-2.2", "Dealer policy §2.2 Pre-authorisation")
def dlr_preauth(f, p):
    lim = p["dealer"]["pre_auth_threshold"]
    if f["claimed_amount"] <= lim:
        return True, f"claim {f['claimed_amount']:.0f} under pre-auth threshold {lim}", False
    ok = bool(f["fields"].get("pre_auth_no")) or bool(f["docs"].get("pre_auth"))
    return ok, "pre-authorisation on file" if ok else "pre-authorisation missing", False


@rule("dealer_reimbursement", "DLR-2.3", "Dealer policy §2.3 Submission window")
def dlr_window(f, p):
    sd = d(f["fields"].get("service_date"))
    if not sd:
        return False, "service date missing", False
    days = (f["submitted_on"] - sd).days
    lim = p["dealer"]["submit_within_days"]
    return days <= lim, f"submitted {days} d after service (limit {lim})", False


# ---------------------------------------------------------------- employee reimbursement
@rule("employee_reimbursement", "EMP-2.1", "Travel policy §2.1 Submission window")
def emp_window(f, p):
    end = d(f["fields"].get("trip_end"))
    if not end:
        return False, "trip end date missing", False
    days = (f["submitted_on"] - end).days
    lim = p["employee"]["submit_within_days"]
    return days <= lim, f"submitted {days} d after trip (limit {lim})", False


@rule("employee_reimbursement", "EMP-2.2", "Travel policy §2.2 Receipts")
def emp_receipts(f, p):
    lim = p["employee"]["receipt_threshold"]
    covered = {(r["fields"].get("for_line") or "").upper() for r in f["docs"].get("receipt", [])}
    missing = [ln["code"] for ln in f["lines"]
               if ln["code"] not in {"PER_DIEM", "MILEAGE_KM"} and (ln["amount"] or 0) >= lim
               and ln["code"].upper() not in covered]
    return not missing, "receipts present for all lines over threshold" if not missing \
        else f"missing receipts for {', '.join(missing)}", False


@rule("employee_reimbursement", "EMP-2.3", "Travel policy §2.3 Business purpose")
def emp_purpose(f, p):
    ok = bool((f["fields"].get("purpose") or "").strip())
    return ok, "business purpose stated" if ok else "purpose missing", False


# ---------------------------------------------------------------- evaluation
def evaluate(claim_id: str) -> EligibilityResult:
    f, p = build_facts(claim_id), rule_params()
    results = []
    for rule_id, clause, fn in RULES.get(f["type"], []):
        passed, detail, waivable = fn(f, p)
        basis = None
        if not passed and waivable:
            if rule_id == "TRN-2.3":
                acks = [x for x in f["docs"].get("carrier_ack", []) if _yes(x["fields"].get("acknowledges_damage"))]
                basis = f"carrier acknowledgement {acks[0]['doc_id']}" if acks else None
            waivable = basis is not None
        results.append(RuleResult(rule_id=rule_id, clause=clause, passed=passed, detail=detail,
                                  waivable=waivable and not passed, waiver_basis=basis))
    failed = [r for r in results if not r.passed]
    blocking = [r.rule_id for r in failed if not r.waivable]
    return EligibilityResult(eligible=not blocking, with_exception=bool(failed) and not blocking,
                             results=results, blocking=blocking)


def calculate_settlement(claim_id: str) -> SettlementCalc:
    f, p = build_facts(claim_id), rule_params()
    t = f["type"]
    lines: list[CalcLine] = []
    mode = "payment"
    if t in ("warranty", "dealer_reimbursement"):
        tier = (f.get("dealer") or {}).get("tier", "B")
        rate = p["warranty"]["labour_rate_by_tier"][tier]
        job = f["fields"].get("job_code")
        std = p["warranty"]["std_hours"].get(job) if t == "warranty" else p["dealer"]["field_service_std_hours"]
        rid = "WAR" if t == "warranty" else "DLR"
        for ln in f["lines"]:
            code, qty, amt = ln["code"], ln["qty"] or 0, ln["amount"] or 0
            if code.startswith("PART:"):
                cat = _part_price(code.split(":", 1)[1])
                adm = min(amt, (cat or ln["rate"] or 0) * qty)
                lines.append(CalcLine(item=ln["description"], claimed=amt, admitted=adm, rule_id=f"{rid}-6.1",
                                      note=f"catalog price {cat}"))
            elif code == "LABOUR":
                hrs = min(qty, std) if std else qty
                adm = min(amt, hrs * rate)
                lines.append(CalcLine(item=ln["description"], claimed=amt, admitted=adm, rule_id=f"{rid}-6.2",
                                      note=f"{hrs:g} h allowed (std {std}) x tier {tier} rate {rate}"))
            elif code == "TRAVEL_KM":
                km = min(qty, p["dealer"]["max_travel_km"])
                adm = min(amt, km * p["dealer"]["travel_rate_per_km"])
                lines.append(CalcLine(item=ln["description"], claimed=amt, admitted=adm, rule_id="DLR-4.3",
                                      note=f"{km:g} km x {p['dealer']['travel_rate_per_km']}"))
            else:
                lines.append(CalcLine(item=ln["description"], claimed=amt, admitted=0, rule_id=f"{rid}-6.9",
                                      note="non-reimbursable line"))
        mode = "credit_note" if t == "warranty" else "payment"
    elif t == "parts_replacement":
        disp, part = f.get("dispatch") or {}, f.get("part") or {}
        qty = to_number(f["fields"].get("qty")) or disp.get("qty", 0)
        val = (part.get("price") or 0) * qty
        lines.append(CalcLine(item=f"Replace {qty:g} x {part.get('part_no')}", claimed=f["claimed_amount"],
                              admitted=min(val, f["claimed_amount"] or val), rule_id="PRT-4.1",
                              note="replacement at catalog value; no cash settlement"))
        mode = "replacement"
    elif t == "transit_damage":
        disp, sh = f.get("dispatch") or {}, f.get("shipment") or {}
        cap = min(x for x in [f["claimed_amount"], disp.get("value") or 1e18, sh.get("insured_value") or 1e18])
        lines.append(CalcLine(item="Transit damage compensation", claimed=f["claimed_amount"], admitted=cap,
                              rule_id="TRN-4.1", note=f"lower of claimed {f['claimed_amount']:.0f}, dispatch "
                              f"value {disp.get('value')}, insured value {sh.get('insured_value')}"))
    elif t == "employee_reimbursement":
        emp = f.get("employee") or {}
        g, tier = emp.get("grade", "G1"), (f["fields"].get("city_tier") or "tier2").lower()
        pe = p["employee"]
        for ln in f["lines"]:
            code, qty, amt = ln["code"], ln["qty"] or 1, ln["amount"] or 0
            if code == "HOTEL_NIGHT":
                adm, rid, note = min(amt, pe["hotel_per_night"][g][tier] * qty), "EMP-4.2", \
                    f"cap {pe['hotel_per_night'][g][tier]}/night ({g}, {tier})"
            elif code == "PER_DIEM":
                adm, rid, note = min(amt, pe["per_diem"][g][tier] * qty), "EMP-4.3", \
                    f"per diem {pe['per_diem'][g][tier]}/day ({g}, {tier})"
            elif code == "MILEAGE_KM":
                adm, rid, note = min(amt, pe["mileage_rate"] * qty), "EMP-4.4", f"{pe['mileage_rate']}/km"
            else:
                adm, rid, note = amt, "EMP-4.1", "actual cost with receipt"
            lines.append(CalcLine(item=ln["description"], claimed=amt, admitted=adm, rule_id=rid, note=note))
    total = round(sum(ln.admitted for ln in lines), 2)
    claimed = f["claimed_amount"] or round(sum(ln.claimed for ln in lines), 2)
    return SettlementCalc(claimed_amount=claimed, admissible_amount=total, lines=lines, settlement_mode=mode,
                          rule_ids=sorted({ln.rule_id for ln in lines}), params_version=p["version"])


def _part_price(part_no: str) -> float | None:
    from app import db
    row = db.one("SELECT price FROM parts WHERE part_no=?", (part_no,))
    return row and row["price"]


def required_approvals(claim_type: str, decision: str, amount: float, exception: bool, confidence: float,
                       action_types: list[str]) -> list[str]:
    """Approval authority matrix. Deterministic; the agents cannot change who approves."""
    a = rule_params()["approval"]
    roles: list[str] = []
    if claim_type == "employee_reimbursement":
        roles.append("reporting_manager")
        if amount > a["officer_limit"]:
            roles.append("finance_controller")
    elif decision == "reject":
        roles.append("claims_manager")
    elif amount <= a["officer_limit"] and not exception and confidence >= a["officer_min_confidence"]:
        roles.append("claims_officer")
    elif amount <= a["manager_limit"]:
        roles.append("claims_manager")
    else:
        roles += ["claims_manager", "finance_controller"]
    if exception and "claims_manager" not in roles:
        roles.insert(0, "claims_manager")
    if "capa_ticket" in action_types:
        roles.append("quality_lead")
    return roles
