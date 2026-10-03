"""Pydantic types mirroring the SQLite CHECK constraints in app/db.py."""
from typing import Literal, Optional
from pydantic import BaseModel, Field

FailureReason = Literal["insufficient_balance", "mandate_expired", "bank_decline", "other"]
PaymentStatus = Literal["failed", "link_sent", "recovered", "handoff", "opted_out"]
CallMode = Literal["web", "phone", "sim"]
Tier = Literal["short", "standard", "extended"]
CallOutcome = Literal["link_sent", "retry_scheduled", "handoff", "no_answer",
                      "wrong_person", "refused", "opted_out", "failed"]
LinkKind = Literal["pay_now", "update_mandate"]
LinkChannel = Literal["inapp", "console", "razorpay_notify", "whatsapp", "sms"]
HandoffStatus = Literal["open", "in_progress", "done"]
AuditStatus = Literal["ok", "refused", "error"]


class CustomerSeed(BaseModel):
    """One record from data/customers.json (all synthetic)."""
    customer_id: str
    name: str
    gender: Optional[str] = None
    phone: str
    amount_due: float
    due_date: Optional[str] = None
    failure_reason: FailureReason
    payment_status: PaymentStatus = "failed"
    default_history: int = 0
    last_call_at: Optional[str] = None
    last_message_at: Optional[str] = None
    attempts_total: int = 0
    past_call_notes: Optional[str] = None
    security_question: Optional[str] = None
    security_answer_hash: Optional[str] = None
    security_salt: Optional[str] = None
    do_not_call: int = Field(default=0, ge=0, le=1)


class CallCreate(BaseModel):
    customer_id: str
    vapi_call_id: Optional[str] = None
    mode: CallMode = "web"
    tier: Optional[Tier] = None
    p_pay: Optional[float] = None


class CallOutcomeUpdate(BaseModel):
    outcome: CallOutcome
    retry_at: Optional[str] = None
    transcript: Optional[str] = None
    judge_json: Optional[str] = None


class PaymentLinkCreate(BaseModel):
    token: str
    customer_id: str
    call_id: Optional[int] = None
    kind: LinkKind = "pay_now"
    channel: LinkChannel = "console"
    created_at: str
    expires_at: str


class HandoffCreate(BaseModel):
    customer_id: str
    call_id: Optional[int] = None
    reason: str
    notes: Optional[str] = None
    created_at: str


class AuditEntry(BaseModel):
    call_id: Optional[int] = None
    actor: Optional[str] = None
    tool: str
    args_masked: Optional[str] = None
    status: AuditStatus
