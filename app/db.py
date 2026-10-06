"""SQLite access (Postgres-compatible SQL). One connection per thread, per database path."""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from app import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS dealers(dealer_id TEXT PRIMARY KEY, name TEXT, region TEXT, tier TEXT,
    authorised_services TEXT, email TEXT);
CREATE TABLE IF NOT EXISTS customers(customer_id TEXT PRIMARY KEY, name TEXT, region TEXT, email TEXT);
CREATE TABLE IF NOT EXISTS employees(employee_id TEXT PRIMARY KEY, name TEXT, grade TEXT, cost_centre TEXT,
    manager TEXT, email TEXT);
CREATE TABLE IF NOT EXISTS equipment(serial TEXT PRIMARY KEY, model TEXT, category TEXT, sale_date TEXT,
    owner TEXT, dealer_id TEXT);
CREATE TABLE IF NOT EXISTS warranty_contracts(contract_id TEXT PRIMARY KEY, serial TEXT, plan TEXT,
    start_date TEXT, end_date TEXT, hours_cap INTEGER, covered TEXT, exclusions TEXT);
CREATE TABLE IF NOT EXISTS service_history(record_id TEXT PRIMARY KEY, serial TEXT, date TEXT, dealer_id TEXT,
    job_code TEXT, description TEXT, parts_used TEXT, hours_reading INTEGER, failed_part_returned INTEGER,
    invoice_no TEXT);
CREATE TABLE IF NOT EXISTS parts(part_no TEXT PRIMARY KEY, description TEXT, price REAL, supplier TEXT,
    component_group TEXT);
CREATE TABLE IF NOT EXISTS dispatches(dispatch_id TEXT PRIMARY KEY, part_no TEXT, packed_part_no TEXT,
    serial TEXT, qty INTEGER, ship_to TEXT, batch TEXT, dispatch_date TEXT, invoice_no TEXT, value REAL);
CREATE TABLE IF NOT EXISTS shipments(shipment_id TEXT PRIMARY KEY, dispatch_id TEXT, carrier TEXT,
    dispatched_on TEXT, delivered_on TEXT, pod_remark TEXT, insured_value REAL, serial TEXT);
CREATE TABLE IF NOT EXISTS invoices(invoice_no TEXT PRIMARY KEY, party_id TEXT, party_type TEXT, date TEXT,
    total REAL, lines TEXT);
CREATE TABLE IF NOT EXISTS claims(claim_id TEXT PRIMARY KEY, type TEXT, claimant_id TEXT, claimant_type TEXT,
    serial TEXT, component TEXT, part_no TEXT, batch TEXT, invoice_no TEXT, amount REAL, submitted_on TEXT,
    status TEXT, outcome TEXT, settled_amount REAL, narrative TEXT, data TEXT, is_historical INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS documents(doc_id TEXT PRIMARY KEY, claim_id TEXT, path TEXT, filename TEXT,
    round INTEGER DEFAULT 0, uploaded_on TEXT);
CREATE TABLE IF NOT EXISTS info_responses(claim_id TEXT, round INTEGER, path TEXT, filename TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS trace(claim_id TEXT, seq INTEGER, ts TEXT, agent TEXT, type TEXT, summary TEXT,
    payload TEXT, PRIMARY KEY(claim_id, seq));
CREATE TABLE IF NOT EXISTS approvals(approval_id TEXT PRIMARY KEY, claim_id TEXT, required_roles TEXT,
    records TEXT, status TEXT, request TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS actions_log(idempotency_key TEXT PRIMARY KEY, claim_id TEXT, action TEXT,
    payload TEXT, result TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS outbox(msg_id TEXT PRIMARY KEY, claim_id TEXT, recipient TEXT, subject TEXT,
    body TEXT, kind TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS overrides(id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT, claim_type TEXT,
    role TEXT, original_decision TEXT, original_amount REAL, new_decision TEXT, new_amount REAL, reason TEXT,
    finding_types TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS escalations(id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT, reason TEXT,
    open_items TEXT, ts TEXT);
"""


def conn() -> sqlite3.Connection:
    path = str(config.db_path())
    cache = getattr(_local, "conns", None)
    if cache is None:
        cache = _local.conns = {}
    c = cache.get(path)
    if c is None:
        config.db_path().parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        cache[path] = c
    return c


def close_all() -> None:
    for c in getattr(_local, "conns", {}).values():
        c.close()
    _local.conns = {}


def q(sql: str, params: tuple | list = ()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def one(sql: str, params: tuple | list = ()) -> dict | None:
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


def ex(sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
    return conn().execute(sql, params)


def jload(v: Any, default: Any = None) -> Any:
    if v is None or v == "":
        return default
    return json.loads(v)


def jdump(v: Any) -> str:
    return json.dumps(v, default=str)


# ---- claim record helpers --------------------------------------------------------------

def get_claim(claim_id: str) -> dict | None:
    row = one("SELECT * FROM claims WHERE claim_id=?", (claim_id,))
    if row:
        row["data"] = jload(row["data"], {})
    return row


def save_claim_data(claim_id: str, data: dict) -> None:
    ex("UPDATE claims SET data=? WHERE claim_id=?", (jdump(data), claim_id))


def update_claim(claim_id: str, **cols: Any) -> None:
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols)
    ex(f"UPDATE claims SET {sets} WHERE claim_id=?", (*cols.values(), claim_id))


def effective_fields(claim: dict) -> dict:
    """Extracted claim-form fields overlaid with recorded, evidence-backed corrections."""
    data = claim.get("data") or {}
    fields = dict(data.get("fields", {}))
    for k, v in data.get("corrections", {}).items():
        fields[k] = v["value"]
    return fields
