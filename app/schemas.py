"""Typed contracts between agents. Every agent output is validated against one of these."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ClaimType(str, Enum):
    WARRANTY = "warranty"
    PARTS = "parts_replacement"
    TRANSIT = "transit_damage"
    DEALER = "dealer_reimbursement"
    EMPLOYEE = "employee_reimbursement"


class Status(str, Enum):
    RECEIVED = "RECEIVED"
    CLASSIFIED = "CLASSIFIED"
    PENDING_INFO = "PENDING_INFO"
    UNDER_REVIEW = "UNDER_REVIEW"
    RECOMMENDED = "RECOMMENDED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    REJECTED = "REJECTED"
    SETTLING = "SETTLING"
    SETTLED = "SETTLED"
    CLOSED = "CLOSED"
    ESCALATED = "ESCALATED"


Severity = Literal["info", "low", "medium", "high"]


class Evidence(BaseModel):
    id: str
    tool: str
    agent: str
    summary: str
    data: Any = None
    hash: str


class Finding(BaseModel):
    id: str = ""
    type: str = Field(description="snake_case finding type, e.g. serial_mismatch, amount_mismatch, duplicate_submission")
    severity: Severity
    description: str
    evidence_ids: list[str] = Field(min_length=1, description="Evidence ledger IDs (EV-###) that support this finding")
    needs: list[str] = Field(default_factory=list, description="Evidence items still required from the claimant")
    resolved: bool = False
    resolution: str | None = None
    source_check: str | None = Field(default=None, description="Named check that raised this finding, if any")


class RuleResult(BaseModel):
    rule_id: str
    clause: str
    passed: bool
    detail: str
    waivable: bool = False
    waiver_basis: str | None = None


class EligibilityResult(BaseModel):
    eligible: bool
    with_exception: bool = False
    results: list[RuleResult]
    blocking: list[str] = []
    evidence_id: str | None = None


class CalcLine(BaseModel):
    item: str
    claimed: float
    admitted: float
    rule_id: str
    note: str = ""


class SettlementCalc(BaseModel):
    claimed_amount: float
    admissible_amount: float
    lines: list[CalcLine]
    settlement_mode: Literal["credit_note", "payment", "replacement", "none"]
    rule_ids: list[str]
    params_version: str
    evidence_id: str | None = None


class PatternFinding(BaseModel):
    kind: Literal["recurring_unit_failure", "batch_failure", "dealer_duplicate_history"]
    description: str
    count: int
    units: list[str] = []
    dealers: list[str] = []


class DuplicateAssessment(BaseModel):
    is_duplicate: bool
    duplicate_of: str | None = None
    repeat_claims: list[str] = []
    repeat_valid: bool | None = None
    patterns: list[PatternFinding] = []
    rationale: str
    evidence_ids: list[str] = []


class Reason(BaseModel):
    text: str
    evidence_ids: list[str] = Field(description="Evidence ledger IDs that support this reason")


ActionType = Literal["payment", "credit_note", "replacement_order", "carrier_recovery", "capa_ticket",
                     "decision_letter"]


class ProposedAction(BaseModel):
    type: ActionType
    params: dict[str, Any] = {}


class Recommendation(BaseModel):
    decision: Literal["approve", "partial", "reject", "escalate"]
    amount: float = Field(description="Admissible amount; must equal the rules-engine calculation")
    reasons: list[Reason]
    exception: bool = False
    exception_reason: str | None = None
    actions: list[ProposedAction] = []
    confidence: float = Field(ge=0, le=1, default=0.9)


class Verification(BaseModel):
    passed: bool
    errors: list[str] = []


class InfoRequest(BaseModel):
    id: str
    round: int
    items: list[str]
    message: str
    status: Literal["open", "answered", "expired"] = "open"


class ApprovalDecision(BaseModel):
    role: str
    decision: Literal["approved", "modified", "rejected", "sent_back"]
    amount: float | None = None
    reason: str = ""
    approver: str = "approver"


class NextAction(BaseModel):
    agent: str = Field(description="Next node to run; must be one of the allowed options")
    goal: str = Field(description="What that agent should achieve on this turn")
    rationale: str = Field(description="Why this is the best next step given the case state")
    remaining_plan: list[str] = Field(default_factory=list, description="Your expected remaining steps")
