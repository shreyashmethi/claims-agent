import type { Status } from "./api";

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 0 });

export const money = (n: number | null | undefined) => (n == null ? "–" : `₹${inr.format(n)}`);

export const TYPE_LABEL: Record<string, string> = {
  warranty: "Warranty",
  parts_replacement: "Parts replacement",
  transit_damage: "Transit damage",
  dealer_reimbursement: "Dealer reimbursement",
  employee_reimbursement: "Employee expenses",
};
export const typeLabel = (t: string | null | undefined) => (t ? TYPE_LABEL[t] ?? t : "Not yet classified");

export const STATUS_LABEL: Record<Status, string> = {
  RECEIVED: "Received",
  CLASSIFIED: "Classified",
  PENDING_INFO: "Waiting on claimant",
  UNDER_REVIEW: "Agents reviewing",
  RECOMMENDED: "Recommended",
  PENDING_APPROVAL: "Needs approval",
  APPROVED: "Approved",
  PARTIALLY_APPROVED: "Partly approved",
  REJECTED: "Rejected",
  SETTLING: "Settling",
  SETTLED: "Settled",
  CLOSED: "Closed",
  ESCALATED: "Escalated",
};

export const ROLE_LABEL: Record<string, string> = {
  claims_officer: "Claims officer",
  claims_manager: "Claims manager",
  finance_controller: "Finance controller",
  reporting_manager: "Reporting manager",
  quality_lead: "Quality lead",
};
export const roleLabel = (r: string) => ROLE_LABEL[r] ?? r;

export const AGENT_LABEL: Record<string, string> = {
  case_manager: "Case manager",
  intake: "Intake",
  completeness: "Completeness",
  policy: "Policy",
  investigation: "Investigation",
  duplicate_pattern: "Duplicates and patterns",
  adjudication: "Adjudication",
  verifier: "Verifier",
  settlement: "Settlement",
  system: "System",
  claimant: "Claimant",
};
export const agentLabel = (a: string) =>
  AGENT_LABEL[a] ?? (a.includes(":") ? roleLabel(a.split(":")[1]) : roleLabel(a));

export const NEXT_LABEL: Record<string, string> = {
  ...AGENT_LABEL,
  request_info: "Ask the claimant",
  route_approval: "Route for approval",
  escalate: "Escalate",
  end: "Close",
};

export const DECISION_LABEL: Record<string, string> = {
  approve: "Approve in full",
  partial: "Approve in part",
  reject: "Reject",
  escalate: "Escalate",
};

export const ACTION_LABEL: Record<string, string> = {
  payment: "Payment",
  credit_note: "Credit note",
  replacement_order: "Replacement order",
  carrier_recovery: "Carrier recovery claim",
  capa_ticket: "Corrective action ticket",
  decision_letter: "Decision letter",
};

export const humanize = (s: string) => {
  const t = s.replace(/^rule_failed:/, "Rule failed: ").replace(/_/g, " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
};

export const time = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export const DONE: Status[] = ["CLOSED", "ESCALATED", "SETTLED", "REJECTED"];
export const isActive = (s: Status) => !DONE.includes(s) && s !== "PENDING_APPROVAL" && s !== "PENDING_INFO";
