# Finwise · Finance Intelligence Agent

A small, classroom-friendly full-stack app (React + Vite frontend, FastAPI backend) that demonstrates two AI patterns on fictional personal-finance data:

- **Layer 1, GenAI extraction:** raw bank text or a receipt/statement file is turned into structured transactions by an LLM (Google Gemini), re-validated with Pydantic, and shown on a dashboard.
- **Layer 2, AI agent audit:** a bounded agent loop (max 6 steps) uses deterministic Python tools to find silent subscription price hikes, calculate their yearly cost, and draft a dispute message that waits behind a human approval gate.

> **Educational demo, not financial advice.** The app never sends email, contacts merchants or banks, or moves money. "Approve" only records a local simulation. Use fictional or redacted data only. **Do not upload real bank statements.**

The whole app works with **no API key** (demo mode): sample data, the dashboard and the full agent audit run on a deterministic offline planner. Only extraction needs Gemini, and that integration is left for you to hand-code (see [Hand-coding the AI integration](#hand-coding-the-ai-integration)).

---

## Project structure

```
finance-intelligence-agent/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── ai_service.py     # AI boundary — STUB (hand-code the Gemini calls here)
│   │   ├── main.py           # FastAPI routes, agent loop, in-memory sessions
│   │   ├── models.py         # Pydantic schemas
│   │   ├── sample_data.py    # 14 fictional transactions
│   │   └── tools.py          # Deterministic finance tools
│   ├── tests/test_tools.py
│   ├── .env.example
│   └── requirements.txt
├── frontend/
│   ├── src/App.jsx  src/main.jsx  src/styles.css
│   ├── index.html  package.json  vite.config.js
├── README.md
└── .gitignore
```

## Prerequisites

- Python 3.11+ (3.12 recommended)
- Node.js 20+ and npm
- Optional: a Google Gemini API key (only for extraction / the LLM planner)

---

## Run it locally

Use two terminals: one for the backend, one for the frontend.

### 1. Backend (http://localhost:8000)

macOS / Linux:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Windows (PowerShell):

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

(Windows Command Prompt: activate with `.venv\Scripts\activate.bat`.)

Interactive API docs: http://localhost:8000/docs

### 2. Frontend (http://localhost:5173)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Sample data loads automatically.

### 3. Tests

```bash
cd backend
pytest
```

25 tests cover the three tools, the offline agent flow (AC8–AC13), approval/reject, extraction errors, argument grounding and the planner allowlist.

---

## Configuration

`backend/.env` (copied from `.env.example`, git-ignored):

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | `replace_me` | Your key. `replace_me` or empty = demo mode |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model used for extraction and planning |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | Allowed CORS origins, comma-separated |

Frontend (optional): set `VITE_API_URL` if the backend is not at `http://localhost:8000`, e.g.

```bash
VITE_API_URL=http://localhost:8001 npm run dev
```

The API key lives only in `backend/.env`. The frontend never sees it and never calls Gemini directly.

---

## 6-step demo flow

1. **Open the app.** The 14 fictional sample transactions load. Check the dashboard: 14 transactions (6 merchants), $364.34 outflow, $221.34 in subscriptions.
2. **Explore the dashboard.** The monthly chart (Jun–Sep 26), category bars and the table, which shows each row's raw bank line.
3. **Try extraction.** Click "Use example", then "Extract transactions". Without a key you get a clear "Gemini is not configured" banner and your data stays put. With a key (after hand-coding), three new transactions appear.
4. **Run the agent audit.** Click "Run agent audit". Watch the trace: detect → calculate → draft, then it stops at "awaiting approval".
5. **Inspect the results.** One finding: StreamBox Premium $37.69 → $49.00 (+30%), $588.00/year, plus a draft marked NOT SENT.
6. **Decide.** Click "Approve simulation" or "Reject". The decision is logged in the trace; nothing is ever sent.

---

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | `{"status":"ok","gemini_configured":bool}` |
| GET | `/api/sample` | 14 fictional transactions, `currency: "USD"` |
| POST | `/api/extract` | Multipart `text` and/or `file` → validated transactions, notes, validation errors |
| POST | `/api/audits` | Start a bounded audit: `{"transactions":[...], "goal":"..."}` |
| GET | `/api/audits/{session_id}` | Read audit state and trace |
| POST | `/api/audits/{session_id}/approval` | `{"decision":"approve"\|"reject", "reason": optional}` |

Errors use FastAPI's `{"detail": "message"}`, which the UI shows in a red banner.

---

## Hand-coding the AI integration

All AI access lives in `backend/app/ai_service.py`. You only need to edit that file. Search for `TODO(manual)`:

1. **`_client()`** – create and return `google.genai.Client(api_key=key)`.
2. **`extract_statement()`** – call Gemini with `system_instruction=SYSTEM_EXTRACTION_PROMPT`, `response_mime_type="application/json"`, `response_schema=Statement`, `temperature=0`, and return `Statement.model_validate_json(response.text)`. For files, pass `types.Part.from_bytes(data=file_bytes, mime_type=mime_type)`.
3. **`choose_next_step()`** (optional) – call Gemini with `SYSTEM_PLANNER_PROMPT` and `response_schema=AgentDecision`. Until you do, the deterministic offline planner is used even when a key is set.

Hints are in the comments. `google-genai` is already in `requirements.txt`. Error mapping is handled for you: raise any exception for a failed call and the API returns `502 "Gemini extraction failed: …"`.

Once done, verify AC19–AC22: the status shows "Gemini connected", "Use example" extracts 3 rows with `raw_line` preserved, a line with no date returns `date: null` plus a note, and the sample audit still ends at $588.00.

---

## Teaching notes

- **Grounded extraction:** the prompt forbids inventing values; unclear dates stay `null` and are explained in `notes`.
- **Two validation layers:** Gemini's structured output, then `Transaction.model_validate` on every row in `main.py`. Invalid rows are reported and never reach the dashboard.
- **Maths in code, not the LLM:** dashboard totals in JavaScript, hike/annual-cost maths in Python.
- **Bounded agent:** max 6 steps, 4 allowlisted tool names, anything else fails safely ("Audit stopped safely: …").
- **Argument grounding:** planner arguments are matched against real findings before any tool runs, so a hallucinated merchant or amount cannot change the result.
- **Human in the loop:** a draft always stops at `awaiting_approval`; approval is simulated, `sent` is always `false`.
- **Prompt-injection defence:** transaction text is treated as data in both prompts, and the allowlist can't be overridden by it.
- **Auditability:** every step is logged with a UTC timestamp and shown as a timeline.

## Known limitations

- Audit sessions live in memory and are lost on restart (a stale approval returns 404 — just rerun the audit).
- No database, no auth, single local user.
- No deduplication of repeated transactions.
- USD display only; no currency conversion.
- Transactions can't be edited individually in the UI.
- Extraction requires the hand-coded Gemini integration; in demo mode it returns 503 by design.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Banner "Could not load sample data. Is the backend running?" | Start the backend on port 8000. Check `FRONTEND_ORIGIN` in `backend/.env` matches the URL you open (e.g. `http://localhost:5173`, not `127.0.0.1`). |
| "Gemini is not configured…" | Expected in demo mode. Add `GEMINI_API_KEY` to `backend/.env`, restart the backend, and make sure you've hand-coded `ai_service.py`. |
| "Gemini extraction failed: …" | Check the key, quota and `GEMINI_MODEL` name. Retry manually (there are no automatic retries). |
| Port 8000 already in use | Run `uvicorn app.main:app --reload --port 8001`, then start the frontend with `VITE_API_URL=http://localhost:8001 npm run dev`. |
| Port 5173 already in use | Vite picks another port; add that URL to `FRONTEND_ORIGIN` (comma-separated) and restart the backend. |
| `pytest` can't import `app` | Run it from inside the `backend` folder. |
