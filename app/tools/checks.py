"""Named, deterministic cross-checks the Investigation Agent can choose to run.

Each check compares the claim's documents against system-of-record data and returns pass / fail / na,
the values compared, and (on fail) a suggested finding with the evidence the claimant would need to supply.
The agent decides which checks to run and what to do with the results; the comparison itself is code.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

from app.config import rule_params
from app.documents import to_number
from app.rules.facts import build_facts, d

CHECKS = ["coverage", "serial_consistency", "amount_vs_records", "labour_vs_standard", "service_timeline",
          "pod_remark", "prior_repairs", "dispatch_match", "expense_receipts"]


def _res(check, status, detail, data=None, finding=None):
    return {"check": check, "status": status, "detail": detail, "data": data or {}, "finding": finding}


def _doc(f, kind):
    return (f["docs"].get(kind) or [{}])[0].get("fields", {})


def run_check(claim_id: str, check: str) -> dict:
    if check not in CHECKS:
        return _res(check, "na", f"unknown check; choose from {CHECKS}")
    f = build_facts(claim_id)
    return globals()[f"_{check}"](f)


def _coverage(f):
    if f["type"] != "warranty":
        return _res("coverage", "na", "warranty only")
    c, part = f.get("contract"), f.get("part")
    if not c:
        return _res("coverage", "fail", "no warranty contract", finding={
            "type": "no_contract", "severity": "high", "description": "No warranty contract for the serial."})
    fail_d, hrs = d(f["fields"].get("failure_date")), to_number(f["fields"].get("hours_reading"))
    in_period = bool(fail_d and d(c["start_date"]) <= fail_d <= d(c["end_date"]))
    in_hours = hrs is not None and hrs <= c["hours_cap"]
    covered = bool(part and part["component_group"] in c["covered"])
    data = {"period": f"{c['start_date']}..{c['end_date']}", "failure_date": str(fail_d), "hours": hrs,
            "hours_cap": c["hours_cap"], "component_group": part and part["component_group"],
            "covered_groups": c["covered"]}
    ok = in_period and in_hours and covered
    return _res("coverage", "pass" if ok else "fail",
                f"in period={in_period}, within hours={in_hours}, component covered={covered}", data,
                None if ok else {"type": "not_covered", "severity": "high",
                                 "description": "Failure falls outside warranty coverage."})


def _serial_consistency(f):
    sources: dict[str, str] = {}
    form = _doc(f, "claim_form")
    if form.get("serial"):
        sources["claim_form"] = form["serial"]
    for kind in ("job_card", "delivery_note"):
        if _doc(f, kind).get("serial"):
            sources[kind] = _doc(f, kind)["serial"]
    for ph in f["docs"].get("photo", []):
        if "serial" in (ph["fields"].get("photo_of") or "").lower() and ph["fields"].get("reading"):
            sources[f"photo:{ph['doc_id']}"] = ph["fields"]["reading"]
    if f.get("shipment"):
        sources["shipment_record"] = f["shipment"]["serial"]
    if f["type"] in ("warranty", "dealer_reimbursement"):
        eq = f.get("equipment")
        sources["equipment_registry"] = eq["serial"] if eq else "NOT_FOUND"
    if len(sources) < 2:
        return _res("serial_consistency", "na", "fewer than two serial sources", sources)
    values = set(sources.values())
    if len(values) == 1:
        return _res("serial_consistency", "pass", f"all {len(sources)} sources agree on {values.pop()}", sources)
    record = sources.get("shipment_record") or sources.get("equipment_registry")
    photo = next((v for k, v in sources.items() if k.startswith("photo:")), None)
    majority = Counter(v for k, v in sources.items() if k != "claim_form").most_common(1)[0][0]
    resolvable = photo is not None and photo == record
    data = {"sources": sources, "record_value": record, "majority": majority, "photo_reading": photo,
            "photo_confirms_record": resolvable}
    return _res("serial_consistency", "fail",
                f"serial differs across sources: {sources}", data,
                {"type": "serial_mismatch", "severity": "high",
                 "description": f"Claim form serial {sources.get('claim_form')} does not match "
                                f"record serial {record}.",
                 "needs": [] if resolvable else ["Photograph of the equipment serial plate"],
                 "resolution_available": {"field": "serial", "value": record} if resolvable else None})


def _amount_vs_records(f):
    claimed, inv = f["claimed_amount"], _doc(f, "invoice")
    inv_total = to_number(inv.get("total"))
    t = f["type"]
    if t == "transit_damage":
        disp_val = (f.get("dispatch") or {}).get("value")
        data = {"claimed": claimed, "invoice_total": inv_total, "dispatch_value": disp_val,
                "insured_value": (f.get("shipment") or {}).get("insured_value")}
        if disp_val and inv_total and inv_total > disp_val * 1.02:
            pct = round((inv_total / disp_val - 1) * 100, 1)
            return _res("amount_vs_records", "fail", f"invoice total exceeds dispatch value by {pct}%", data,
                        {"type": "amount_mismatch", "severity": "high",
                         "description": f"Claimed invoice {inv_total:,.0f} is {pct}% above the dispatch "
                                        f"invoice value {disp_val:,.0f} in the ERP.",
                         "needs": ["Original purchase invoice supporting the claimed value"],
                         "capped_by": "TRN-4.1"})
        return _res("amount_vs_records", "pass", "claimed value consistent with dispatch value", data)
    if t == "employee_reimbursement":
        total = sum(ln["amount"] or 0 for ln in f["lines"])
        ok = abs(total - claimed) < 1
        return _res("amount_vs_records", "pass" if ok else "fail", f"line total {total} vs claimed {claimed}",
                    {"line_total": total, "claimed": claimed},
                    None if ok else {"type": "amount_mismatch", "severity": "medium",
                                     "description": "Expense lines do not add up to the claimed amount."})
    if t == "parts_replacement":
        part, qty = f.get("part") or {}, to_number(f["fields"].get("qty")) or 0
        val = (part.get("price") or 0) * qty
        ok = abs(val - claimed) < 1
        return _res("amount_vs_records", "pass" if ok else "fail", f"catalog value {val} vs claimed {claimed}",
                    {"catalog_value": val, "claimed": claimed},
                    None if ok else {"type": "amount_mismatch", "severity": "low",
                                     "description": "Claimed value differs from catalog value.",
                                     "capped_by": "PRT-4.1"})
    data = {"claimed": claimed, "invoice_total": inv_total, "lines": f["lines"]}
    issues = []
    if inv_total is not None and abs(inv_total - claimed) > 1:
        issues.append(f"claimed {claimed} differs from invoice total {inv_total}")
    for ln in f["lines"]:
        if ln["code"].startswith("PART:"):
            from app.rules.engine import _part_price
            cat = _part_price(ln["code"].split(":", 1)[1])
            if cat and (ln["rate"] or 0) > cat * 1.001:
                issues.append(f"{ln['code']} billed at {ln['rate']} above catalog {cat}")
    if issues:
        return _res("amount_vs_records", "fail", "; ".join(issues), data,
                    {"type": "amount_mismatch", "severity": "medium", "description": "; ".join(issues),
                     "capped_by": "WAR-6.1" if t == "warranty" else "DLR-4.1"})
    return _res("amount_vs_records", "pass", "invoice, lines and claimed amount agree", data)


def _labour_vs_standard(f):
    if f["type"] not in ("warranty", "dealer_reimbursement"):
        return _res("labour_vs_standard", "na", "not a labour claim")
    p = rule_params()
    job = f["fields"].get("job_code")
    std = p["warranty"]["std_hours"].get(job) if f["type"] == "warranty" else p["dealer"]["field_service_std_hours"]
    hrs = sum(ln["qty"] or 0 for ln in f["lines"] if ln["code"] == "LABOUR")
    data = {"job_code": job, "claimed_hours": hrs, "standard_hours": std}
    if std is None:
        return _res("labour_vs_standard", "na", f"no standard for job code {job}", data)
    if hrs > std:
        return _res("labour_vs_standard", "fail", f"{hrs:g} h claimed vs {std} h standard", data,
                    {"type": "labour_over_standard", "severity": "medium",
                     "description": f"Labour {hrs:g} h exceeds the {std} h standard for {job}.",
                     "capped_by": "WAR-6.2" if f["type"] == "warranty" else "DLR-4.1"})
    return _res("labour_vs_standard", "pass", f"{hrs:g} h within {std} h standard", data)


def _service_timeline(f):
    from app.tools.services import service_history
    serial = f["fields"].get("serial")
    if not serial or f["type"] not in ("warranty", "dealer_reimbursement"):
        return _res("service_timeline", "na", "no serviced unit on this claim")
    hist = service_history(serial)
    jc = _doc(f, "job_card")
    work_date = d(jc.get("date") or f["fields"].get("service_date"))
    match = [h for h in hist if work_date and abs((d(h["date"]) - work_date).days) <= 2]
    data = {"work_date": str(work_date), "matching_records": match,
            "history": [{"date": h["date"], "job_code": h["job_code"]} for h in hist]}
    if not match:
        return _res("service_timeline", "fail", f"no service record near {work_date} for {serial}", data,
                    {"type": "service_record_missing", "severity": "medium",
                     "description": f"Dealer system has no service record for {serial} on or near {work_date}.",
                     "needs": ["Customer-signed service report for the visit"]})
    if f["type"] == "warranty":
        fail_d = d(f["fields"].get("failure_date"))
        sched = [h for h in hist if h["job_code"] == "SCHED-SVC" and fail_d and d(h["date"]) <= fail_d]
        last = max((d(h["date"]) for h in sched), default=None)
        data["last_scheduled_service"] = str(last)
        if not last or (fail_d - last) > timedelta(days=270):
            return _res("service_timeline", "fail", "scheduled maintenance overdue at failure", data,
                        {"type": "missed_maintenance", "severity": "high",
                         "description": "No scheduled service in the 9 months before failure (WAR-3.4).",
                         "needs": ["Proof of scheduled maintenance"]})
    return _res("service_timeline", "pass", f"service record found for {work_date}", data)


def _pod_remark(f):
    if f["type"] != "transit_damage":
        return _res("pod_remark", "na", "transit only")
    sh = f.get("shipment") or {}
    remark = (sh.get("pod_remark") or _doc(f, "delivery_note").get("pod_remark") or "").strip()
    acks = [x for x in f["docs"].get("carrier_ack", [])
            if str(x["fields"].get("acknowledges_damage", "")).lower() in {"yes", "true"}]
    data = {"pod_remark": remark or None, "carrier_ack_docs": [a["doc_id"] for a in acks]}
    if remark and remark not in {"-", "—", "none"}:
        return _res("pod_remark", "pass", f"POD remark: {remark}", data)
    return _res("pod_remark", "fail", "POD has no damage remark", data,
                {"type": "missing_pod_remark", "severity": "high",
                 "description": "Proof of delivery carries no damage remark (TRN-2.3).",
                 "needs": [] if acks else ["Carrier's written acknowledgement of the damage"],
                 "exception_basis": f"carrier acknowledgement {acks[0]['doc_id']}" if acks else None})


def _prior_repairs(f):
    from app.tools.services import service_history
    serial, job = f["fields"].get("serial"), f["fields"].get("job_code")
    if not serial or not job:
        return _res("prior_repairs", "na", "no job code")
    prior = [h for h in service_history(serial, job) if h["date"] < (_doc(f, "job_card").get("date") or "9999")]
    data = {"prior_same_job": prior}
    if prior:
        return _res("prior_repairs", "fail", f"{len(prior)} earlier {job} repair(s) on {serial}", data,
                    {"type": "repeat_repair", "severity": "low",
                     "description": f"{len(prior)} earlier {job} repair(s) on this unit; see duplicate/pattern "
                                    f"analysis."})
    return _res("prior_repairs", "pass", "no earlier repair of this kind", data)


def _dispatch_match(f):
    if f["type"] != "parts_replacement":
        return _res("dispatch_match", "na", "parts only")
    disp = f.get("dispatch") or {}
    labels = [p["fields"].get("reading") for p in f["docs"].get("photo", []) if p["fields"].get("reading")]
    data = {"ordered": disp.get("part_no"), "packed": disp.get("packed_part_no"),
            "received_claimed": f["fields"].get("received_part_no"), "photo_label": labels}
    issue = (f["fields"].get("issue") or "").lower()
    if issue == "wrong_part":
        ok = disp.get("packed_part_no") == f["fields"].get("received_part_no") != disp.get("part_no")
        ok = ok and (not labels or labels[0] == disp.get("packed_part_no"))
        return _res("dispatch_match", "pass" if ok else "fail",
                    "warehouse scan, dealer report and photo agree a wrong part was sent" if ok
                    else "wrong-part claim not corroborated", data,
                    None if ok else {"type": "wrong_part_unconfirmed", "severity": "high",
                                     "description": "Dispatch scan does not confirm a wrong part.",
                                     "needs": ["Photograph of the part label received"]})
    return _res("dispatch_match", "pass" if labels else "fail", "defect evidence present" if labels else
                "no defect photo", data)


def _expense_receipts(f):
    if f["type"] != "employee_reimbursement":
        return _res("expense_receipts", "na", "employee only")
    lim = rule_params()["employee"]["receipt_threshold"]
    receipts = {(r["fields"].get("for_line") or "").upper(): to_number(r["fields"].get("amount"))
                for r in f["docs"].get("receipt", [])}
    missing, mismatch = [], []
    for ln in f["lines"]:
        if ln["code"] in {"PER_DIEM", "MILEAGE_KM"} or (ln["amount"] or 0) < lim:
            continue
        if ln["code"] not in receipts:
            missing.append(ln["code"])
        elif abs((receipts[ln["code"]] or 0) - (ln["amount"] or 0)) > 1:
            mismatch.append(f"{ln['code']}: receipt {receipts[ln['code']]} vs claimed {ln['amount']}")
    data = {"receipts": receipts, "missing": missing, "mismatch": mismatch}
    if missing:
        return _res("expense_receipts", "fail", f"missing receipts: {missing}", data,
                    {"type": "missing_receipt", "severity": "high", "description": f"No receipt for {missing}.",
                     "needs": [f"Receipt for {m}" for m in missing]})
    if mismatch:
        return _res("expense_receipts", "fail", "; ".join(mismatch), data,
                    {"type": "receipt_mismatch", "severity": "medium", "description": "; ".join(mismatch),
                     "needs": ["Corrected receipts"]})
    return _res("expense_receipts", "pass", "receipts match all lines over threshold", data)
