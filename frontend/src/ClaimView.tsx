import { useEffect, useRef, useState } from "react";
import { api, type ClaimDetail, type Status, type TraceEvent, type WaitingApproval, type WaitingInfo } from "./api";
import { money, roleLabel, typeLabel } from "./format";
import { useClaim, useTrace } from "./hooks";
import { Panels, type Tab } from "./Panels";
import { Timeline } from "./Timeline";

type Notify = (msg: string, bad?: boolean) => void;

export function ClaimView({ id, notify, onChanged }: { id: string; notify: Notify; onChanged: () => void }) {
  const { data: c, error, refresh } = useClaim(id);
  const live = !!c && !c.waiting_for && !["CLOSED", "ESCALATED"].includes(c.status) && c.flags?.turns != null;
  const events = useTrace(id, live || !c);
  const [tab, setTab] = useState<Tab>("decision");
  const [focusEv, setFocusEv] = useState<string | null>(null);
  useEffect(() => { setTab("decision"); setFocusEv(null); }, [id]);

  if (error) return <div className="page"><div className="empty"><h3>Could not load {id}</h3>{error}</div></div>;
  if (!c) return <div className="page muted">Loading {id}...</div>;

  const openEvidence = (ev: string) => { setFocusEv(ev); setTab("evidence"); };
  const after = () => { refresh(); onChanged(); };
  const party = c.claim?.parties?.claimant;
  const final = c.approval?.final;

  return (
    <div className="page">
      <header className="claim-head">
        <div>
          <h2>{c.claim_id}</h2>
          <p className="who">
            {typeLabel(c.type)} claim from {party?.name ?? c.claimant_id}, submitted {c.submitted_on}
            {c.claim?.type_confidence != null && <span className="quiet"> (classified at {Math.round(c.claim.type_confidence * 100)}% confidence)</span>}
          </p>
        </div>
        <div className="figure">
          {final ? (
            <>
              <div className="big" style={{ color: final.decision === "reject" ? "var(--bad)" : "var(--good)" }}>
                {final.decision === "reject" ? "Rejected" : money(final.amount)}
              </div>
              <div className="of">{final.decision === "reject" ? `${money(c.amount)} claimed` : `allowed of ${money(c.amount)} claimed`}</div>
            </>
          ) : (
            <><div className="big">{money(c.amount)}</div><div className="of">claimed</div></>
          )}
        </div>
      </header>

      <Lifecycle status={c.status} events={events} infoRounds={c.info_requests?.length ?? 0} final={final?.decision} />

      {c.waiting_for?.type === "approval" && <ApprovalBand c={c} w={c.waiting_for} notify={notify} after={after} />}
      {c.waiting_for?.type === "info_request" && <ClaimantBand c={c} w={c.waiting_for} notify={notify} after={after} />}
      {!c.waiting_for && c.flags?.turns == null && <StartBand id={id} notify={notify} after={after} />}
      {c.status === "ESCALATED" && c.escalation && (
        <section className="band bad">
          <h3>Escalated to a claims manager</h3>
          <p className="lede">{c.escalation.reason}. No money moved; the open findings are listed under Decision.</p>
        </section>
      )}

      <div className="work">
        <Timeline events={events} live={live} onEvidence={openEvidence} />
        <Panels c={c} tab={tab} setTab={setTab} focusEv={focusEv} onEvidence={openEvidence} />
      </div>
    </div>
  );
}

const MAIN: { key: string; label: string; statuses: Status[] }[] = [
  { key: "received", label: "Received", statuses: ["RECEIVED"] },
  { key: "classified", label: "Classified", statuses: ["CLASSIFIED"] },
  { key: "review", label: "Agents reviewing", statuses: ["UNDER_REVIEW", "PENDING_INFO"] },
  { key: "recommended", label: "Recommended", statuses: ["RECOMMENDED"] },
  { key: "approval", label: "Approval", statuses: ["PENDING_APPROVAL"] },
  { key: "decided", label: "Decided", statuses: ["APPROVED", "PARTIALLY_APPROVED", "REJECTED"] },
  { key: "settled", label: "Settled", statuses: ["SETTLING", "SETTLED"] },
  { key: "closed", label: "Closed", statuses: ["CLOSED", "ESCALATED"] },
];

function Lifecycle({ status, events, infoRounds, final }: {
  status: Status; events: TraceEvent[]; infoRounds: number; final?: string;
}) {
  const seen = new Set<string>(["RECEIVED", ...events.filter((e) => e.type === "status_change").map((e) => e.to_status)]);
  const idx = MAIN.findIndex((m) => m.statuses.includes(status));
  return (
    <div className="lifecycle" role="list" aria-label="Claim lifecycle">
      {MAIN.map((m, i) => {
        const reached = m.statuses.some((s) => seen.has(s));
        let label = m.label;
        let tone = "";
        let note: string | null = null;
        if (m.key === "decided" && final) label = { approve: "Approved", partial: "Partly approved", reject: "Rejected" }[final] ?? label;
        if (m.key === "review" && infoRounds) note = `Asked claimant ${infoRounds === 1 ? "once" : `${infoRounds} times`}`;
        if (m.key === "review" && status === "PENDING_INFO") { tone = "signal"; note = "Waiting on claimant"; }
        if (m.key === "approval" && status === "PENDING_APPROVAL") tone = "signal";
        if (m.key === "closed" && status === "ESCALATED") { label = "Escalated"; tone = "bad"; }
        if (m.key === "closed" && status === "CLOSED") tone = final === "reject" ? "" : "good";
        if (m.key === "settled" && final === "reject") note = "Not applicable";
        const now = i === idx;
        const done = !now && (i < idx || reached);
        return (
          <div key={m.key} role="listitem" className={`stage ${now ? `now ${tone}` : done ? "done" : ""}`}
               aria-current={now ? "step" : undefined}>
            <span className="bar" />
            {label}
            {note && <span className="note">{note}</span>}
          </div>
        );
      })}
    </div>
  );
}

function StartBand({ id, notify, after }: { id: string; notify: Notify; after: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <section className="band neutral">
      <h3>Not started</h3>
      <p className="lede">The claim and its documents are in. Start processing to hand it to the case manager.</p>
      <div style={{ marginTop: 12 }}>
        <button className="btn primary" disabled={busy} onClick={async () => {
          setBusy(true);
          try { await api.process(id); notify("Processing started"); after(); }
          catch (e) { notify((e as Error).message, true); setBusy(false); }
        }}>Start processing</button>
      </div>
    </section>
  );
}

type Choice = "approved" | "modified" | "rejected" | "sent_back";
const PHRASE: Record<string, string> = { approve: "approving in full", partial: "approving in part",
  reject: "rejecting the claim", escalate: "escalating" };

function ApprovalBand({ c, w, notify, after }: { c: ClaimDetail; w: WaitingApproval; notify: Notify; after: () => void }) {
  const rec = w.recommendation;
  const isCapa = w.role === "quality_lead";
  const [choice, setChoice] = useState<Choice>("approved");
  const [amount, setAmount] = useState(String(rec.amount));
  const [reason, setReason] = useState("");
  const [approver, setApprover] = useState("");
  const [busy, setBusy] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { setChoice("approved"); setReason(""); setAmount(String(rec.amount)); setBusy(false); },
    [w.approval_id, w.role, rec.amount]);

  const capa = rec.actions.filter((a) => a.type === "capa_ticket");
  const needsReason = choice !== "approved";
  const amt = Number(amount);
  const valid = (!needsReason || reason.trim().length > 2) && (choice !== "modified" || (amt >= 0 && !Number.isNaN(amt)));
  const verb: Record<Choice, string> = isCapa
    ? { approved: "Open the tickets", modified: "Open the tickets", rejected: "Decline the tickets", sent_back: "Send back to the agents" }
    : {
        approved: rec.decision === "reject" ? "Confirm rejection" : `Approve ${money(rec.amount)}`,
        modified: `${rec.decision === "reject" ? "Pay" : "Approve"} ${money(amt || 0)} instead`,
        rejected: "Reject the claim",
        sent_back: "Send back to the agents",
      };
  const done: Record<Choice, string> = { approved: "Approved", modified: "Amount changed and approved",
    rejected: isCapa ? "Tickets declined" : "Claim rejected", sent_back: "Sent back to the agents" };

  const submit = async () => {
    setBusy(true);
    try {
      await api.decide(c.claim_id, {
        role: w.role, decision: choice, approver: approver.trim() || roleLabel(w.role),
        amount: choice === "modified" ? amt : undefined, reason: reason.trim() || undefined,
      });
      notify(`${done[choice]}: ${c.claim_id}`);
      after();
    } catch (e) {
      notify((e as Error).message, true);
      setBusy(false);
    }
  };

  return (
    <section className="band signal" aria-label="Your decision">
      <h3>{roleLabel(w.role)} decision needed</h3>
      <p className="lede">
        {isCapa
          ? "The claim decision is made. Approve or decline the corrective action tickets raised from the patterns found."
          : <>The agents recommend <b>{PHRASE[rec.decision]}</b>
            {rec.decision !== "reject" && <> at <b>{money(rec.amount)}</b></>}
            {w.calc && <> of {money(w.calc.claimed_amount)} claimed</>}
            {rec.exception && <>, as a <b>policy exception</b></>}.
            {w.prior_decisions.length > 0 && <> Already approved by {w.prior_decisions.map((p) => roleLabel(p.role)).join(", ")}.</>}</>}
      </p>
      <div className="band-grid">
        <div>
          {isCapa ? (
            <ul>{capa.map((a, i) => <li key={i}><b>{a.params.title}</b><br /><span className="muted">{a.params.body}</span></li>)}</ul>
          ) : (
            <>
              <ul>
                {rec.reasons.slice(0, 6).map((r, i) => <li key={i}>{r.text}</li>)}
              </ul>
              {rec.exception && <p className="notice signal" style={{ background: "#fff" }}>{rec.exception_reason}</p>}
              <p className="muted" style={{ marginTop: 8, fontSize: 13 }}>
                On approval: {rec.actions.map((a) => a.type.replace(/_/g, " ")).join(", ")}.
                Full evidence is in the panels below.
              </p>
            </>
          )}
        </div>
        <div className="form-stack">
          <div className="choice" role="group" aria-label="Decision">
            {(isCapa ? (["approved", "rejected"] as Choice[]) : rec.decision === "reject"
              ? (["approved", "modified", "sent_back"] as Choice[])
              : (["approved", "modified", "rejected", "sent_back"] as Choice[])).map((k) => (
              <button key={k} aria-pressed={choice === k} onClick={() => { setChoice(k); if (k !== "approved") setTimeout(() => reasonRef.current?.focus(), 0); }}>
                {{ approved: isCapa ? "Approve" : rec.decision === "reject" ? "Confirm rejection" : "Approve",
                   modified: rec.decision === "reject" ? "Pay an amount instead" : "Change amount",
                   rejected: isCapa ? "Decline" : "Reject", sent_back: "Send back" }[k]}
              </button>
            ))}
          </div>
          {choice === "modified" && (
            <div className="field">
              <label htmlFor="amt">Amount to approve (₹)</label>
              <input id="amt" type="number" min={0} step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
            </div>
          )}
          {needsReason && (
            <div className="field">
              <label htmlFor="reason">{choice === "sent_back" ? "Question for the agents" : "Reason (kept as a precedent for similar claims)"}</label>
              <textarea id="reason" ref={reasonRef} value={reason} onChange={(e) => setReason(e.target.value)}
                placeholder={choice === "sent_back" ? "e.g. Check the hours reading against the telematics log" : ""} />
            </div>
          )}
          <div className="field">
            <label htmlFor="who">Your name</label>
            <input id="who" value={approver} onChange={(e) => setApprover(e.target.value)} placeholder={roleLabel(w.role)} />
          </div>
          <div>
            <button className="btn signal" disabled={!valid || busy} onClick={submit}>{busy ? "Sending..." : verb[choice]}</button>
          </div>
        </div>
      </div>
    </section>
  );
}

function ClaimantBand({ c, w, notify, after }: { c: ClaimDetail; w: WaitingInfo; notify: Notify; after: () => void }) {
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [showMsg, setShowMsg] = useState(false);
  useEffect(() => { setBusy(false); setFiles([]); }, [w.request.id]);
  const run = async (fn: () => Promise<unknown>, msg: string) => {
    setBusy(true);
    try { await fn(); notify(msg); after(); } catch (e) { notify((e as Error).message, true); setBusy(false); }
  };
  return (
    <section className="band signal" aria-label="Waiting on the claimant">
      <h3>Waiting on the claimant, request {w.request.round} of 2</h3>
      <p className="lede">The agents asked for evidence to resolve open findings. In this demo you answer for the claimant.</p>
      <div className="band-grid">
        <div>
          <ul>{w.request.items.map((i) => <li key={i}>{i}</li>)}</ul>
          <button className="link-btn" style={{ marginTop: 8 }} onClick={() => setShowMsg(!showMsg)}>
            {showMsg ? "Hide the message sent" : "Show the message sent"}
          </button>
          {showMsg && <div className="letter" style={{ marginTop: 8 }}>{w.request.message}</div>}
        </div>
        <div className="form-stack">
          {c.scripted_reply_available && (
            <div>
              <button className="btn signal" disabled={busy}
                onClick={() => run(() => api.reply(c.claim_id, { use_scripted: true }), "Claimant reply sent")}>
                Send the claimant's prepared reply
              </button>
              <p className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>Uses the documents prepared for this scenario.</p>
            </div>
          )}
          <div className="field">
            <label htmlFor="up">Or upload reply documents (PDF)</label>
            <input id="up" type="file" accept="application/pdf" multiple onChange={(e) => setFiles([...(e.target.files ?? [])])} />
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn" disabled={busy || !files.length}
              onClick={() => run(() => api.replyUpload(c.claim_id, files, ""), "Documents sent to the agents")}>
              Send uploaded documents
            </button>
            <button className="btn danger" disabled={busy}
              onClick={() => run(() => api.reply(c.claim_id, { no_response: true }), "Recorded: no response")}>
              Record no response
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

