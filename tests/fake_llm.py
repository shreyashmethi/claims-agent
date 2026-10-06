"""A scripted chat model that exercises every LLM code path without an API key.

It behaves like a careful (but imperfect) model: it calls tools before submitting, and on its first adjudication
it submits the claimed amount instead of the calculated one, so the Verifier-driven repair loop is exercised.
"""
from __future__ import annotations

import json
import re

from langchain_core.messages import AIMessage, ToolMessage


def _tool_results(messages):
    out = []
    for m in messages:
        if isinstance(m, ToolMessage):
            try:
                out.append(json.loads(m.content))
            except json.JSONDecodeError:
                pass
    return out


class _Bound:
    def __init__(self, llm, tools):
        self.llm, self.names = llm, [t["function"]["name"] for t in tools]

    def invoke(self, messages):
        return self.llm.react(messages, self.names)


class _Structured:
    def __init__(self, llm, schema):
        self.llm, self.schema = llm, schema

    def invoke(self, messages):
        return self.llm.structured(self.schema, messages)


class ScriptedLLM:
    def __init__(self, supervisor_override: str | None = None):
        self.calls: list[str] = []
        self.supervisor_override = supervisor_override
        self._n = 0

    def _id(self):
        self._n += 1
        return f"call_{self._n}"

    def _call(self, name, **args):
        return {"name": name, "args": args, "id": self._id(), "type": "tool_call"}

    # ---- plain text (writer role)
    def invoke(self, prompt):
        self.calls.append("writer")
        text = prompt if isinstance(prompt, str) else str(prompt)
        body = text.split("\n\n", 1)[-1]
        return AIMessage(content=body)

    def bind_tools(self, tools):
        return _Bound(self, tools)

    def with_structured_output(self, schema, **kw):
        return _Structured(self, schema)

    # ---- structured output
    def structured(self, schema, messages):
        name = schema.__name__
        self.calls.append(f"structured:{name}")
        user = messages[-1].content
        if name == "NextAction":
            allowed = re.search(r"Allowed next steps: \[(.*?)\]", user).group(1)
            allowed = [a.strip(" '") for a in allowed.split(",")]
            default = re.search(r"Default planner suggestion: (\w+)", user).group(1)
            choice = self.supervisor_override or default
            return schema(agent=choice, goal="next step", rationale=f"LLM supervisor picks {choice}",
                          remaining_plan=[choice])
        if name == "ExtractedDoc":
            kind = re.search(r"DOCUMENT: (\w+)", user)
            return schema(kind=kind.group(1) if kind else "unknown", fields={})
        if name == "Classification":
            t = re.search(r"'claim_type': '([a-z ]+)'", user).group(1).replace(" ", "_")
            return schema(type=t, confidence=0.93, rationale="declared type matches documents")
        raise AssertionError(name)

    # ---- ReAct
    def react(self, messages, tools):
        system = messages[0].content
        results = _tool_results(messages)
        evid = [r["evidence_id"] for r in results if r.get("evidence_id")]
        if "Investigation Agent" in system:
            self.calls.append("react:investigation")
            if not results:
                return AIMessage(content="Start with coverage and labour.", tool_calls=[
                    self._call("run_check", check="coverage"), self._call("run_check", check="labour_vs_standard"),
                    self._call("run_check", check="serial_consistency")])
            findings = []
            for r in results:
                res = r.get("result") or {}
                if res.get("status") == "fail" and res.get("finding"):
                    f = res["finding"]
                    findings.append({"type": f["type"], "severity": f["severity"], "description": f["description"],
                                     "evidence_ids": [r["evidence_id"]], "needs": [], "resolved": True,
                                     "resolution": "capped by policy", "source_check": res["check"]})
            return AIMessage(content="", tool_calls=[self._call("submit", plan=["coverage", "labour_vs_standard",
                             "serial_consistency"], findings=findings, summary=f"{len(findings)} finding(s)")])
        if "Duplicate and Pattern Agent" in system:
            self.calls.append("react:duplicate")
            if not results:
                inv = re.search(r'"invoice_no": "([^"]+)"', messages[1].content)
                return AIMessage(content="", tool_calls=[self._call("search_claims", invoice_no=inv.group(1))])
            return AIMessage(content="", tool_calls=[self._call("submit", assessment={
                "is_duplicate": False, "rationale": "invoice not seen before", "evidence_ids": evid}, findings=[])])
        if "Adjudication Agent" in system:
            self.calls.append("react:adjudication")
            calc = next((r["result"] for r in results if isinstance(r.get("result"), dict)
                         and "admissible_amount" in r["result"]), None)
            if calc is None:
                return AIMessage(content="", tool_calls=[self._call("rules_calculate_settlement")])
            errors = [r for r in results if r.get("error") == "invalid submission"]
            amount = calc["claimed_amount"] if not errors else calc["admissible_amount"]
            decision = "approve" if not errors else "partial"
            cev = next(r["evidence_id"] for r in results if r.get("evidence_id"))
            return AIMessage(content="Submitting.", tool_calls=[self._call(
                "submit", decision=decision, amount=amount, confidence=0.9,
                reasons=[{"text": "Labour capped at standard hours (WAR-6.2).", "evidence_ids": [cev]}])])
        raise AssertionError(system[:80])
