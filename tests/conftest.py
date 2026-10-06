import os
import tempfile

# Redirect all data to a temp dir before any app module touches the filesystem.
os.environ["CLAIMS_DATA_DIR"] = tempfile.mkdtemp(prefix="claims-test-")
os.environ["LLM_PROVIDER"] = "offline"

import pytest  # noqa: E402


@pytest.fixture()
def fresh():
    from app.llm.provider import set_model_override
    from app.seed import seed
    set_model_override(None)
    seed()
    yield
    set_model_override(None)


def drive(claim_id, approvals=None, reply=True):
    """Run a claim to the end. `approvals` maps role -> decision dict (default: approve)."""
    from app import runner
    from app.mock_claimant import scripted_reply
    approvals = approvals or {}
    st = runner.start(claim_id)
    seen = {}
    while st["waiting_for"]:
        w = st["waiting_for"]
        if w["type"] == "info_request":
            r = scripted_reply(claim_id, w["request"]["round"]) if reply else {"doc_ids": []}
            st = runner.resume(claim_id, r if r["doc_ids"] else {"no_response": True})
        else:
            role = w["role"]
            seen[role] = seen.get(role, 0) + 1
            d = approvals.get(role, {"decision": "approved"})
            if isinstance(d, list):
                d = d[min(seen[role] - 1, len(d) - 1)]
            st = runner.resume(claim_id, {"approver": f"test:{role}", **d})
    return runner.state(claim_id)
