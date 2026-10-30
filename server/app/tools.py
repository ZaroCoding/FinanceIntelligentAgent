"""Deterministic, read-only finance tools used by the agent.

Safety notes:
- All money arithmetic happens here in Python, never in the LLM.
- None of these functions send anything, call the network, or change state.
- generate_cancellation_script only returns a DRAFT with sent=False.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any, Iterable

from .models import Transaction


def detect_stealth_hikes(
    transactions: Iterable[Transaction], threshold: float = 0.10
) -> list[dict[str, Any]]:
    """Find consecutive subscription charges from the same merchant that rose by more than `threshold`."""
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.category == "Subscriptions" and tx.amount > 0:
            groups[tx.merchant.strip().casefold()].append(tx)

    findings: list[dict[str, Any]] = []
    for charges in groups.values():
        # Null dates sort first so they never hide a later, dated hike.
        charges.sort(key=lambda t: t.date or dt.date.min)
        for prev, cur in zip(charges, charges[1:]):
            if prev.amount <= 0:
                continue
            change = (cur.amount - prev.amount) / prev.amount
            if change > threshold:  # strictly greater: exactly 10% is not flagged
                findings.append(
                    {
                        "merchant": cur.merchant,
                        "old_amount": round(prev.amount, 2),
                        "new_amount": round(cur.amount, 2),
                        "change_pct": round(change * 100, 1),
                        "date": cur.date.isoformat() if cur.date else None,
                        "monthly_increase": round(cur.amount - prev.amount, 2),
                    }
                )
    return findings


def calculate_annual_leakage(merchant: str, monthly_amount: float) -> dict[str, Any]:
    """Annualise a monthly subscription cost."""
    if monthly_amount < 0:
        raise ValueError("monthly_amount must not be negative.")
    return {
        "merchant": merchant,
        "monthly_amount": round(monthly_amount, 2),
        "annual_cost": round(monthly_amount * 12, 2),
    }


def generate_cancellation_script(merchant: str, old_amount: float, new_amount: float) -> dict[str, Any]:
    """Draft a review/cancellation message. Draft only: this function never sends anything."""
    increase = round(new_amount - old_amount, 2)
    draft = (
        "Subject: Request to review subscription price change\n"
        "\n"
        f"Hello {merchant} Support,\n"
        "\n"
        f"I noticed my recurring subscription changed from ${old_amount:.2f} to ${new_amount:.2f}. "
        "Please confirm when and how this price change was communicated. "
        f"I request a review of the ${increase:.2f} increase and information about cancellation "
        "options and any applicable refund policy.\n"
        "\n"
        "Thank you."
    )
    return {"merchant": merchant, "draft": draft, "sent": False}
