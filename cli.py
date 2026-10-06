"""Command-line runner with a live execution trace.

    python cli.py seed                       # reset and seed data + documents
    python cli.py run s1|s2|s3|parts|employee|all|CLM-xxxx [--approve auto|interactive] [--no-reply]
    python cli.py trace CLM-xxxx [--json]    # replay a stored trace
    python cli.py status | outbox | actions
    python cli.py eval                       # golden-set evaluation
"""
from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console
from rich.table import Table

from app import db, trace
from app.mock_claimant import scripted_reply

console = Console(highlight=False, width=170)
COLORS = {"case_manager": "bold magenta", "intake": "cyan", "completeness": "cyan", "policy": "yellow",
          "investigation": "green", "duplicate_pattern": "bright_green", "adjudication": "bold blue",
          "verifier": "blue", "settlement": "bold red", "system": "white", "claimant": "bright_white"}
ICONS = {"delegate": "->", "tool_call": "tool", "rule_fired": "rule", "finding": "find", "plan": "plan",
         "recommendation": "REC", "verification": "verify", "approval_request": "HITL", "approval": "HITL",
         "action": "ACT", "status_change": "status", "escalation": "ESC", "guardrail": "GUARD",
         "info_request": "ask", "info_response": "reply", "reasoning": "think", "memory": "mem",
         "llm_fallback": "fallback", "tool_error": "error"}


def print_event(e: dict, verbose: bool = True) -> None:
    if not verbose and e["type"] in ("tool_call",):
        return
    color = COLORS.get(e["agent"], "white")
    tag = ICONS.get(e["type"], e["type"])
    ref = f" [dim]{e['result_ref']}[/dim]" if e.get("result_ref") else ""
    style = "bold" if e["type"] in ("recommendation", "approval", "escalation", "status_change") else ""
    summary = e["summary"].replace("[", "\\[")
    console.print(f"[dim]{e['seq']:>3}[/dim] [{color}]{e['agent']:<17}[/{color}] [dim]{tag:<7}[/dim] "
                  f"[{style}]{summary}[/{style}]{ref}" if style else
                  f"[dim]{e['seq']:>3}[/dim] [{color}]{e['agent']:<17}[/{color}] [dim]{tag:<7}[/dim] {summary}{ref}")


def approval_prompt(req: dict, mode: str) -> dict:
    rec = req["recommendation"]
    console.rule(f"[bold]Approval needed: {req['role']}[/bold]")
    console.print(f"{req['claim_id']}  {rec['decision'].upper()}  INR {rec['amount']:,.2f}"
                  + ("  [yellow](policy exception)[/yellow]" if rec["exception"] else "")
                  + f"  confidence {rec['confidence']}")
    for r in rec["reasons"]:
        console.print(f"  - {r['text']} [dim]{r['evidence_ids']}[/dim]")
    console.print("  actions: " + ", ".join(a["type"] for a in rec["actions"]))
    if mode == "auto":
        console.print(f"[green]auto-approver ({req['role']}): approved[/green]")
        return {"decision": "approved", "approver": f"auto:{req['role']}", "reason": "auto-approved in demo"}
    choice = console.input("[a]pprove / [m]odify / [r]eject / [s]end back? ").strip().lower()[:1]
    if choice == "m":
        amt = float(console.input("new amount: "))
        return {"decision": "modified", "amount": amt, "reason": console.input("reason: "), "approver": req["role"]}
    if choice == "r":
        return {"decision": "rejected", "reason": console.input("reason: "), "approver": req["role"]}
    if choice == "s":
        return {"decision": "sent_back", "reason": console.input("question for the agents: "),
                "approver": req["role"]}
    return {"decision": "approved", "approver": req["role"]}


def drive(claim_id: str, approve: str = "auto", reply: bool = True, verbose: bool = True) -> dict:
    """Run a claim to completion, answering interrupts as the mock claimant / approver."""
    from app import runner
    listener = (lambda e: print_event(e, verbose)) if verbose is not None else None
    if listener:
        trace.subscribe(listener)
    try:
        console.rule(f"[bold]{claim_id}[/bold]")
        try:
            st = runner.start(claim_id)
        except RuntimeError:
            st = runner.status(claim_id)
            if not st["waiting_for"]:
                console.print(f"{claim_id} already processed ({st['status']}). Run `python cli.py seed` to reset.")
                return st
        while st["waiting_for"]:
            w = st["waiting_for"]
            if w["type"] == "info_request":
                r = scripted_reply(claim_id, w["request"]["round"]) if reply else {"doc_ids": []}
                st = runner.resume(claim_id, r if r["doc_ids"] else {"no_response": True})
            else:
                st = runner.resume(claim_id, approval_prompt(w, approve))
        final = st.get("final") or {}
        console.print(f"[bold]=> {claim_id}: {st['status']}[/bold]"
                      + (f"  final {final.get('decision')} INR {final.get('amount', 0):,.2f}" if final else ""))
        return st
    finally:
        if listener:
            trace.unsubscribe(listener)


def main() -> None:
    from app.seed import SCENARIO_CLAIMS
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--golden", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("target")
    r.add_argument("--approve", choices=["auto", "interactive"], default="auto")
    r.add_argument("--no-reply", action="store_true", help="claimant never answers information requests")
    r.add_argument("--quiet", action="store_true", help="hide individual tool calls")
    t = sub.add_parser("trace")
    t.add_argument("claim_id")
    t.add_argument("--json", action="store_true")
    sub.add_parser("status")
    sub.add_parser("outbox")
    sub.add_parser("actions")
    sub.add_parser("eval")
    a = ap.parse_args()

    if a.cmd == "seed":
        from app.seed import seed
        out = seed(with_golden=a.golden)
        console.print(f"seeded {len(out['claims'])} claims: {', '.join(out['claims'][:8])}"
                      + (" ..." if len(out["claims"]) > 8 else ""))
    elif a.cmd == "run":
        if a.target == "all":
            ids = [c for k in ("s1", "s2", "s3", "parts", "employee") for c in SCENARIO_CLAIMS[k]]
        else:
            ids = SCENARIO_CLAIMS.get(a.target, [a.target])
        for cid in ids:
            drive(cid, a.approve, not a.no_reply, verbose=not a.quiet)
    elif a.cmd == "trace":
        events = trace.get_trace(a.claim_id)
        if a.json:
            print(json.dumps(events, indent=1, default=str))
        else:
            for e in events:
                print_event(e)
    elif a.cmd == "status":
        tb = Table("claim", "type", "status", "outcome", "claimed", "settled")
        for c in db.q("SELECT * FROM claims WHERE is_historical=0 ORDER BY claim_id"):
            tb.add_row(c["claim_id"], c["type"] or "", c["status"], c["outcome"] or "",
                       f"{c['amount'] or 0:,.0f}", f"{c['settled_amount'] or 0:,.0f}")
        console.print(tb)
    elif a.cmd == "outbox":
        for m in db.q("SELECT * FROM outbox ORDER BY ts"):
            console.rule(f"{m['claim_id']} {m['kind']} -> {m['recipient']}")
            console.print(f"[bold]{m['subject']}[/bold]\n{m['body']}")
    elif a.cmd == "actions":
        tb = Table("claim", "action", "result")
        for r in db.q("SELECT * FROM actions_log ORDER BY ts"):
            res = json.loads(r["result"])
            tb.add_row(r["claim_id"], r["action"], f"{res.get('ref')} {res.get('amount', '')}")
        console.print(tb)
    elif a.cmd == "eval":
        from app.golden import run_eval
        sys.exit(0 if run_eval() else 1)


if __name__ == "__main__":
    main()
