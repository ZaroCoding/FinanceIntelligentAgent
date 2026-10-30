"""Fictional workshop sample data (USD). Not real people, accounts or merchants."""

from __future__ import annotations

from .models import Transaction

_ROWS = [
    ("Campus Cafe", "2026-06-05", "Dining", 8.50, "SQ *CAMPUS CAFE 8.50"),
    ("StreamBox Premium", "2026-06-03", "Subscriptions", 37.69, "STREAMBOX*PREM 37.69"),
    ("Groceries Market", "2026-06-08", "Shopping", 42.10, "POS GROCERIES MARKET 42.10"),
    ("Spotify", "2026-06-14", "Subscriptions", 11.99, "TST* SPOTIFY USA 11.99"),
    ("StreamBox Premium", "2026-07-03", "Subscriptions", 37.69, "STREAMBOX*PREM 37.69"),
    ("Campus Cafe", "2026-07-06", "Dining", 9.25, "SQ *CAMPUS CAFE 9.25"),
    ("Spotify", "2026-07-14", "Subscriptions", 11.99, "TST* SPOTIFY USA 11.99"),
    ("StreamBox Premium", "2026-08-03", "Subscriptions", 49.00, "STREAMBOX*PREM 49.00"),
    ("Groceries Market", "2026-08-08", "Shopping", 39.40, "POS GROCERIES MARKET 39.40"),
    ("Spotify", "2026-08-14", "Subscriptions", 11.99, "TST* SPOTIFY USA 11.99"),
    ("Fuel Station", "2026-08-17", "Fuel", 25.00, "POS FUEL STATION 25.00"),
    ("StreamBox Premium", "2026-09-03", "Subscriptions", 49.00, "STREAMBOX*PREM 49.00"),
    ("Spotify", "2026-09-14", "Subscriptions", 11.99, "TST* SPOTIFY USA 11.99"),
    ("Food Delivery", "2026-09-18", "Food delivery", 18.75, "UPI/FOOD DELIVERY/8231 18.75"),
]


def sample_transactions() -> list[Transaction]:
    """Return the 14 fictional, already-validated sample transactions."""
    return [
        Transaction(merchant=m, date=d, category=c, amount=a, raw_line=r)
        for (m, d, c, a, r) in _ROWS
    ]
