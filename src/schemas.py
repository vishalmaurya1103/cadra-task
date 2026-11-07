"""Request/response models for the Larkfield support-agent API (pydantic v2)."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

TicketType = Literal["REFUND", "RETURN", "ORDER_STATUS", "DELIVERY_ISSUE", "OTHER"]
Outcome = Literal[
    "PROCEED",
    "PROCEED_MODIFIED",
    "PEND",
    "ESCALATE",
    "REJECT",
    "INVALID_REQUEST",
    "UNABLE_TO_VERIFY",
]
CheckResult = Literal["PASS", "FAIL", "NOT_APPLICABLE", "UNABLE_TO_VERIFY"]
ApproverRole = Optional[Literal["SUPPORT_LEAD", "TRUST_SAFETY", "CARRIER_CLAIMS"]]


class Attachment(BaseModel):
    file_name: str = ""
    content_type: str = ""
    content_base64: str = ""


class Ticket(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    ticket_id: str
    received_at: str
    channel: str = "email"
    from_: str = Field(alias="from")
    to: str = ""
    subject: str = ""
    body: str = ""
    attachments: list[Attachment] = Field(default_factory=list)


class Resolved(dict):
    pass


class Check(dict):
    pass


class DraftReply(dict):
    pass


class Trace(dict):
    pass


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    approver: str = ""


class LedgerEntry(dict):
    pass


class Run(dict):
    """Documented added fields: amount_usd, run_status, duplicate_of, decision, latency_ms."""
