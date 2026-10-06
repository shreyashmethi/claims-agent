import time

from fastapi.testclient import TestClient


def _wait(c, cid, pred, t=20):
    for _ in range(t * 10):
        r = c.get(f"/claims/{cid}").json()
        if pred(r):
            return r
        time.sleep(0.1)
    raise AssertionError(r["status"])


def test_api_flow(fresh):
    from app.api.main import app
    c = TestClient(app)
    assert c.post("/claims/CLM-3001/process").status_code == 202
    r = _wait(c, "CLM-3001", lambda r: r["waiting_for"])
    assert r["waiting_for"]["role"] == "claims_manager"
    assert c.post("/claims/CLM-3001/approval", json={"role": "claims_officer", "decision": "approved"}).status_code == 403
    assert c.post("/claims/CLM-3001/approval", json={"role": "claims_manager", "decision": "modified"}).status_code == 422
    assert c.post("/claims/CLM-3001/approval", json={"role": "claims_manager", "decision": "approved"}).status_code == 202
    r = _wait(c, "CLM-3001", lambda r: r["status"] == "CLOSED")
    assert r["approval"]["final"]["decision"] == "reject" and "tokens" not in r["approval"]
    assert c.get("/claims/CLM-3001/trace").json()[0]["type"] == "delegate"
    assert c.get("/outbox", params={"claim_id": "CLM-3001"}).json()[0]["kind"] == "decision_letter"
    assert c.get("/mock/shipments/SHP-5530").json()["carrier"] == "Shree Ganesh Roadlines"


def test_api_upload_new_claim(fresh, tmp_path):
    from app.api.main import app
    from app.documents import write_pdf
    c = TestClient(app)
    f = write_pdf(tmp_path / "form.pdf", "Claim", "claim_form", {"Claim Type": "warranty", "Claimant ID": "D-014",
                                                                 "Claimed Amount": "100"})
    r = c.post("/claims", data={"claimant_id": "D-014", "claim_id": "CLM-7000"},
               files=[("files", ("form.pdf", f.read_bytes(), "application/pdf"))]).json()
    assert r["claim_id"] == "CLM-7000"
    r = _wait(c, "CLM-7000", lambda r: r["waiting_for"] or r["status"] == "ESCALATED")
    assert r["waiting_for"]["type"] == "info_request"
    assert any("invoice" in i for i in r["waiting_for"]["request"]["items"])
