"""Exercises every LLM code path with a scripted model: supervisor choice, structured extraction and
classification, tool-using ReAct loops, verifier-driven repair, guardrail on a disallowed supervisor choice,
and fallback when the provider errors."""
from app import trace
from app.llm.provider import set_model_override
from tests.conftest import drive
from tests.fake_llm import ScriptedLLM


def test_full_case_through_llm_paths(fresh):
    llm = ScriptedLLM()
    set_model_override(llm)
    s = drive("CLM-1001")
    assert s["status"] == "CLOSED"
    assert s["approval"]["final"]["amount"] == 20600.0
    assert "structured:NextAction" in llm.calls and "structured:Classification" in llm.calls
    assert "react:investigation" in llm.calls and "react:duplicate" in llm.calls
    # calc call + wrong submission (rejected by the verifier inside the loop) + corrected submission
    assert llm.calls.count("react:adjudication") == 3
    assert not [e for e in trace.get_trace("CLM-1001") if e["type"] == "llm_fallback"]
    delegates = [e for e in trace.get_trace("CLM-1001") if e["type"] == "delegate" and not e["forced"]]
    assert all("LLM supervisor" in e["rationale"] for e in delegates)


def test_supervisor_cannot_pick_a_disallowed_step(fresh):
    set_model_override(ScriptedLLM(supervisor_override="settlement"))
    s = drive("CLM-1001")
    guard = [e for e in trace.get_trace("CLM-1001") if e["type"] == "guardrail" and "not permitted" in e["summary"]]
    assert guard and s["status"] == "CLOSED"
    assert s["approval"]["final"]["amount"] == 20600.0


class Broken:
    def __getattr__(self, name):
        raise ConnectionError("provider down")


def test_provider_failure_falls_back_to_deterministic_path(fresh):
    set_model_override(Broken())
    s = drive("CLM-2001")
    assert s["approval"]["final"]["amount"] == 303500.0
    assert [e for e in trace.get_trace("CLM-2001") if e["type"] == "llm_fallback"]
