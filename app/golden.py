"""Golden evaluation set: templated claims across all five types with known correct outcomes.

`seed_golden` adds 24 claims (12 templates x 2) on top of the base seed. `run_eval` processes the 6 scenario
claims plus the golden claims with an auto-approver and no claimant replies (beyond scripted ones), then scores
decision accuracy, amount exactness, expected findings, escalations, steps and tool calls.
"""
from __future__ import annotations

import random
import time
from datetime import date, timedelta

from app import db
from app.config import rule_params
from app.seed import add_unit, ins, iso, new_claim

EXPECTED: dict[str, dict] = {
    "CLM-1001": {"decision": "partial", "amount": 20600.0, "findings": ["labour_over_standard"]},
    "CLM-2001": {"decision": "partial", "amount": 303500.0, "exception": True,
                 "findings": ["serial_mismatch", "missing_pod_remark", "amount_mismatch",
                              "suspicious_instruction_in_document"]},
    "CLM-3001": {"decision": "reject", "amount": 0.0, "findings": ["duplicate_submission"]},
    "CLM-3002": {"decision": "approve", "amount": 9200.0, "findings": ["repeat_claim", "batch_failure_pattern"],
                 "capa": 2},
    "CLM-4001": {"decision": "approve", "amount": 12400.0, "findings": []},
    "CLM-5001": {"decision": "partial", "amount": 32700.0, "findings": []},
}


def _rate(dealer: str) -> int:
    tier = db.one("SELECT tier FROM dealers WHERE dealer_id=?", (dealer,))["tier"]
    return rule_params()["warranty"]["labour_rate_by_tier"][tier]


def _price(part: str) -> float:
    return db.one("SELECT price FROM parts WHERE part_no=?", (part,))["price"]


def _warranty(cid, rng, part, job, hours, labour_h, sale=None, hours_reading=None):
    dealer = rng.choice(["D-008", "D-014", "D-019", "D-050", "D-063"])
    serial = f"EX-{cid[-3:]}G"
    sale = sale or date(2025, rng.randint(1, 9), rng.randint(1, 28))
    add_unit(serial, dealer, "C-355", sale, rng)
    fail = date(2026, 9, rng.randint(15, 25))
    work = fail + timedelta(days=1)
    hrs = hours_reading or rng.randint(900, 2500)
    ins("service_history", (f"SR-{serial}-R", serial, iso(work), dealer, job, "repair", db.jdump([part]), hrs, 1, None))
    rate = _rate(dealer)
    price = _price(part)
    claimed = price + labour_h * rate
    inv = f"{dealer.replace('-', '')}-INV-G{cid[-3:]}"
    new_claim(cid, dealer, iso(work + timedelta(days=rng.randint(2, 6))), [
        ("claim_form.pdf", "Warranty Claim Form", "claim_form", {
            "Claim Type": "warranty", "Claimant ID": dealer, "Serial": serial, "Part No": part,
            "Failure Date": iso(fail), "Hours Reading": str(hrs), "Job Code": job, "Invoice No": inv,
            "Claimed Amount": f"{claimed:.2f}", "Description": f"{part} failure on {serial}"}, None, ""),
        ("invoice.pdf", "Dealer Invoice", "invoice", {"Invoice No": inv, "Date": iso(work), "Total": f"{claimed:.2f}"},
         [(part, f"PART:{part}", 1, price, price), ("Labour", "LABOUR", labour_h, rate, labour_h * rate)], ""),
        ("job_card.pdf", "Dealer Job Card", "job_card", {
            "Serial": serial, "Date": iso(work), "Job Code": job, "Hours Reading": str(hrs),
            "Labour Hours": str(labour_h), "Failed Part Returned": "yes"}, None, ""),
    ])
    std = rule_params()["warranty"]["std_hours"][job]
    return claimed, price + min(labour_h, std) * rate


def _transit(cid, rng, delivered, submitted, part, qty, remark):
    disp, ship = f"DSP-G{cid[-3:]}", f"SHP-G{cid[-3:]}"
    value = _price(part) * qty
    ins("dispatches", (disp, part, part, None, qty, "C-355", "GB-X", iso(delivered - timedelta(days=3)), f"SI-G{cid[-3:]}",
                       value))
    ins("shipments", (ship, disp, "Bharat Parcel Lines", iso(delivered - timedelta(days=3)), iso(delivered), remark,
                      value, None))
    new_claim(cid, "C-355", iso(submitted), [
        ("claim_form.pdf", "Transit Damage Claim Form", "claim_form", {
            "Claim Type": "transit damage", "Claimant ID": "C-355", "Shipment ID": ship, "Dispatch ID": disp,
            "Delivered On": iso(delivered), "Invoice No": f"SI-G{cid[-3:]}", "Claimed Amount": f"{value:.2f}",
            "Damage Description": f"{qty} x {part} crushed in transit"}, None, ""),
        ("invoice.pdf", "Invoice", "invoice", {"Invoice No": f"SI-G{cid[-3:]}", "Date": iso(delivered),
                                               "Total": f"{value:.2f}"},
         [(part, f"PART:{part}", qty, _price(part), value)], ""),
        ("delivery_note.pdf", "Delivery Note", "delivery_note", {"Shipment ID": ship, "Delivered On": iso(delivered),
                                                                 "POD Remark": remark or "-"}, None, ""),
        ("photo.pdf", "Photograph", "photo", {"Photo Of": "crushed crate"}, None, ""),
    ])
    return value


def _parts(cid, rng, dispatch_date, submitted, part, qty):
    disp = f"DSP-P{cid[-3:]}"
    value = _price(part) * qty
    ins("dispatches", (disp, part, part, None, qty, "D-063", "SNS-X", iso(dispatch_date), f"SI-P{cid[-3:]}", value))
    new_claim(cid, "D-063", iso(submitted), [
        ("claim_form.pdf", "Parts Replacement Claim", "claim_form", {
            "Claim Type": "parts replacement", "Claimant ID": "D-063", "Part No": part, "Dispatch ID": disp,
            "Issue": "defective", "Qty": str(qty), "Claimed Amount": f"{value:.2f}",
            "Description": f"{qty} x {part} defective on arrival"}, None, ""),
        ("delivery_note.pdf", "Delivery Note", "delivery_note", {"Dispatch ID": disp,
                                                                 "Delivered On": iso(dispatch_date + timedelta(days=3))},
         None, ""),
        ("photo.pdf", "Photograph", "photo", {"Photo Of": "defect", "Reading": part}, None, ""),
    ])
    return value


def _dealer(cid, rng, duplicate: bool):
    dealer = rng.choice(["D-008", "D-050"])
    serial = f"LD-{cid[-3:]}G"
    add_unit(serial, dealer, "C-341", date(2025, 6, 1), rng)
    svc = date(2026, 9, rng.randint(10, 25))
    ins("service_history", (f"SR-{serial}-F", serial, iso(svc), dealer, "FIELD-SVC", "field visit",
                            db.jdump(["FL-100"]), 800, 0, None))
    rate = _rate(dealer)
    claimed = 1200 + 3 * rate + 60 * 12
    inv = f"DLR-INV-G{cid[-3:]}"
    if duplicate:
        db.ex("INSERT INTO claims(claim_id, type, claimant_id, serial, invoice_no, amount, submitted_on, status, "
              "outcome, settled_amount, narrative, data, is_historical) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)",
              (f"CLM-H-G{cid[-3:]}", "dealer_reimbursement", dealer, serial, inv, claimed, "2026-08-01", "CLOSED",
               "settled", claimed, f"Field visit {serial}", "{}"))
    new_claim(cid, dealer, iso(svc + timedelta(days=5)), [
        ("claim_form.pdf", "Dealer Reimbursement Claim", "claim_form", {
            "Claim Type": "dealer reimbursement", "Claimant ID": dealer, "Serial": serial,
            "Service Type": "field_service", "Service Date": iso(svc), "Invoice No": inv, "Travel Km": "60",
            "Claimed Amount": f"{claimed:.2f}", "Description": f"Field visit {serial}"}, None, ""),
        ("invoice.pdf", "Dealer Invoice", "invoice", {"Invoice No": inv, "Date": iso(svc), "Total": f"{claimed:.2f}"},
         [("Filter kit", "PART:FL-100", 1, 1200, 1200), ("Labour", "LABOUR", 3, rate, 3 * rate),
          ("Travel", "TRAVEL_KM", 60, 12, 720)], ""),
        ("report.pdf", "Field Service Report", "job_card", {"Serial": serial, "Date": iso(svc), "Job Code": "FIELD-SVC",
                                                            "Labour Hours": "3"}, None, ""),
    ])
    return claimed


def _employee(cid, rng, with_receipt: bool):
    emp = "E-1077"  # G1
    p = rule_params()["employee"]
    hotel, pd = p["hotel_per_night"]["G1"]["tier2"], p["per_diem"]["G1"]["tier2"]
    lines = [("Train fare", "RAIL", 1, 2100, 2100), ("Hotel", "HOTEL_NIGHT", 1, hotel, hotel),
             ("Per diem", "PER_DIEM", 2, pd, 2 * pd)]
    total = 2100 + hotel + 2 * pd
    docs = [("expense_report.pdf", "Travel Expense Report", "claim_form", {
        "Claim Type": "employee reimbursement", "Claimant ID": emp, "Employee ID": emp, "City Tier": "tier2",
        "Trip Start": "2026-09-20", "Trip End": "2026-09-21", "Claimed Amount": f"{total:.2f}",
        "Purpose": "Dealer training at Nagpur"}, lines, ""),
        ("receipt_hotel.pdf", "Receipt", "receipt", {"For Line": "HOTEL_NIGHT", "Amount": str(hotel)}, None, "")]
    if with_receipt:
        docs.append(("receipt_rail.pdf", "Receipt", "receipt", {"For Line": "RAIL", "Amount": "2100"}, None, ""))
    new_claim(cid, emp, "2026-09-28", docs)
    return total


def seed_golden(rng: random.Random) -> list[str]:
    ids = []
    for k in range(2):
        b = 100 + k * 20
        c = lambda i: f"CLM-G{b + i:03d}"  # noqa: E731
        claimed, adm = _warranty(c(1), rng, "ST-110", "ENG-STARTER-R", 0, 3)
        EXPECTED[c(1)] = {"decision": "approve", "amount": adm, "findings": []}
        claimed, adm = _warranty(c(2), rng, "AL-310", "ELEC-ALT-R", 0, 5)
        EXPECTED[c(2)] = {"decision": "partial", "amount": adm, "findings": ["labour_over_standard"]}
        _warranty(c(3), rng, "TR-505", "TRK-ROLLER-R", 0, 5, sale=date(2024, 7, 1))
        EXPECTED[c(3)] = {"decision": "reject", "amount": 0.0, "findings": ["not_covered"]}
        _warranty(c(4), rng, "HP-450", "HYD-PUMP-R", 0, 6, hours_reading=3400)
        EXPECTED[c(4)] = {"decision": "reject", "amount": 0.0, "findings": ["not_covered"]}
        _transit(c(5), rng, date(2026, 9, 10), date(2026, 9, 25), "TR-505", 4, "two cartons crushed")
        EXPECTED[c(5)] = {"decision": "reject", "amount": 0.0, "findings": []}
        v = _transit(c(6), rng, date(2026, 9, 30), date(2026, 10, 2), "AL-310", 5, "pallet broken, units dented")
        EXPECTED[c(6)] = {"decision": "approve", "amount": v, "findings": []}
        v = _parts(c(7), rng, date(2026, 9, 18), date(2026, 9, 30), "ST-110", 2)
        EXPECTED[c(7)] = {"decision": "approve", "amount": v, "findings": []}
        _parts(c(8), rng, date(2026, 8, 5), date(2026, 10, 1), "SN-75", 3)
        EXPECTED[c(8)] = {"decision": "reject", "amount": 0.0, "findings": []}
        v = _dealer(c(9), rng, duplicate=False)
        EXPECTED[c(9)] = {"decision": "approve", "amount": v, "findings": []}
        _dealer(c(10), rng, duplicate=True)
        EXPECTED[c(10)] = {"decision": "reject", "amount": 0.0, "findings": ["duplicate_submission"]}
        v = _employee(c(11), rng, with_receipt=True)
        EXPECTED[c(11)] = {"decision": "approve", "amount": v, "findings": []}
        _employee(c(12), rng, with_receipt=False)
        EXPECTED[c(12)] = {"decision": "escalate", "amount": 0.0, "findings": ["missing_documents"]}
        ids += [c(i) for i in range(1, 13)]
    db.ex("CREATE TABLE IF NOT EXISTS golden(claim_id TEXT PRIMARY KEY, expected TEXT)")
    for cid in ids:
        db.ex("INSERT INTO golden VALUES (?,?)", (cid, db.jdump(EXPECTED[cid])))
    return ids


def run_eval() -> bool:
    from rich.console import Console
    from rich.table import Table

    from app import runner, trace
    from app.mock_claimant import scripted_reply
    from app.seed import seed
    console = Console(width=170)
    seed(with_golden=True)
    rows = db.q("SELECT * FROM golden")
    expected = {**{k: v for k, v in EXPECTED.items() if k.startswith("CLM-") and "-G" not in k},
                **{r["claim_id"]: db.jload(r["expected"]) for r in rows}}
    order = ["CLM-1001", "CLM-2001", "CLM-3001", "CLM-3002", "CLM-4001", "CLM-5001"] + \
        sorted(k for k in expected if "-G" in k)
    tb = Table("claim", "type", "expected", "got", "amount ok", "findings ok", "turns", "tools", "sec")
    score = {"decision": 0, "amount": 0, "findings": 0, "n": 0}
    turns, tools = [], []
    for cid in order:
        t0 = time.perf_counter()
        st = runner.start(cid)
        while st["waiting_for"]:
            w = st["waiting_for"]
            if w["type"] == "info_request":
                r = scripted_reply(cid, w["request"]["round"])
                st = runner.resume(cid, r if r["doc_ids"] else {"no_response": True})
            else:
                st = runner.resume(cid, {"decision": "approved", "approver": f"eval:{w['role']}"})
        s = runner.state(cid)
        exp = expected[cid]
        if s["status"] == "ESCALATED":
            got, amt = "escalate", 0.0
        else:
            got, amt = s["approval"]["final"]["decision"], s["approval"]["final"]["amount"]
        ftypes = {f["type"] for f in s.get("findings", [])}
        d_ok, a_ok = got == exp["decision"], abs(amt - exp["amount"]) < 0.01
        f_ok = set(exp["findings"]) <= ftypes
        if exp.get("capa"):
            f_ok = f_ok and sum(1 for a in s.get("actions", []) if a["action"] == "capa_ticket") == exp["capa"]
        ev = trace.get_trace(cid)
        turns.append(s["flags"]["turns"])
        tools.append(sum(1 for e in ev if e["type"] == "tool_call"))
        score["decision"] += d_ok
        score["amount"] += a_ok
        score["findings"] += f_ok
        score["n"] += 1
        mark = lambda b: "[green]yes[/green]" if b else "[red]NO[/red]"  # noqa: E731
        tb.add_row(cid, s["claim"].get("type") or "", f"{exp['decision']} {exp['amount']:,.0f}",
                   f"{got} {amt:,.0f}" if d_ok else f"[red]{got} {amt:,.0f}[/red]", mark(a_ok), mark(f_ok),
                   str(turns[-1]), str(tools[-1]), f"{time.perf_counter() - t0:.1f}")
    console.print(tb)
    n = score["n"]
    console.print(f"decision accuracy {score['decision']}/{n}  amount exact {score['amount']}/{n}  expected findings "
                  f"{score['findings']}/{n}  mean agent turns {sum(turns) / n:.1f}  mean tool calls {sum(tools) / n:.1f}")
    return score["decision"] == n and score["amount"] == n and score["findings"] == n
