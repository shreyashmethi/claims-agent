// Typed client for the claims API (served under /api).

export type Status =
  | "RECEIVED" | "CLASSIFIED" | "PENDING_INFO" | "UNDER_REVIEW" | "RECOMMENDED" | "PENDING_APPROVAL"
  | "APPROVED" | "PARTIALLY_APPROVED" | "REJECTED" | "SETTLING" | "SETTLED" | "CLOSED" | "ESCALATED";

export interface ClaimRow {
  claim_id: string;
  type: string | null;
  claimant_id: string;
  claimant_name: string | null;
  amount: number | null;
  submitted_on: string;
  status: Status;
  outcome: string | null;
  settled_amount: number | null;
  events: number;
  waiting_role?: string | null;
}

export interface TraceEvent {
  claim_id: string;
  seq: number;
  ts: string;
  agent: string;
  type: string;
  summary: string;
  [k: string]: any;
}

export interface Finding {
  id: string;
  type: string;
  severity: "info" | "low" | "medium" | "high";
  description: string;
  evidence_ids: string[];
  needs: string[];
  resolved: boolean;
  resolution: string | null;
}

export interface Reason { text: string; evidence_ids: string[] }
export interface Action { type: string; params: Record<string, any> }
export interface Recommendation {
  decision: "approve" | "partial" | "reject" | "escalate";
  amount: number;
  reasons: Reason[];
  exception: boolean;
  exception_reason: string | null;
  actions: Action[];
  confidence: number;
}
export interface CalcLine { item: string; claimed: number; admitted: number; rule_id: string; note: string }
export interface Calc {
  claimed_amount: number; admissible_amount: number; lines: CalcLine[]; settlement_mode: string;
  rule_ids: string[]; params_version: string;
}
export interface RuleResult {
  rule_id: string; clause: string; passed: boolean; detail: string; waivable: boolean; waiver_basis: string | null;
}
export interface ApprovalRecord {
  role: string; decision: string; amount: number | null; reason: string; approver: string;
}
export interface InfoRequest { id: string; round: number; items: string[]; message: string; status: string }

export interface WaitingApproval {
  type: "approval"; claim_id: string; approval_id: string; role: string; recommendation: Recommendation;
  calc: Calc | null; findings: Finding[]; prior_decisions: ApprovalRecord[];
}
export interface WaitingInfo { type: "info_request"; claim_id: string; request: InfoRequest }
export type Waiting = WaitingApproval | WaitingInfo;

export interface Doc {
  doc_id: string; kind: string; fields: Record<string, string>; lines: any[]; evidence_id: string;
  round: number; filename: string; injection_hits?: string[];
}

export interface ClaimDetail {
  claim_id: string;
  status: Status;
  outcome: string | null;
  type: string | null;
  waiting_for: Waiting | null;
  plan: string[] | null;
  claim: {
    type?: string; type_confidence?: number; fields?: Record<string, string>; docs?: Record<string, Doc>;
    corrections?: Record<string, { value: string; previous: string; evidence_ids: string[]; reason: string }>;
    parties?: { claimant?: Record<string, any> };
  } | null;
  findings: Finding[] | null;
  completeness: { complete: boolean; missing: string[] } | null;
  eligibility: { eligible: boolean; with_exception: boolean; blocking: string[]; results: RuleResult[];
    explanation?: string } | null;
  duplicate: { is_duplicate: boolean; duplicate_of: string | null; repeat_claims: string[];
    repeat_valid: boolean | null; patterns: { kind: string; description: string; count: number }[];
    rationale: string } | null;
  calc: Calc | null;
  recommendation: Recommendation | null;
  verification: { passed: boolean; errors: string[] } | null;
  approval: { approval_id: string; required_roles: string[]; records: ApprovalRecord[]; status: string;
    final?: { decision: string; amount: number; approver_reason: string } } | null;
  actions: { action: string; status: string; ref?: string; amount?: number; [k: string]: any }[] | null;
  info_requests: InfoRequest[] | null;
  escalation: { reason: string; open_findings: Finding[] } | null;
  evidence_count: number;
  submitted_on: string;
  claimant_id: string;
  amount: number | null;
  settled_amount: number | null;
  flags: { turns: number; verify_failures: number; sent_back: number };
  scripted_reply_available: boolean;
}

export interface EvidenceItem { id: string; tool: string; agent: string; summary: string; hash: string; data?: any }
export interface Letter { msg_id: string; claim_id: string; recipient: string; subject: string; body: string;
  kind: string; ts: string }
export interface Sample { kind: string; label: string }

const BASE = "/api";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, init);
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* not json */ }
    throw new Error(detail);
  }
  return r.json();
}

const json = (body: unknown): RequestInit => ({
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

export const api = {
  health: () => req<{ ok: boolean; llm_provider: string }>("/health"),
  claims: () => req<ClaimRow[]>("/claims"),
  claim: (id: string) => req<ClaimDetail>(`/claims/${id}`),
  trace: (id: string, after = 0) => req<TraceEvent[]>(`/claims/${id}/trace?after=${after}`),
  evidence: (id: string) => req<EvidenceItem[]>(`/claims/${id}/evidence`),
  evidenceItem: (id: string, ev: string) => req<EvidenceItem>(`/claims/${id}/evidence?evidence_id=${ev}`),
  letters: (id: string) => req<Letter[]>(`/outbox?claim_id=${id}`),
  pendingApprovals: () => req<WaitingApproval[]>("/approvals/pending"),
  process: (id: string) => req(`/claims/${id}/process`, { method: "POST" }),
  reply: (id: string, body: { use_scripted?: boolean; no_response?: boolean }) =>
    req(`/claims/${id}/info-response`, json(body)),
  replyUpload: (id: string, files: File[], note: string) => {
    const f = new FormData();
    files.forEach((x) => f.append("files", x));
    f.append("note", note);
    return req(`/claims/${id}/info-response/upload`, { method: "POST", body: f });
  },
  decide: (id: string, body: { role: string; decision: string; amount?: number; reason?: string; approver: string }) =>
    req(`/claims/${id}/approval`, json(body)),
  seed: () => req<{ claims: string[] }>("/admin/seed", { method: "POST" }),
  samples: () => req<Sample[]>("/admin/samples"),
  createSample: (kind: string) => req<{ claim_id: string }>(`/admin/samples/${kind}`, { method: "POST" }),
  submit: (claimant: string, files: File[]) => {
    const f = new FormData();
    f.append("claimant_id", claimant);
    files.forEach((x) => f.append("files", x));
    return req<{ claim_id: string }>("/claims", { method: "POST", body: f });
  },
  docUrl: (id: string, doc: string) => `${BASE}/claims/${id}/documents/${doc}`,
};
