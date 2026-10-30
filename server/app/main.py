"""FastAPI routes, the bounded agent loop and in-memory audit sessions.

Safety decisions (kept visible on purpose for teaching):
- The only AI access is through ai_service.extract_statement / choose_next_step.
- Everything the AI returns is re-validated (Pydantic) or re-grounded before use.
- The agent can only run allowlisted, deterministic Python tools, at most 6 steps.
- A drafted message always stops at `awaiting_approval`. Approval is a local
  simulation: nothing is ever sent, and `sent` stays False.
"""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from .ai_service import choose_next_step, extract_statement
from .models import AgentDecision, ApprovalRequest, AuditRequest, AuditSession, Transaction
from .sample_data import sample_transactions
from .tools import calculate_annual_leakage, detect_stealth_hikes, generate_cancellation_script

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_TEXT_CHARS = 100_000
MAX_STEPS = 6
ALLOWED_TOOLS = [
    "detect_stealth_hikes",
    "calculate_annual_leakage",
    "generate_cancellation_script",
    "finish",
]
NOT_CONFIGURED_PREFIX = "Gemini is not configured"

# In-memory only: cleared on restart. No database, no file storage.
SESSIONS: dict[str, AuditSession] = {}

app = FastAPI(
    title="Finance Intelligence Agent (Finwise)",
    version="1.0.0",
    description="Educational demo. Not financial advice. Never sends messages or moves money.",
)

_origins = [o.strip() for o in os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- helpers


def _gemini_configured() -> bool:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    return bool(key) and key != "replace_me"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(session: AuditSession, event_type: str, message: str, details: Optional[dict] = None) -> None:
    session.audit_log.append(
        {"time": _now(), "type": event_type, "message": message, "details": details or {}}
    )


def _summary(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "No subscription price hikes above 10% were found in the provided data."
    parts = []
    for f in findings:
        parts.append(
            f"{f['merchant']} increased from ${f['old_amount']:.2f} to ${f['new_amount']:.2f} "
            f"(+{f['change_pct']:.1f}%). Current annualized cost: ${f['new_amount'] * 12:.2f}."
        )
    return " ".join(parts)


def _match_finding(findings: list[dict[str, Any]], merchant: Any, amount: Any = None) -> Optional[dict]:
    """Ground planner arguments against real findings. Falls back to the first finding."""
    if not findings:
        return None
    for f in findings:
        if f["merchant"] != merchant:
            continue
        if amount is None:
            return f
        try:
            if abs(float(amount) - f["new_amount"]) < 0.001:
                return f
        except (TypeError, ValueError):
            pass
    return findings[0]


# --------------------------------------------------------------------------- routes


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "gemini_configured": _gemini_configured()}


@app.get("/api/sample")
def sample() -> dict:
    return {
        "transactions": [t.model_dump(mode="json") for t in sample_transactions()],
        "currency": "USD",
        "source": "fictional workshop sample",
    }


@app.post("/api/extract")
async def extract(
    text: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
) -> dict:
    clean_text = (text or "").strip()
    has_file = file is not None and bool(file.filename)
    if not clean_text and not has_file:
        raise HTTPException(status_code=400, detail="Paste transaction text or choose a file.")

    file_bytes: Optional[bytes] = None
    mime = "application/octet-stream"
    filename = ""
    if has_file:
        # Read at most limit+1 bytes so huge uploads are rejected without buffering them fully.
        file_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Please use a file smaller than 12 MB for this classroom demo.",
            )
        if not file_bytes:
            raise HTTPException(status_code=400, detail="The selected file is empty.")
        mime = file.content_type or "application/octet-stream"
        filename = (file.filename or "").lower()

    try:
        if file_bytes is not None and (mime == "text/csv" or filename.endswith(".csv")):
            # Python parses the table; the AI only normalises the rows.
            try:
                decoded = file_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded.")
            try:
                rows = list(csv.DictReader(io.StringIO(decoded)))
                if not rows:
                    raise ValueError("no data rows found")
            except (csv.Error, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"Could not read CSV: {exc}")
            prompt = "CSV rows (extract/normalize only; do not invent values):\n" + repr(rows)
            statement = extract_statement(text=prompt[:MAX_TEXT_CHARS])
        else:
            statement = extract_statement(
                text=clean_text[:MAX_TEXT_CHARS] or None,
                file_bytes=file_bytes,
                mime_type=mime if file_bytes is not None else None,
            )
    except HTTPException:
        raise
    except RuntimeError as exc:
        if str(exc).startswith(NOT_CONFIGURED_PREFIX):
            raise HTTPException(status_code=503, detail=str(exc))
        raise HTTPException(status_code=502, detail=f"Gemini extraction failed: {exc}")
    except Exception as exc:  # network, quota, bad JSON, schema mismatch...
        raise HTTPException(status_code=502, detail=f"Gemini extraction failed: {exc}")

    # Second validation layer: never trust AI output, even if it claims to match the schema.
    raw_items = getattr(statement, "transactions", None) or []
    notes = [str(n) for n in (getattr(statement, "notes", None) or [])]
    valid: list[dict] = []
    errors: list[dict] = []
    for index, item in enumerate(raw_items):
        try:
            data = item.model_dump() if hasattr(item, "model_dump") else item
            tx = Transaction.model_validate(data)
            valid.append(tx.model_dump(mode="json"))
        except ValidationError as exc:
            errors.append({"row": index + 1, "error": exc.errors()[0].get("msg", str(exc))})
        except Exception as exc:
            errors.append({"row": index + 1, "error": str(exc)})

    return {"transactions": valid, "notes": notes, "validation_errors": errors, "count": len(valid)}


@app.post("/api/audits")
def start_audit(request: AuditRequest) -> dict:
    if not request.transactions:
        raise HTTPException(status_code=400, detail="Add transactions before starting an audit.")

    session = AuditSession(goal=request.goal)
    _log(session, "started", "Audit started.", {"transaction_count": len(request.transactions)})
    SESSIONS[session.session_id] = session

    tool_results: list[dict[str, Any]] = []
    try:
        for _ in range(MAX_STEPS):  # hard cap on agent iterations
            session.steps += 1
            raw_decision = choose_next_step(session.goal, session.findings, tool_results, list(ALLOWED_TOOLS))
            # Planner output is untrusted: re-validate it (unknown tool names fail here).
            if isinstance(raw_decision, AgentDecision):
                decision = raw_decision
            else:
                try:
                    decision = AgentDecision.model_validate(raw_decision)
                except ValidationError:
                    raise ValueError("Tool is not allowed")
            tool = decision.tool_name
            args = decision.arguments or {}
            _log(session, "plan", decision.message or f"Selected {tool}.", {"tool": tool, "arguments": args})

            if tool not in ALLOWED_TOOLS:
                raise ValueError("Tool is not allowed")

            if tool == "finish":
                break

            if tool == "detect_stealth_hikes":
                result: Any = detect_stealth_hikes(request.transactions)
                session.findings = result

            elif tool == "calculate_annual_leakage":
                match = _match_finding(session.findings, args.get("merchant"), args.get("monthly_amount"))
                if match is None:
                    result = {"skipped": "No price hike found."}
                else:
                    # Computed from the grounded finding, never from raw model arguments.
                    result = calculate_annual_leakage(match["merchant"], match["new_amount"])

            elif tool == "generate_cancellation_script":
                match = _match_finding(session.findings, args.get("merchant"))
                if match is None:
                    result = {"skipped": "No price hike found."}
                else:
                    result = generate_cancellation_script(
                        match["merchant"], match["old_amount"], match["new_amount"]
                    )
                    session.pending_action = {
                        "type": "review_subscription_change",
                        "merchant": match["merchant"],
                        "old_amount": match["old_amount"],
                        "new_amount": match["new_amount"],
                        "change_pct": match["change_pct"],
                        "date": match["date"],
                        "annual_cost": round(match["new_amount"] * 12, 2),
                        "monthly_increase": match["monthly_increase"],
                        "draft": result["draft"],
                        "sent": False,
                    }

            else:  # defensive: unreachable while ALLOWED_TOOLS is checked above
                raise ValueError("Tool is not allowed")

            tool_results.append({"tool": tool, "result": result})
            details = result if isinstance(result, dict) else {"findings": result}
            _log(session, "tool", f"Executed {tool}.", details)

            if session.pending_action is not None:
                # Human-in-the-loop gate: stop here and wait. Nothing is sent.
                session.status = "awaiting_approval"
                session.summary = _summary(session.findings)
                _log(
                    session,
                    "approval_required",
                    "Draft ready. Waiting for human approval; nothing has been sent.",
                    {"merchant": session.pending_action["merchant"]},
                )
                SESSIONS[session.session_id] = session
                return session.model_dump(mode="json")

        session.status = "done"
        session.summary = _summary(session.findings)
        _log(session, "completed", "Audit completed.", {"steps": session.steps})
    except Exception as exc:
        session.status = "failed"
        session.pending_action = None
        session.summary = f"Audit stopped safely: {exc}"
        _log(session, "error", f"Audit stopped safely: {exc}", {})
    finally:
        SESSIONS[session.session_id] = session

    return session.model_dump(mode="json")


@app.get("/api/audits/{session_id}")
def get_audit(session_id: str) -> dict:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Audit session not found.")
    return session.model_dump(mode="json")


@app.post("/api/audits/{session_id}/approval")
def approve_audit(session_id: str, request: ApprovalRequest) -> dict:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Audit session not found. Start a new audit.")
    if session.status != "awaiting_approval" or not session.pending_action:
        raise HTTPException(status_code=409, detail="This audit has no pending approval.")

    action = session.pending_action
    if request.decision == "approve":
        session.status = "executing"
        # Simulation only: we record the decision locally. No email, no merchant contact.
        action["simulated_execution"] = "Approved and recorded locally; no external message was sent."
        action["approved"] = True
        _log(
            session,
            "approved_simulation",
            "Human approved. Action simulated locally; nothing was sent.",
            {"merchant": action.get("merchant"), "reason": request.reason},
        )
        session.status = "done"
        session.summary += " Demo action approved and simulated; no message was sent."
    else:
        action["approved"] = False
        _log(
            session,
            "rejected",
            "Human rejected the proposed action. Nothing was sent.",
            {"merchant": action.get("merchant"), "reason": request.reason},
        )
        session.status = "done"
        session.summary += " Proposed action rejected; nothing was sent."

    action["sent"] = False  # invariant: never sent
    SESSIONS[session_id] = session
    return session.model_dump(mode="json")
