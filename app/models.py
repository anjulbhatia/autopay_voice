from typing import Literal, Optional
from pydantic import BaseModel, Field

FailureReason = Literal["insufficient_balance", "mandate_expired", "bank_decline", "other"]
PaymentStatus = Literal["failed", "link_sent", "recovered", "handoff", "opted_out"]
CallOutcome = Literal["link_sent", "retry_scheduled", "handoff", "no_answer",
                      "wrong_person", "refused", "opted_out", "failed"]
LinkKind = Literal["pay_now", "update_mandate"]
LinkChannel = Literal["inapp", "console", "razorpay_notify", "whatsapp", "sms"]


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
