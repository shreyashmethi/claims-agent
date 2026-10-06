"""Tool registry and per-agent tool context.

The registry, not the prompt, decides which agent may call which tool. Read tools add an entry to the evidence
ledger; write tools are executed only with a valid approval token (checked inside the tool). Every call is a
trace event with arguments, latency and the evidence ID it produced.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import time
import typing
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import create_model

from app import trace
from app.schemas import Evidence
from app.tools.actions import ApprovalRequired

Effect = typing.Literal["read", "write_internal", "write_external", "financial"]


@dataclass
class ToolSpec:
    name: str
    fn: Callable
    effect: Effect
    agents: set[str]
    description: str
    params: list[str] = field(default_factory=list)


REGISTRY: dict[str, ToolSpec] = {}


def register(name: str, fn: Callable, effect: Effect, agents: set[str], description: str) -> None:
    REGISTRY[name] = ToolSpec(name, fn, effect, agents, description, list(inspect.signature(fn).parameters))


def _redact(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if k == "approval_token" and v:
            out[k] = str(v).split(".")[0] + ".****"
        elif k in ("body", "message") and isinstance(v, str):
            v = " ".join(v.split())
            out[k] = v if len(v) <= 80 else v[:77] + "..."
        else:
            out[k] = v
    return out


def _preview(result: Any, n: int = 220) -> str:
    s = json.dumps(result, default=str)
    return s if len(s) <= n else s[: n - 3] + "..."


class ToolContext:
    """Holds one agent's view of the case for one turn: identity, evidence ledger, permitted tools."""

    def __init__(self, state: dict, agent: str):
        self.claim_id = state["claim_id"]
        self.agent = agent
        self.evidence: list[dict] = list(state.get("evidence", []))

    def _next_ev(self) -> str:
        return f"EV-{len(self.evidence) + 1:03d}"

    def evidence_ids(self) -> set[str]:
        return {e["id"] for e in self.evidence}

    def call(self, name: str, **args: Any) -> dict:
        spec = REGISTRY.get(name)
        if spec is None:
            return {"error": f"unknown tool '{name}'"}
        if self.agent not in spec.agents:
            trace.emit(self.claim_id, self.agent, "guardrail", f"blocked: {self.agent} may not call {name}",
                       tool=name)
            return {"error": f"agent '{self.agent}' is not permitted to call '{name}'"}
        if "claim_id" in spec.params:
            args["claim_id"] = self.claim_id
        args = {k: v for k, v in args.items() if k in spec.params}
        t0 = time.perf_counter()
        try:
            result = spec.fn(**args)
        except ApprovalRequired as e:
            trace.emit(self.claim_id, self.agent, "guardrail", f"blocked {name}: no valid approval token",
                       tool=name, args=_redact(args), error=str(e))
            return {"error": str(e)}
        except Exception as e:  # tool failures are data for the agent, not crashes
            trace.emit(self.claim_id, self.agent, "tool_error", f"{name} failed: {e}", tool=name,
                       args=_redact(args))
            return {"error": f"{type(e).__name__}: {e}"}
        ms = round((time.perf_counter() - t0) * 1000, 1)
        ev_id = None
        if spec.effect == "read":
            ev_id = self._next_ev()
            blob = json.dumps(result, default=str, sort_keys=True)
            shown = {k: v for k, v in args.items() if k != "claim_id"}
            self.evidence.append(Evidence(
                id=ev_id, tool=name, agent=self.agent, data=result,
                summary=f"{name}({', '.join(f'{k}={v}' for k, v in shown.items())})",
                hash=hashlib.sha256(blob.encode()).hexdigest()[:12]).model_dump())
        trace.emit(self.claim_id, self.agent, "tool_call",
                   f"{name}({', '.join(f'{k}={v}' for k, v in _redact(args).items() if k != 'claim_id')})",
                   tool=name, effect=spec.effect, args=_redact(args), result_ref=ev_id, latency_ms=ms,
                   result_preview=_preview(result))
        return {"evidence_id": ev_id, "result": result} if ev_id else {"result": result}

    # ---- LLM tool schemas ---------------------------------------------------------------
    def tool_schemas(self, names: list[str]) -> list[dict]:
        out = []
        for n in names:
            spec = REGISTRY[n]
            hints = typing.get_type_hints(spec.fn)
            sig = inspect.signature(spec.fn)
            fields = {}
            for p in sig.parameters.values():
                if p.name in ("claim_id",):
                    continue
                default = ... if p.default is inspect.Parameter.empty else p.default
                fields[p.name] = (hints.get(p.name, str), default)
            model = create_model(f"{n}_args", **fields)
            schema = model.model_json_schema()
            schema.pop("title", None)
            out.append({"type": "function", "function": {"name": n, "description": spec.description,
                                                         "parameters": schema}})
        return out
