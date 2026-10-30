"""Pydantic v2 schemas shared by the API, the agent loop and the AI boundary.

These models are the contract between untrusted AI output and the rest of the
app: anything the LLM returns is re-validated against them before it is used.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Category = Literal[
    "Dining",
    "Fuel",
    "Shopping",
    "Subscriptions",
    "Food delivery",
    "Income",
    "Bills",
    "Other",
]

ToolName = Literal[
    "detect_stealth_hikes",
    "calculate_annual_leakage",
    "generate_cancellation_script",
    "finish",
]

SessionStatus = Literal["analyzing", "awaiting_approval", "executing", "done", "failed"]

DEFAULT_GOAL = "Audit my transactions for subscription traps and silent price hikes."


class Transaction(BaseModel):
    """One validated transaction. Positive amount = charge, negative = refund/credit."""

    merchant: str = Field(..., min_length=1, max_length=120, description="Normalised merchant name")
    # Null when the source does not clearly show a date. Never guessed.
    date: Optional[dt.date] = Field(default=None, description="ISO YYYY-MM-DD, or null if unclear")
    category: Category = "Other"
    amount: float = Field(..., description="Numeric amount, no currency symbol")
    raw_line: Optional[str] = Field(default=None, description="Original source line")


class Statement(BaseModel):
    """Schema the AI extraction must return."""

    transactions: list[Transaction] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, description="Explains any uncertainty")


class AuditRequest(BaseModel):
    transactions: list[Transaction]
    goal: str = DEFAULT_GOAL


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: Optional[str] = None


class AgentDecision(BaseModel):
    """Planner output. Treated as untrusted: the backend re-checks it before acting."""

    tool_name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class AuditSession(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: SessionStatus = "analyzing"
    goal: str = DEFAULT_GOAL
    findings: list[dict[str, Any]] = Field(default_factory=list)
    pending_action: Optional[dict[str, Any]] = None
    audit_log: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    steps: int = 0
