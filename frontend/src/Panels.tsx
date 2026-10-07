import { useEffect, useState } from "react";
import { api, type ClaimDetail, type EvidenceItem, type Letter } from "./api";
import { ACTION_LABEL, DECISION_LABEL, humanize, money, roleLabel } from "./format";
import { EvChip, EvChips } from "./Timeline";

export type Tab = "decision" | "findings" | "evidence" | "documents" | "letters";

export function Panels({ c, tab, setTab, focusEv, onEvidence }: {
  c: ClaimDetail; tab: Tab; setTab: (t: Tab) => void; focusEv: string | null; onEvidence: (id: string) => void;
}) {
  const findings = (c.findings ?? []).filter((f) => f.severity !== "info");
  const docs = Object.keys(c.claim?.docs ?? {}).length;
  const tabs: [Tab, string, number | null][] = [
    ["decision", "Decision", null],
    ["findings", "Findings", findings.length],
    ["evidence", "Evidence", c.evidence_count],
    ["documents", "Documents", docs],
    ["letters", "Messages", null],
  ];
  return (
    <section className="panel sticky">
      <div className="tabs" role="tablist">
        {tabs.map(([k, label, n]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
            {label}{n != null && <span className="n">{n}</span>}
          </button>
        ))}
      </div>
      <div className="panel-b">
        {tab === "decision" && <Decision c={c} onEvidence={onEvidence} />}
        {tab === "findings" && <Findings c={c} onEvidence={onEvidence} />}
        {tab === "evidence" && <Evidence c={c} focus={focusEv} />}
        {tab === "documents" && <Documents c={c} onEvidence={onEvidence} />}
        {tab === "letters" && <Letters id={c.claim_id} status={c.status} />}
      </div>
    </section>
  );
}

function Decision({ c, onEvidence }: { c: ClaimDetail; onEvidence: (id: string) => void }) {
  const rec = c.recommendation;
  const final = c.approval?.final;
  const calc = c.calc;
  const elig = c.eligibility;
  return (
    <>
      {c.escalation && (
        <div className="section">
          <div className="verdict" style={{ color: "var(--bad)" }}>Escalated to a claims manager</div>
          <p className="verdict-sub">{c.escalation.reason}</p>
          {c.escalation.open_findings.length > 0 && (
            <ul className="reasons" style={{ marginTop: 8 }}>
              {c.escalation.open_findings.map((f) => (
                <li key={f.id}>{f.description} {f.needs.length > 0 && <span className="muted">Still needed: {f.needs.join("; ")}.</span>}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!rec && !final && !c.escalation && (
        <div className="section">
          <p className="muted">No recommendation yet. It appears here once eligibility, investigation and the
            duplicate check are done.</p>
        </div>
      )}

      {(rec || final) && (
        <div className="section">
          {final ? (
            <>
              <div className="verdict">{DECISION_LABEL[final.decision]}{final.decision !== "reject" && `: ${money(final.amount)}`}</div>
              <p className="verdict-sub">
                Final decision{calc ? ` on ${money(calc.claimed_amount)} claimed` : ""}.
                {final.approver_reason && ` Approver note: ${final.approver_reason}`}
              </p>
            </>
          ) : rec && (
            <>
              <div className="verdict">{DECISION_LABEL[rec.decision]}{rec.decision !== "reject" && rec.amount > 0 && `: ${money(rec.amount)}`}</div>
              <p className="verdict-sub">
                Recommended by the adjudication agent{calc ? ` on ${money(calc.claimed_amount)} claimed` : ""},
                confidence {Math.round(rec.confidence * 100)}%.
              </p>
            </>
          )}
          {rec?.exception && (
            <p className="notice signal">Needs a policy exception: {rec.exception_reason}</p>
          )}
          {c.verification && !c.verification.passed && (
            <p className="notice bad">Verifier rejected the last draft: {c.verification.errors.join("; ")}</p>
          )}
        </div>
      )}

      {calc && (
        <div className="section">
          <h4>Settlement calculation</h4>
          <table className="grid">
            <thead><tr><th>Item</th><th className="r">Claimed</th><th className="r">Allowed</th><th>Rule</th></tr></thead>
            <tbody>
              {calc.lines.map((l, i) => (
                <tr key={i}>
                  <td>{l.item}<div className="quiet" style={{ fontSize: 12 }}>{l.note}</div></td>
                  <td className="r">{money(l.claimed)}</td>
                  <td className={`r ${l.admitted < l.claimed ? "cut" : ""}`}>{money(l.admitted)}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{l.rule_id}</td>
                </tr>
              ))}
            </tbody>
            <tfoot><tr><td>Total</td><td className="r">{money(calc.claimed_amount)}</td>
              <td className="r">{money(calc.admissible_amount)}</td><td /></tr></tfoot>
          </table>
          <p className="quiet" style={{ fontSize: 12, marginTop: 6 }}>Computed by the rules engine, parameters {calc.params_version}.
            {final && Math.abs(final.amount - calc.admissible_amount) > 0.01 && ` The approver changed the amount to ${money(final.amount)}.`}</p>
        </div>
      )}

      {rec && rec.reasons.length > 0 && (
        <div className="section">
          <h4>Why</h4>
          <ul className="reasons">
            {rec.reasons.map((r, i) => <li key={i}>{r.text} <EvChips ids={r.evidence_ids} onOpen={onEvidence} /></li>)}
          </ul>
        </div>
      )}

      {c.approval && (
        <div className="section">
          <h4>Approvals</h4>
          <ol className="chain">
            {c.approval.required_roles.map((role) => {
              const r = c.approval!.records.filter((x) => x.role === role).slice(-1)[0];
              const waiting = c.waiting_for?.type === "approval" && c.waiting_for.role === role;
              const mark = r ? (r.decision === "rejected" ? "no" : "done") : waiting ? "now" : "";
              return (
                <li key={role}>
                  <span className={`mark ${mark}`} />
                  <span><b>{roleLabel(role)}</b>{" "}
                    {r ? <>{r.decision === "sent_back" ? "sent back" : r.decision}{r.amount != null && r.decision === "modified" ? ` to ${money(r.amount)}` : ""}
                      {r.reason && <span className="muted">: {r.reason}</span>} <span className="quiet">by {r.approver}</span></>
                      : waiting ? <span style={{ color: "var(--signal-ink)" }}>waiting for you</span> : <span className="quiet">next</span>}
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
      )}

      {(c.actions?.length ?? 0) > 0 && (
        <div className="section">
          <h4>Actions carried out</h4>
          <table className="grid">
            <tbody>
              {c.actions!.filter((a) => !a.action.startsWith("message:info")).map((a, i) => (
                <tr key={i}>
                  <td>{ACTION_LABEL[a.action] ?? a.action}</td>
                  <td>{a.ref ?? ""}</td>
                  <td className="r">{a.amount != null ? money(a.amount) : a.value != null ? money(a.value) : a.qty ? `${a.qty} x ${a.part_no}` : ""}</td>
                  <td className={a.status === "executed" ? "pass" : "fail"}>{a.status === "executed" ? "Done" : a.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rec && !final && rec.actions.length > 0 && (
        <div className="section">
          <h4>Will happen once approved</h4>
          <ul className="reasons">
            {rec.actions.map((a, i) => (
              <li key={i}>{ACTION_LABEL[a.type] ?? a.type}{a.params.amount ? ` of ${money(a.params.amount)}` : ""}
                {a.params.title ? `: ${a.params.title}` : ""}</li>
            ))}
          </ul>
        </div>
      )}

      {elig && (
        <div className="section">
          <h4>Eligibility rules</h4>
          <table className="grid">
            <tbody>
              {elig.results.map((r) => (
                <tr key={r.rule_id}>
                  <td style={{ width: 70 }}><b>{r.rule_id}</b></td>
                  <td>{r.clause.replace(/^.*§[\d.]+\s*/, "")}<div className="quiet" style={{ fontSize: 12 }}>{r.detail}</div></td>
                  <td className={r.passed ? "pass" : r.waivable ? "waive" : "fail"} style={{ whiteSpace: "nowrap" }}>
                    {r.passed ? "Pass" : r.waivable ? "Exception" : "Fail"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {c.duplicate && (
        <div className="section">
          <h4>Duplicates and patterns</h4>
          <p style={{ fontSize: 13 }}>
            {c.duplicate.is_duplicate ? <b className="fail">Duplicate of {c.duplicate.duplicate_of}. </b>
              : c.duplicate.repeat_valid ? <b>Valid repeat claim. </b> : "No duplicate found. "}
          </p>
          {c.duplicate.patterns.length > 0 && (
            <ul className="reasons" style={{ marginTop: 6 }}>
              {c.duplicate.patterns.map((p, i) => <li key={i}>{p.description}</li>)}
            </ul>
          )}
        </div>
      )}
    </>
  );
}

function Findings({ c, onEvidence }: { c: ClaimDetail; onEvidence: (id: string) => void }) {
  const list = (c.findings ?? []).filter((f) => f.severity !== "info");
  if (!list.length) return <p className="muted" style={{ padding: "14px 0" }}>No findings yet.</p>;
  const open = list.filter((f) => !f.resolved);
  return (
    <>
      <p className="muted" style={{ fontSize: 13, padding: "10px 0 4px" }}>
        {open.length ? `${open.length} open, ${list.length - open.length} resolved.` : `All ${list.length} resolved.`}
      </p>
      {[...open, ...list.filter((f) => f.resolved)].map((f) => (
        <div className="finding" key={f.id + f.type}>
          <span className={`sev ${f.severity}`}>{f.severity === "high" ? "High" : f.severity === "medium" ? "Medium" : "Low"}</span>
          <div>
            <div className="t">{humanize(f.type)}</div>
            <p className="d">{f.description} <EvChips ids={f.evidence_ids} onOpen={onEvidence} /></p>
            {f.resolved ? (f.resolution && <p className="res">Resolved: {f.resolution}</p>)
              : f.needs.length > 0 && <p className="open">Needs from the claimant: {f.needs.join("; ")}</p>}
          </div>
        </div>
      ))}
    </>
  );
}

function Evidence({ c, focus }: { c: ClaimDetail; focus: string | null }) {
  const [items, setItems] = useState<EvidenceItem[]>([]);
  const [open, setOpen] = useState<string | null>(focus);
  const [detail, setDetail] = useState<Record<string, EvidenceItem>>({});
  useEffect(() => { api.evidence(c.claim_id).then(setItems).catch(() => {}); }, [c.claim_id, c.evidence_count]);
  useEffect(() => { if (focus) setOpen(focus); }, [focus]);
  useEffect(() => {
    if (open && !detail[open]) api.evidenceItem(c.claim_id, open).then((d) => setDetail((x) => ({ ...x, [open]: d }))).catch(() => {});
    if (open) document.getElementById(`ev-${open}`)?.scrollIntoView({ block: "nearest" });
  }, [open, c.claim_id, detail, items.length]);
  if (!items.length) return <p className="muted" style={{ padding: "14px 0" }}>The evidence ledger is empty.</p>;
  return (
    <>
      <p className="muted" style={{ fontSize: 13, padding: "10px 0 4px" }}>
        Every tool result the agents read is recorded here with a hash. Findings and reasons cite these IDs.
      </p>
      {items.map((e) => (
        <div key={e.id} id={`ev-${e.id}`}>
          <button className="ev-row" aria-expanded={open === e.id} onClick={() => setOpen(open === e.id ? null : e.id)}>
            <span className="id">{e.id}</span>
            <span><span className="s">{e.summary}</span><br /><span className="by">read by {e.agent.replace("_", " ")}, hash {e.hash}</span></span>
          </button>
          {open === e.id && (
            <pre className="json">{detail[e.id] ? JSON.stringify(detail[e.id].data, null, 2) : "Loading..."}</pre>
          )}
        </div>
      ))}
    </>
  );
}

function Documents({ c, onEvidence }: { c: ClaimDetail; onEvidence: (id: string) => void }) {
  const docs = Object.values(c.claim?.docs ?? {});
  const corr = c.claim?.corrections ?? {};
  if (!docs.length) return <p className="muted" style={{ padding: "14px 0" }}>No documents read yet.</p>;
  return (
    <>
      {Object.entries(corr).map(([field, x]) => (
        <p key={field} className="notice good">
          Corrected {field} from {x.previous} to <b>{x.value}</b>: {x.reason} <EvChips ids={x.evidence_ids} onOpen={onEvidence} />
        </p>
      ))}
      {docs.map((d) => (
        <div className="doc" key={d.doc_id}>
          <div className="doc-h">
            <span><span className="fn">{humanize(d.kind)}</span> <span className="quiet">{d.filename}{d.round ? `, claimant reply ${d.round}` : ""}</span></span>
            <span><EvChip id={d.evidence_id} onOpen={onEvidence} /> <a href={api.docUrl(c.claim_id, d.doc_id)} target="_blank" rel="noreferrer">Open PDF</a></span>
          </div>
          {d.injection_hits && (
            <p className="notice bad">Contains instruction-like text, treated as data and not acted on: "{d.injection_hits.join('", "')}"</p>
          )}
          <dl className="kv">
            {Object.entries(d.fields).map(([k, v]) => <FieldRow key={k} k={k} v={v} />)}
          </dl>
          {d.lines.length > 0 && (
            <table className="grid" style={{ marginTop: 8 }}>
              <thead><tr><th>Line</th><th className="r">Qty</th><th className="r">Rate</th><th className="r">Amount</th></tr></thead>
              <tbody>{d.lines.map((l: any, i: number) => (
                <tr key={i}><td>{l.description}</td><td className="r">{l.qty}</td><td className="r">{money(l.rate)}</td><td className="r">{money(l.amount)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </div>
      ))}
    </>
  );
}

function FieldRow({ k, v }: { k: string; v: string }) {
  return <><dt>{humanize(k)}</dt><dd>{v}</dd></>;
}

function Letters({ id, status }: { id: string; status: string }) {
  const [list, setList] = useState<Letter[]>([]);
  useEffect(() => { api.letters(id).then(setList).catch(() => {}); }, [id, status]);
  if (!list.length) return <p className="muted" style={{ padding: "14px 0" }}>Nothing has been sent to the claimant yet.</p>;
  return (
    <>
      {list.map((m) => (
        <div className="section" key={m.msg_id}>
          <h4>{m.kind === "info_request" ? "Information request" : "Decision letter"}
            <span className="quiet" style={{ fontWeight: 400 }}> to {m.recipient}</span></h4>
          <div className="letter">{m.body}</div>
        </div>
      ))}
    </>
  );
}
