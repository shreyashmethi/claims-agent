"""FastAPI: claims API, approval console API, claimant replies, live trace stream, and mock enterprise services.

Run:  uvicorn app.api.main:app --reload
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import db, runner, trace
from app.mock_claimant import attach, scripted_reply, submit_claim
from app.schemas import ApprovalDecision
from app.tools import services

app = FastAPI(title="Agentic Claims Processing Manager", version="0.1.0",
              description="Multi-agent claims assessment with deterministic rules and human approvals.")

TERMINAL = {"CLOSED", "ESCALATED"}


def _claim_or_404(claim_id: str) -> dict:
    c = db.get_claim(claim_id)
    if not c:
        raise HTTPException(404, f"claim {claim_id} not found")
    return c


def _run_bg(claim_id: str) -> None:
    try:
        runner.start(claim_id)
    except Exception as e:  # surfaced in the trace, not lost in a worker thread
        trace.emit(claim_id, "system", "tool_error", f"run failed: {type(e).__name__}: {e}")


def _resume_bg(claim_id: str, value: dict) -> None:
    try:
        runner.resume(claim_id, value)
    except Exception as e:
        trace.emit(claim_id, "system", "tool_error", f"resume failed: {type(e).__name__}: {e}")


@app.get("/health")
def health():
    from app.llm.provider import provider_name
    return {"ok": True, "llm_provider": provider_name()}


@app.post("/admin/seed")
def admin_seed(golden: bool = False):
    from app.seed import seed
    return seed(with_golden=golden)


# ------------------------------------------------------------------------------ claims
@app.post("/claims", status_code=202)
async def create_claim(background: BackgroundTasks, claimant_id: str = Form(...), claim_id: str | None = Form(None),
                       files: list[UploadFile] = File(...), auto_start: bool = Form(True)):
    cid = claim_id or f"CLM-{9000 + (db.one('SELECT COUNT(*) n FROM claims WHERE is_historical=0')['n']):04d}"
    if db.get_claim(cid):
        raise HTTPException(409, f"{cid} exists")
    tmp = Path(tempfile.mkdtemp())
    paths = []
    for f in files:
        p = tmp / Path(f.filename or "upload.pdf").name
        p.write_bytes(await f.read())
        paths.append(p)
    submit_claim(cid, claimant_id, paths)
    if auto_start:
        background.add_task(_run_bg, cid)
    return {"claim_id": cid, "documents": len(paths), "started": auto_start}


@app.post("/claims/{claim_id}/process", status_code=202)
def process_claim(claim_id: str, background: BackgroundTasks):
    _claim_or_404(claim_id)
    if runner.state(claim_id):
        raise HTTPException(409, "already started")
    background.add_task(_run_bg, claim_id)
    return {"claim_id": claim_id, "started": True}


@app.get("/claims")
def list_claims():
    return db.q("SELECT claim_id, type, claimant_id, amount, submitted_on, status, outcome, settled_amount "
                "FROM claims WHERE is_historical=0 ORDER BY claim_id")


@app.get("/claims/{claim_id}")
def get_claim(claim_id: str):
    c = _claim_or_404(claim_id)
    s = runner.state(claim_id) or {}
    return {
        "claim_id": claim_id, "status": c["status"], "outcome": c["outcome"], "type": c["type"],
        "waiting_for": runner.pending(claim_id) if s else None,
        "plan": s.get("plan"), "claim": s.get("claim"), "findings": s.get("findings"),
        "completeness": s.get("completeness"), "eligibility": s.get("eligibility"), "duplicate": s.get("duplicate"),
        "calc": s.get("calc"), "recommendation": s.get("recommendation"), "verification": s.get("verification"),
        "approval": {k: v for k, v in (s.get("approval") or {}).items() if k != "tokens"} or None,
        "actions": s.get("actions"), "info_requests": s.get("info_requests"), "escalation": s.get("escalation"),
        "evidence_count": len(s.get("evidence", [])),
    }


@app.get("/claims/{claim_id}/evidence")
def get_evidence(claim_id: str, evidence_id: str | None = None):
    ev = (runner.state(claim_id) or {}).get("evidence", [])
    if evidence_id:
        hit = next((e for e in ev if e["id"] == evidence_id), None)
        if not hit:
            raise HTTPException(404, evidence_id)
        return hit
    return [{k: e[k] for k in ("id", "tool", "agent", "summary", "hash")} for e in ev]


@app.get("/claims/{claim_id}/trace")
def get_trace(claim_id: str, after: int = 0):
    _claim_or_404(claim_id)
    return trace.get_trace(claim_id, after)


@app.get("/claims/{claim_id}/trace/stream")
async def stream_trace(claim_id: str, after: int = 0):
    """Server-sent events: one event per trace record, until the claim pauses for input or closes."""
    _claim_or_404(claim_id)

    async def gen():
        seq, idle = after, 0
        while True:
            events = trace.get_trace(claim_id, seq)
            for e in events:
                seq = e["seq"]
                yield f"id: {seq}\nevent: {e['type']}\ndata: {json.dumps(e, default=str)}\n\n"
            idle = 0 if events else idle + 1
            status = db.get_claim(claim_id)["status"]
            if idle > 4 and (status in TERMINAL or runner.pending(claim_id)):
                yield f"event: paused\ndata: {json.dumps({'status': status, 'waiting_for': runner.pending(claim_id)}, default=str)}\n\n"
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ------------------------------------------------------------------------------ claimant replies
class InfoResponse(BaseModel):
    use_scripted: bool = False
    no_response: bool = False


@app.post("/claims/{claim_id}/info-response", status_code=202)
def info_response(claim_id: str, body: InfoResponse, background: BackgroundTasks):
    w = runner.pending(claim_id)
    if not w or w["type"] != "info_request":
        raise HTTPException(409, "claim is not waiting for claimant information")
    if body.no_response:
        value = {"no_response": True}
    else:
        value = scripted_reply(claim_id, w["request"]["round"])
        if not value["doc_ids"]:
            raise HTTPException(400, "no scripted reply for this round; upload files instead")
    background.add_task(_resume_bg, claim_id, value)
    return {"claim_id": claim_id, "resumed_with": value}


@app.post("/claims/{claim_id}/info-response/upload", status_code=202)
async def info_response_upload(claim_id: str, background: BackgroundTasks, files: list[UploadFile] = File(...),
                               note: str = Form("")):
    w = runner.pending(claim_id)
    if not w or w["type"] != "info_request":
        raise HTTPException(409, "claim is not waiting for claimant information")
    tmp = Path(tempfile.mkdtemp())
    ids = []
    for f in files:
        p = tmp / Path(f.filename or "reply.pdf").name
        p.write_bytes(await f.read())
        ids.append(attach(claim_id, p, w["request"]["round"], p.name))
    background.add_task(_resume_bg, claim_id, {"doc_ids": ids, "note": note})
    return {"claim_id": claim_id, "doc_ids": ids}


# ------------------------------------------------------------------------------ approvals (HITL)
@app.get("/approvals/pending")
def pending_approvals():
    out = []
    for c in db.q("SELECT claim_id FROM claims WHERE status='PENDING_APPROVAL'"):
        w = runner.pending(c["claim_id"])
        if w and w["type"] == "approval":
            out.append(w)
    return out


@app.post("/claims/{claim_id}/approval", status_code=202)
def decide(claim_id: str, decision: ApprovalDecision, background: BackgroundTasks):
    """Approver decision. `role` must be the role the matrix is waiting for (SSO would assert it in production).
    decision: approved | modified (with amount + reason) | rejected (with reason) | sent_back (with question)."""
    w = runner.pending(claim_id)
    if not w or w["type"] != "approval":
        raise HTTPException(409, "claim is not waiting for an approval")
    if decision.role != w["role"]:
        raise HTTPException(403, f"waiting for role {w['role']}, got {decision.role}")
    if decision.decision in ("modified", "rejected", "sent_back") and not decision.reason:
        raise HTTPException(422, "a reason is required for modify, reject and send back")
    if decision.decision == "modified" and decision.amount is None:
        raise HTTPException(422, "modified requires an amount")
    background.add_task(_resume_bg, claim_id, decision.model_dump())
    return {"claim_id": claim_id, "accepted": decision.model_dump()}


# ------------------------------------------------------------------------------ outputs
@app.get("/outbox")
def outbox(claim_id: str | None = None):
    if claim_id:
        return db.q("SELECT * FROM outbox WHERE claim_id=? ORDER BY ts", (claim_id,))
    return db.q("SELECT * FROM outbox ORDER BY ts")


@app.get("/actions")
def actions(claim_id: str | None = None):
    rows = db.q("SELECT * FROM actions_log" + (" WHERE claim_id=?" if claim_id else "") + " ORDER BY ts",
                (claim_id,) if claim_id else ())
    for r in rows:
        r["payload"], r["result"] = json.loads(r["payload"]), json.loads(r["result"])
    return rows


@app.get("/escalations")
def escalations():
    rows = db.q("SELECT * FROM escalations ORDER BY id")
    for r in rows:
        r["open_items"] = json.loads(r["open_items"])
    return rows


# ------------------------------------------------------------------------------ mock enterprise services
@app.get("/mock/equipment/{serial}", tags=["mock"])
def m_equipment(serial: str):
    return services.lookup_equipment(serial)


@app.get("/mock/parties/{party_id}", tags=["mock"])
def m_party(party_id: str):
    return services.lookup_party(party_id)


@app.get("/mock/warranty/{serial}", tags=["mock"])
def m_warranty(serial: str):
    return services.warranty_contract(serial)


@app.get("/mock/service-history/{serial}", tags=["mock"])
def m_service(serial: str, job_code: str | None = None):
    return services.service_history(serial, job_code)


@app.get("/mock/invoices/{invoice_no}", tags=["mock"])
def m_invoice(invoice_no: str):
    return services.get_invoice(invoice_no)


@app.get("/mock/parts/{part_no}", tags=["mock"])
def m_part(part_no: str):
    return services.parts_record(part_no=part_no)


@app.get("/mock/dispatches/{dispatch_id}", tags=["mock"])
def m_dispatch(dispatch_id: str):
    return services.parts_record(dispatch_id=dispatch_id)


@app.get("/mock/shipments/{shipment_id}", tags=["mock"])
def m_shipment(shipment_id: str):
    return services.shipment_record(shipment_id=shipment_id)


@app.get("/mock/claims/search", tags=["mock"])
def m_claims(invoice_no: str | None = None, serial: str | None = None, part_no: str | None = None,
             claimant_id: str | None = None, outcome: str | None = None):
    return services.search_claims("", invoice_no, serial, part_no, claimant_id, outcome)


@app.get("/mock/failure-stats/{part_no}", tags=["mock"])
def m_failures(part_no: str, batch: str | None = None, months: int = 12):
    return services.failure_stats(part_no, batch, months)


@app.get("/mock/policies/search", tags=["mock"])
def m_policy(q: str = Query(...), k: int = 3):
    return services.search_policy(q, k)
