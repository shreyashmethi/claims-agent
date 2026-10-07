import { useMemo, useRef, useState } from "react";
import type { TraceEvent } from "./api";
import { agentLabel, money, NEXT_LABEL, roleLabel, STATUS_LABEL, time } from "./format";

export function EvChip({ id, onOpen }: { id: string; onOpen: (id: string) => void }) {
  return (
    <button className="ev-chip" onClick={() => onOpen(id)} title={`Open evidence ${id}`}>
      {id}
    </button>
  );
}

export function EvChips({ ids, onOpen }: { ids?: (string | null)[]; onOpen: (id: string) => void }) {
  if (!ids?.length) return null;
  return <>{ids.filter(Boolean).map((i) => <EvChip key={i!} id={i!} onOpen={onOpen} />)}</>;
}

interface Step {
  n: number;
  head: TraceEvent | null;
  events: TraceEvent[];
}

function group(events: TraceEvent[]): Step[] {
  const steps: Step[] = [];
  let cur: Step = { n: 0, head: null, events: [] };
  for (const e of events) {
    if (e.type === "delegate") {
      if (cur.head || cur.events.length) steps.push(cur);
      cur = { n: 0, head: e, events: [] };
    } else {
      cur.events.push(e);
    }
  }
  if (cur.head || cur.events.length) steps.push(cur);
  return steps.map((s, i) => ({ ...s, n: i + 1 }));
}

function color(agent: string) {
  if (agent.includes(":") || agent.startsWith("auto") || agent.startsWith("test")) return "var(--a-human)";
  return `var(--a-${agent}, var(--a-system))`;
}

function Event({ e, onEvidence, fresh }: { e: TraceEvent; onEvidence: (id: string) => void; fresh: boolean }) {
  const cls = ["ev", fresh ? "new" : ""];
  const dot = <span className="dot" style={{ background: color(e.agent) }} />;
  const who = <span className="who">{agentLabel(e.agent)}</span>;
  const ev = <EvChips ids={e.evidence_ids ?? (e.evidence_id ? [e.evidence_id] : [])} onOpen={onEvidence} />;

  switch (e.type) {
    case "status_change":
      return (
        <li className={[...cls, "status"].join(" ")}>
          <span />
          <span>
            Status: {STATUS_LABEL[e.from_status as keyof typeof STATUS_LABEL] ?? e.from_status}{" "}
            <span className="arrow">to</span> <b>{STATUS_LABEL[e.to_status as keyof typeof STATUS_LABEL] ?? e.to_status}</b>
          </span>
        </li>
      );
    case "rule_fired": {
      const verdict = e.passed ? <span className="ok">Pass</span> : e.waivable
        ? <span className="maybe">Exception possible</span> : <span className="no">Fail</span>;
      return (
        <li className={[...cls, "rule"].join(" ")}>
          {dot}
          <span>
            {verdict} <b>{e.rule_id}</b> {e.clause?.replace(/^.*§[\d.]+\s*/, "")}: {e.summary.split(" - ").slice(1).join(" - ")}
          </span>
        </li>
      );
    }
    case "recommendation":
      return (
        <li className={[...cls, "strong"].join(" ")}>
          {dot}
          <span>{who}recommends <b>{e.decision === "partial" ? "approving in part" : e.decision === "approve"
            ? "approving in full" : e.decision === "reject" ? "rejecting" : "escalating"}</b>
            {e.amount ? <>, <b>{money(e.amount)}</b></> : null}
            {e.summary.includes("exception") ? " as a policy exception" : ""}</span>
        </li>
      );
    case "approval_request":
      return (
        <li className={[...cls, "human"].join(" ")}>
          {dot}
          <span>Sent for approval to <b>{(e.roles as string[]).map(roleLabel).join(", then ")}</b></span>
        </li>
      );
    case "approval":
      if (e.role) {
        return (
          <li className={[...cls, "human", "strong"].join(" ")}>
            {dot}
            <span><b>{roleLabel(e.role)}</b> {e.decision === "sent_back" ? "sent it back" : e.decision}
              {e.decision === "modified" && e.amount != null ? <> to <b>{money(e.amount)}</b></> : null}
              {e.summary.includes(" - ") ? <span className="muted">: {e.summary.split(" - ").slice(1).join(" - ")}</span> : null}
            </span>
          </li>
        );
      }
      return (
        <li className={[...cls, "human"].join(" ")}>
          {dot}
          <span>Approvals complete: <b>{e.final?.decision}</b> {money(e.final?.amount)}, {e.authorised?.length} action(s) authorised</span>
        </li>
      );
    case "guardrail":
      return (
        <li className={[...cls, "guard"].join(" ")}>
          {dot}
          <span>{who}Guardrail: {e.summary}</span>
        </li>
      );
    case "reasoning":
      return (
        <li className={[...cls, "think"].join(" ")}>
          {dot}
          <span>{who}{e.summary} {ev}</span>
        </li>
      );
    case "action":
      return (
        <li className={[...cls, e.status === "executed" ? "strong" : ""].join(" ")}>
          {dot}
          <span>{who}{e.summary.replace("->", "")} </span>
        </li>
      );
    default:
      return (
        <li className={cls.join(" ")}>
          {dot}
          <span>{who}{e.summary} {ev}</span>
        </li>
      );
  }
}

function StepView({ s, onEvidence, freshFrom }: { s: Step; onEvidence: (id: string) => void; freshFrom: number }) {
  const [openTools, setOpenTools] = useState(false);
  const tools = s.events.filter((e) => e.type === "tool_call");
  const rest = s.events.filter((e) => e.type !== "tool_call");
  const h = s.head;
  const target = h ? NEXT_LABEL[h.next] ?? h.next : "Received";
  return (
    <li className="step">
      <span className="step-n" style={{ background: h ? color(h.next) : "var(--steel)" }}>{s.n}</span>
      <div className="step-h">
        <span className="to">{target}</span>
        {h && (h.forced
          ? <span className="tag" title="The process requires this step at this point">Required step</span>
          : <span className="tag chosen" title="The case manager chose this among the allowed options">
              Chosen from {h.allowed?.length ?? 1} options</span>)}
        {h && <span className="quiet" style={{ fontSize: 12 }}>{time(h.ts)}</span>}
      </div>
      {h && <p className="step-why">{h.rationale.charAt(0).toUpperCase() + h.rationale.slice(1)}</p>}
      {rest.length > 0 && (
        <ul className="events">
          {rest.map((e) => <Event key={e.seq} e={e} onEvidence={onEvidence} fresh={e.seq > freshFrom} />)}
        </ul>
      )}
      {tools.length > 0 && (
        <>
          <button className="tools-toggle" aria-expanded={openTools} onClick={() => setOpenTools(!openTools)}>
            {openTools ? "Hide" : "Show"} {tools.length} tool call{tools.length > 1 ? "s" : ""}
          </button>
          {openTools && (
            <ul className="tool-list">
              {tools.map((t) => (
                <li key={t.seq}>
                  <span className="name">{t.tool}</span>{" "}
                  <span className="args">{Object.entries(t.args ?? {}).map(([k, v]) =>
                    `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ")}</span>{" "}
                  {t.result_ref && <EvChip id={t.result_ref} onOpen={onEvidence} />}
                  <span className="quiet"> {t.latency_ms} ms</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </li>
  );
}

export function Timeline({ events, live, onEvidence }: {
  events: TraceEvent[]; live: boolean; onEvidence: (id: string) => void;
}) {
  const steps = useMemo(() => group(events), [events]);
  // Highlight events that arrive after first paint so a live run is easy to follow.
  const firstSeen = useRef<number | null>(null);
  if (firstSeen.current === null && events.length) firstSeen.current = events[events.length - 1].seq;
  const tools = events.filter((e) => e.type === "tool_call").length;
  return (
    <section className="panel">
      <div className="panel-h">
        <h3>How the agents worked this claim</h3>
        <span className="meta">
          {live ? <span className="live">Working</span> : `${steps.length} steps, ${tools} tool calls`}
        </span>
      </div>
      <div className="panel-b">
        {events.length === 0 ? (
          <p className="muted" style={{ padding: "14px 0" }}>Nothing yet. Start processing to watch the agents work.</p>
        ) : (
          <ol className="steps">
            {steps.map((s) => <StepView key={s.n} s={s} onEvidence={onEvidence} freshFrom={firstSeen.current ?? 0} />)}
          </ol>
        )}
      </div>
    </section>
  );
}
