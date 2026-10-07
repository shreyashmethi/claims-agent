import { useCallback, useEffect, useState } from "react";
import { api, type ClaimRow, type Sample, type WaitingApproval } from "./api";
import { ClaimView } from "./ClaimView";
import { DECISION_LABEL, money, roleLabel, STATUS_LABEL, typeLabel } from "./format";
import { usePoll } from "./hooks";

type View = { kind: "queue" } | { kind: "new" } | { kind: "claim"; id: string };

function parseHash(): View {
  const h = window.location.hash.replace(/^#\/?/, "");
  if (h.startsWith("claim/")) return { kind: "claim", id: h.slice(6) };
  if (h === "new") return { kind: "new" };
  return { kind: "queue" };
}
const go = (v: View) => {
  window.location.hash = v.kind === "claim" ? `/claim/${v.id}` : `/${v.kind}`;
};

export default function App() {
  const [view, setView] = useState<View>(parseHash);
  const [toast, setToast] = useState<{ msg: string; bad?: boolean } | null>(null);
  const [provider, setProvider] = useState("");
  const claims = usePoll(api.claims, 1500, []);
  useEffect(() => {
    const on = () => setView(parseHash());
    window.addEventListener("hashchange", on);
    api.health().then((h) => setProvider(h.llm_provider)).catch(() => {});
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const notify = useCallback((msg: string, bad?: boolean) => {
    setToast({ msg, bad });
    setTimeout(() => setToast(null), 3500);
  }, []);

  const rows = claims.data ?? [];
  const waiting = rows.filter((r) => r.status === "PENDING_APPROVAL").length;

  return (
    <div className="shell">
      <Sidebar rows={rows} view={view} waiting={waiting} provider={provider} error={claims.error}
        onReset={async () => {
          if (!window.confirm("Reset all demo data? Every claim goes back to its starting state.")) return;
          await api.seed();
          notify("Demo data reset");
          claims.refresh();
          go({ kind: "queue" });
        }} />
      <main className="main">
        {view.kind === "queue" && <Queue rows={rows} notify={notify} refresh={claims.refresh} />}
        {view.kind === "new" && <NewClaim notify={notify} refresh={claims.refresh} />}
        {view.kind === "claim" && <ClaimView id={view.id} notify={notify} onChanged={claims.refresh} />}
      </main>
      {toast && <div className={`toast ${toast.bad ? "bad" : ""}`} role="status">{toast.msg}</div>}
    </div>
  );
}

function bucket(r: ClaimRow): string {
  if (r.status === "PENDING_APPROVAL") return "you";
  if (r.status === "PENDING_INFO") return "claimant";
  if (r.status === "CLOSED" || r.status === "ESCALATED") return "done";
  if (r.events === 0) return "new";
  return "agents";
}
const GROUPS: [string, string][] = [
  ["you", "Waiting for your decision"],
  ["claimant", "Waiting on the claimant"],
  ["agents", "Agents working"],
  ["new", "Not started"],
  ["done", "Closed"],
];

function Sidebar({ rows, view, waiting, provider, error, onReset }: {
  rows: ClaimRow[]; view: View; waiting: number; provider: string; error: string | null; onReset: () => void;
}) {
  return (
    <aside className="side">
      <div className="brand">
        <h1>Claims desk</h1>
        <span className="provider" title="LLM provider configured on the server">
          {provider === "offline" ? "Offline planner" : provider ? `LLM: ${provider}` : ""}
        </span>
      </div>
      <nav className="side-nav">
        <button aria-current={view.kind === "queue" ? "page" : undefined} onClick={() => go({ kind: "queue" })}>
          Approvals{waiting > 0 && <span className="count">{waiting}</span>}
        </button>
        <button aria-current={view.kind === "new" ? "page" : undefined} onClick={() => go({ kind: "new" })}>
          New claim
        </button>
      </nav>
      <div className="side-list">
        {error && <p className="notice bad" style={{ margin: "12px 16px" }}>Can't reach the API: {error}</p>}
        {GROUPS.map(([key, label]) => {
          const list = rows.filter((r) => bucket(r) === key);
          if (!list.length) return null;
          return (
            <div key={key}>
              <div className="group-h"><span>{label}</span><span className="quiet">{list.length}</span></div>
              {list.map((r) => {
                const tone = key === "you" ? "signal" : r.status === "ESCALATED" ? "bad"
                  : r.outcome?.startsWith("settled") || r.outcome === "partially_settled" ? "good" : "";
                const st = key === "you" && r.waiting_role ? roleLabel(r.waiting_role)
                  : r.status === "CLOSED" ? outcomeLabel(r) : STATUS_LABEL[r.status];
                return (
                  <button key={r.claim_id} className={`claim-link ${key === "you" ? "waiting" : ""}`}
                    aria-current={view.kind === "claim" && view.id === r.claim_id}
                    onClick={() => go({ kind: "claim", id: r.claim_id })}>
                    <span className="id">{r.claim_id}</span>
                    <span className="amt">{r.amount == null ? "" : money(r.status === "CLOSED" && r.settled_amount != null ? r.settled_amount : r.amount)}</span>
                    <span className="sub">{r.type ? `${typeLabel(r.type)}, ` : ""}{r.claimant_name ?? r.claimant_id}</span>
                    <span className={`st ${tone}`}>{st}</span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>
      <div className="side-foot">
        Demo data is synthetic. <button onClick={onReset}>Reset demo data</button>
        {" "}or open the <a href="/api/docs" target="_blank" rel="noreferrer">API docs</a>.
      </div>
    </aside>
  );
}

function outcomeLabel(r: ClaimRow) {
  return ({ settled: "Paid in full", partially_settled: "Paid in part", rejected: "Rejected",
    rejected_duplicate: "Rejected, duplicate" } as Record<string, string>)[r.outcome ?? ""] ?? "Closed";
}

function Queue({ rows, notify, refresh }: { rows: ClaimRow[]; notify: (m: string, b?: boolean) => void; refresh: () => void }) {
  const pending = usePoll(api.pendingApprovals, 2000, []);
  const items = pending.data ?? [];
  const notStarted = rows.filter((r) => r.events === 0 && r.status === "RECEIVED");
  const [busy, setBusy] = useState(false);
  const startAll = async () => {
    setBusy(true);
    for (const r of notStarted) {
      try { await api.process(r.claim_id); } catch (e) { notify((e as Error).message, true); }
    }
    notify(`Started ${notStarted.length} claim${notStarted.length > 1 ? "s" : ""}`);
    refresh();
    setBusy(false);
  };
  return (
    <div className="page">
      <div className="page-h">
        <h2>Approvals</h2>
        <p>Agents investigate each claim and recommend a decision with evidence. Nothing is paid, replaced or sent
          until the people the approval matrix names have signed off here.</p>
      </div>

      {items.length === 0 ? (
        <div className="empty">
          <h3>No decisions waiting</h3>
          {notStarted.length > 0
            ? <>There {notStarted.length === 1 ? "is 1 claim" : `are ${notStarted.length} claims`} not yet started.
                Start them and recommendations will arrive here as the agents finish.</>
            : "Start a claim from the list, or create a new one."}
        </div>
      ) : (
        <div className="queue">
          {items.map((w) => <QueueItem key={w.claim_id} w={w} row={rows.find((r) => r.claim_id === w.claim_id)} />)}
        </div>
      )}

      {notStarted.length > 0 && (
        <>
          <h3 className="sub">Not started</h3>
          <div className="panel" style={{ marginTop: 10 }}>
            <div className="panel-b" style={{ paddingTop: 4 }}>
              {notStarted.map((r) => (
                <div className="sample" key={r.claim_id}>
                  <span><b>{r.claim_id}</b> <span className="muted">{r.claimant_name ?? r.claimant_id}, submitted {r.submitted_on}</span></span>
                  <button className="btn small" onClick={() => go({ kind: "claim", id: r.claim_id })}>Open</button>
                </div>
              ))}
            </div>
          </div>
          <div style={{ marginTop: 12 }}>
            <button className="btn primary" disabled={busy} onClick={startAll}>
              Start all {notStarted.length} claims
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function QueueItem({ w, row }: { w: WaitingApproval; row?: ClaimRow }) {
  const rec = w.recommendation;
  const open = w.findings.filter((f) => !f.resolved).length;
  return (
    <article className="q-item">
      <div>
        <h3>{w.claim_id}: {w.role === "quality_lead" ? "corrective action tickets" : DECISION_LABEL[rec.decision].toLowerCase()}</h3>
        <p className="why">
          {typeLabel(row?.type)}{row?.claimant_name ? ` from ${row.claimant_name}` : ""}.
          {" "}Waiting for the {roleLabel(w.role).toLowerCase()}
          {w.prior_decisions.length > 0 ? `, after ${w.prior_decisions.map((p) => roleLabel(p.role).toLowerCase()).join(" and ")}` : ""}.
          {rec.exception && " Needs a policy exception."}
          {open > 0 && ` ${open} open finding${open > 1 ? "s" : ""}.`}
        </p>
      </div>
      <div className="money">
        {rec.decision === "reject" ? <span style={{ color: "var(--bad)" }}>Reject</span> : money(rec.amount)}
        <small>{w.calc ? `of ${money(w.calc.claimed_amount)} claimed` : ""}</small>
      </div>
      <div style={{ gridColumn: "1 / -1" }}>
        <button className="btn signal small" onClick={() => go({ kind: "claim", id: w.claim_id })}>Review and decide</button>
      </div>
    </article>
  );
}

function NewClaim({ notify, refresh }: { notify: (m: string, b?: boolean) => void; refresh: () => void }) {
  const [samples, setSamples] = useState<Sample[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [claimant, setClaimant] = useState("D-014");
  const [files, setFiles] = useState<File[]>([]);
  useEffect(() => { api.samples().then(setSamples).catch(() => {}); }, []);
  const make = async (kind: string) => {
    setBusy(kind);
    try {
      const r = await api.createSample(kind);
      notify(`Created ${r.claim_id}; the agents have started`);
      refresh();
      go({ kind: "claim", id: r.claim_id });
    } catch (e) { notify((e as Error).message, true); setBusy(null); }
  };
  const upload = async () => {
    setBusy("upload");
    try {
      const r = await api.submit(claimant.trim(), files);
      notify(`Submitted ${r.claim_id}`);
      refresh();
      go({ kind: "claim", id: r.claim_id });
    } catch (e) { notify((e as Error).message, true); setBusy(null); }
  };
  return (
    <div className="page">
      <div className="page-h">
        <h2>New claim</h2>
        <p>Generate a claim with realistic documents for one of the situations below, or upload your own PDFs.
          The agents start as soon as it is created.</p>
      </div>
      <div className="cols">
        <section className="panel">
          <div className="panel-h"><h3>Generate a sample claim</h3><span className="meta">{samples.length} situations</span></div>
          <div className="panel-b">
            {samples.map((s) => (
              <div className="sample" key={s.kind}>
                <span>{s.label}</span>
                <button className="btn small" disabled={!!busy} onClick={() => make(s.kind)}>
                  {busy === s.kind ? "Creating..." : "Create"}
                </button>
              </div>
            ))}
          </div>
        </section>
        <section className="panel">
          <div className="panel-h"><h3>Upload documents</h3></div>
          <div className="panel-b form-stack" style={{ paddingTop: 14 }}>
            <div className="field">
              <label htmlFor="cl">Claimant ID</label>
              <input id="cl" value={claimant} onChange={(e) => setClaimant(e.target.value)} />
              <span className="quiet" style={{ fontSize: 12.5 }}>Dealers D-008 to D-063, customers C-310 to C-355, employees E-1042, E-1077, E-1103.</span>
            </div>
            <div className="field">
              <label htmlFor="files">Claim form and supporting documents (PDF)</label>
              <input id="files" type="file" accept="application/pdf" multiple onChange={(e) => setFiles([...(e.target.files ?? [])])} />
            </div>
            <p className="muted" style={{ fontSize: 13 }}>
              Documents are read as labelled fields ("Serial: EX-2231") with optional item lines. A claim missing
              required documents goes to the claimant with a request for them.
            </p>
            <div>
              <button className="btn primary" disabled={!files.length || !claimant.trim() || !!busy} onClick={upload}>
                {busy === "upload" ? "Submitting..." : "Submit claim"}
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
