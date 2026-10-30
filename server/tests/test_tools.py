"""Tests for the deterministic tools and the offline (no-key) API flow.

Run from the backend folder:  pytest
"""

import os
import sys

# Make `app` importable when running `pytest` from the backend folder.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import main
from app.models import Transaction
from app.sample_data import sample_transactions
from app.tools import calculate_annual_leakage, detect_stealth_hikes, generate_cancellation_script


@pytest.fixture(autouse=True)
def offline_mode(monkeypatch):
    """Force demo mode so tests never depend on a real key."""
    monkeypatch.setenv("GEMINI_API_KEY", "replace_me")


@pytest.fixture
def client():
    return TestClient(main.app)


# ----------------------------------------------------------------- tools (spec 10.B)


def test_sample_contains_expected_price_hike():
    hikes = detect_stealth_hikes(sample_transactions())
    assert len(hikes) == 1
    hike = hikes[0]
    assert hike["merchant"] == "StreamBox Premium"
    assert hike["old_amount"] == 37.69
    assert hike["new_amount"] == 49.00
    assert hike["change_pct"] == 30.0
    assert hike["date"] == "2026-08-03"
    assert hike["monthly_increase"] == 11.31


def test_annual_cost():
    assert calculate_annual_leakage("StreamBox Premium", 49.0)["annual_cost"] == 588.0


def test_draft_is_not_sent():
    result = generate_cancellation_script("StreamBox Premium", 37.69, 49.0)
    assert result["sent"] is False
    assert "$11.31" in result["draft"]
    assert "$37.69" in result["draft"] and "$49.00" in result["draft"]


def test_flat_spotify_is_not_flagged():
    spotify = [t for t in sample_transactions() if t.merchant == "Spotify"]
    assert detect_stealth_hikes(spotify) == []


def test_exact_ten_percent_is_not_flagged():
    txs = [
        Transaction(merchant="Box", date="2026-01-01", category="Subscriptions", amount=10.00),
        Transaction(merchant="Box", date="2026-02-01", category="Subscriptions", amount=11.00),
    ]
    assert detect_stealth_hikes(txs) == []


def test_negative_monthly_amount_raises():
    with pytest.raises(ValueError):
        calculate_annual_leakage("X", -1)


def test_refunds_and_non_subscriptions_are_ignored():
    txs = [
        Transaction(merchant="Box", date="2026-01-01", category="Subscriptions", amount=10.00),
        Transaction(merchant="Box", date="2026-02-01", category="Subscriptions", amount=-10.00),
        Transaction(merchant="Cafe", date="2026-01-01", category="Dining", amount=5.00),
        Transaction(merchant="Cafe", date="2026-02-01", category="Dining", amount=50.00),
    ]
    assert detect_stealth_hikes(txs) == []


# ----------------------------------------------------------------- API (offline mode)


def _sample_payload():
    return {"transactions": main.sample()["transactions"]}


def test_health_reports_demo_mode(client):
    assert client.get("/api/health").json() == {"status": "ok", "gemini_configured": False}


def test_sample_endpoint(client):
    body = client.get("/api/sample").json()
    assert body["currency"] == "USD"
    assert len(body["transactions"]) == 14


def test_audit_happy_path_ac8_to_ac11(client):
    s = client.post("/api/audits", json=_sample_payload()).json()
    assert s["status"] == "awaiting_approval"
    assert s["steps"] == 3
    assert s["findings"] == [
        {
            "merchant": "StreamBox Premium",
            "old_amount": 37.69,
            "new_amount": 49.0,
            "change_pct": 30.0,
            "date": "2026-08-03",
            "monthly_increase": 11.31,
        }
    ]
    pa = s["pending_action"]
    assert pa["annual_cost"] == 588.0
    assert pa["sent"] is False
    for text in ("$37.69", "$49.00", "$11.31"):
        assert text in pa["draft"]

    trace = [(e["type"], e["details"].get("tool")) for e in s["audit_log"]]
    assert trace == [
        ("started", None),
        ("plan", "detect_stealth_hikes"),
        ("tool", None),
        ("plan", "calculate_annual_leakage"),
        ("tool", None),
        ("plan", "generate_cancellation_script"),
        ("tool", None),
        ("approval_required", None),
    ]
    assert s["summary"] == (
        "StreamBox Premium increased from $37.69 to $49.00 (+30.0%). Current annualized cost: $588.00."
    )


def test_approve_then_second_approval_conflicts(client):
    sid = client.post("/api/audits", json=_sample_payload()).json()["session_id"]
    r = client.post(f"/api/audits/{sid}/approval", json={"decision": "approve"})
    s = r.json()
    assert s["status"] == "done"
    assert s["pending_action"]["approved"] is True
    assert s["pending_action"]["sent"] is False
    assert s["pending_action"]["simulated_execution"]
    assert s["audit_log"][-1]["type"] == "approved_simulation"
    assert s["summary"].endswith("Demo action approved and simulated; no message was sent.")

    again = client.post(f"/api/audits/{sid}/approval", json={"decision": "approve"})
    assert again.status_code == 409


def test_reject(client):
    sid = client.post("/api/audits", json=_sample_payload()).json()["session_id"]
    s = client.post(f"/api/audits/{sid}/approval", json={"decision": "reject"}).json()
    assert s["pending_action"]["approved"] is False
    assert s["audit_log"][-1]["type"] == "rejected"
    assert s["summary"].endswith("Proposed action rejected; nothing was sent.")


def test_unknown_session(client):
    assert client.post("/api/audits/nope/approval", json={"decision": "approve"}).status_code == 404
    assert client.get("/api/audits/nope").status_code == 404


def test_no_hike_audit(client):
    spotify = [t for t in _sample_payload()["transactions"] if t["merchant"] == "Spotify"]
    s = client.post("/api/audits", json={"transactions": spotify}).json()
    assert s["status"] == "done"
    assert s["findings"] == []
    assert s["pending_action"] is None
    assert s["summary"] == "No subscription price hikes above 10% were found in the provided data."


def test_empty_audit_rejected(client):
    assert client.post("/api/audits", json={"transactions": []}).status_code == 400


def test_planner_disallowed_tool_fails_safely(client, monkeypatch):
    monkeypatch.setattr(main, "choose_next_step", lambda *a, **k: {"tool_name": "send_email"})
    s = client.post("/api/audits", json=_sample_payload()).json()
    assert s["status"] == "failed"
    assert s["summary"].startswith("Audit stopped safely:")
    assert s["pending_action"] is None


def test_planner_made_up_arguments_are_grounded(client, monkeypatch):
    from app.models import AgentDecision

    calls = iter(
        [
            AgentDecision(tool_name="detect_stealth_hikes"),
            AgentDecision(tool_name="calculate_annual_leakage", arguments={"merchant": "Fake", "monthly_amount": 9999}),
            AgentDecision(tool_name="generate_cancellation_script", arguments={"merchant": "Fake", "old_amount": 1, "new_amount": 2}),
        ]
    )
    monkeypatch.setattr(main, "choose_next_step", lambda *a, **k: next(calls))
    s = client.post("/api/audits", json=_sample_payload()).json()
    calc = [e for e in s["audit_log"] if e["type"] == "tool"][1]["details"]
    assert calc["annual_cost"] == 588.0
    assert s["pending_action"]["merchant"] == "StreamBox Premium"


# ----------------------------------------------------------------- extraction errors


def test_extract_without_inputs(client):
    assert client.post("/api/extract", data={}).status_code == 400


def test_extract_text_without_key_is_503(client):
    r = client.post("/api/extract", data={"text": "2026-09-14 TST* SPOTIFY USA 11.99"})
    assert r.status_code == 503
    assert r.json()["detail"].startswith("Gemini is not configured")


def test_extract_empty_file(client):
    r = client.post("/api/extract", files={"file": ("empty.txt", b"", "text/plain")})
    assert r.status_code == 400


def test_extract_too_large_file(client):
    big = b"x" * (13 * 1024 * 1024)
    r = client.post("/api/extract", files={"file": ("big.txt", big, "text/plain")})
    assert r.status_code == 413


def test_extract_csv_not_utf8(client):
    r = client.post("/api/extract", files={"file": ("a.csv", "naïve,x\n1,2".encode("latin-1"), "text/csv")})
    assert r.status_code == 400
    assert r.json()["detail"] == "CSV must be UTF-8 encoded."


def test_extract_csv_header_only(client):
    r = client.post("/api/extract", files={"file": ("a.csv", b"date,merchant,amount\n", "text/csv")})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Could not read CSV")


def test_extract_revalidates_ai_output(client, monkeypatch):
    from app.models import Statement

    class Fake:
        transactions = [
            {"merchant": "Spotify", "date": "2026-09-14", "category": "Subscriptions", "amount": 11.99},
            {"merchant": "", "date": "2026-09-14", "category": "Subscriptions", "amount": 1},
            {"merchant": "X", "date": None, "category": "NotACategory", "amount": 1},
        ]
        notes = ["one line had no date"]

    monkeypatch.setattr(main, "extract_statement", lambda **k: Fake())
    body = client.post("/api/extract", data={"text": "anything"}).json()
    assert body["count"] == 1
    assert [e["row"] for e in body["validation_errors"]] == [2, 3]
    assert body["notes"] == ["one line had no date"]
    assert Statement  # imported to show the schema in use


def test_main_does_not_import_ai_sdk():
    src = open(main.__file__, encoding="utf-8").read()
    assert "google" not in src and "genai" not in src
    for bad in ("smtplib", "requests", "stripe"):
        assert bad not in src
