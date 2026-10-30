# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

"Finwise": a classroom demo (React + Vite client, FastAPI server) with two AI patterns on fictional finance data: LLM extraction of transactions (Gemini) and a bounded audit agent that finds subscription price hikes and drafts a dispute message behind a human approval gate. It never sends anything; `sent` is always `False`.

**The README is stale on paths:** it says `backend/` and `frontend/`, but the real directories are `server/` and `client/`. Use `server/` and `client/` in all commands. `.env.example` (mentioned in the README) is not in the repo; create `server/.env` by hand (see Configuration).

## Commands

Server (run from `server/`; Python 3.11+):

```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000     # API docs at http://localhost:8000/docs
pytest                                        # all tests
pytest tests/test_tools.py::test_reject       # single test
```

Client (run from `client/`; Node 20+):

```bash
npm install
npm run dev      # http://localhost:5173 (vite --host 0.0.0.0)
npm run build
```

There is no linter or formatter configured. Run `pytest` from inside `server/` (the test file also adds the parent dir to `sys.path`).

## Configuration

`server/.env` (git-ignored, loaded by `main.py` and `ai_service.py` from the `server/` dir regardless of cwd): `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-2.5-flash`), `FRONTEND_ORIGIN` (comma-separated CORS origins; must match the exact URL you open, `localhost` vs `127.0.0.1` matters). A missing key or the literal `replace_me` means **demo mode**. The client reads `VITE_API_URL` (default `http://localhost:8000`).

## Architecture

**The AI boundary is `server/app/ai_service.py`.** It is the only file that may touch the LLM; `main.py` imports just `extract_statement` and `choose_next_step`, and a test (`test_main_does_not_import_ai_sdk`) fails if `main.py` mentions `google`, `genai`, `smtplib`, `requests` or `stripe`. Keep it that way.

- The Gemini integration is intentionally **left as `TODO(manual)` stubs** (`_client()`, `extract_statement()`, the LLM branch of `choose_next_step()`) for the user to hand-code. Don't implement them unless asked. Until then `extract_statement` raises `RuntimeError("Gemini is not configured…")`, which `/api/extract` maps to 503 (other failures map to 502).
- `choose_next_step` falls back to `_offline_plan`, a deterministic planner, so the full audit works with no key. It is used even when a key is set until the LLM branch is implemented.

**Agent loop (`start_audit` in `main.py`)** treats all AI output as untrusted:
1. Max 6 iterations (`MAX_STEPS`); tool names are checked against `ALLOWED_TOOLS` (also enforced by the `ToolName` Literal in `models.AgentDecision`). Anything else sets status `failed` with "Audit stopped safely: …".
2. Planner arguments are **grounded** through `_match_finding`: tool inputs come from real `detect_stealth_hikes` findings, never from model-supplied values (a hallucinated merchant/amount falls back to the first finding).
3. Once `generate_cancellation_script` sets `pending_action`, the loop returns early with status `awaiting_approval`. `POST /api/audits/{id}/approval` then records approve/reject locally and re-asserts `sent = False`.
4. Every step is appended to `session.audit_log` (the UI renders it as a timeline; tests assert the exact event sequence).

**Extraction (`/api/extract`)**: CSV files are parsed in Python and only the rows go to the model; other files go as raw bytes + MIME type. The result is re-validated row by row with `Transaction.model_validate`; bad rows go to `validation_errors` instead of the dashboard. Upload cap is 12 MB, text cap 100k chars.

**Maths lives in code, not the LLM:** hike/annual-cost logic is in `server/app/tools.py` (pure, deterministic; a hike is strictly > 10%, only positive `Subscriptions` charges, null dates sort first); dashboard totals are computed in `client/src/App.jsx`.

**State:** audit sessions are an in-memory dict (`SESSIONS`) lost on restart (stale approvals return 404). No DB, no auth.

**Client:** single-file React app (`client/src/App.jsx`) talking to the API via `fetch`; Recharts for charts.

## Changing behavior safely

- Tests in `server/tests/test_tools.py` pin exact outputs for the sample data (StreamBox Premium $37.69 → $49.00, +30%, $588/year; 14 sample transactions; the trace event order). Changing `sample_data.py`, tool output shapes or log events will break them, and the README's demo walkthrough quotes the same numbers.
- Pydantic schemas in `server/app/models.py` are the contract between untrusted AI output and the rest of the app; `Statement` is also the Gemini `response_schema`. The category list is duplicated in `SYSTEM_EXTRACTION_PROMPT`, so keep both in sync. Note the extraction prompt also asks for `is_financial_document` / `rejection_reason`, which `Statement` does not define yet, so reconcile the two when hand-coding `extract_statement`.
