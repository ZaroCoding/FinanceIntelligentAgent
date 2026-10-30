import { useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  AlertCircle,
  ArrowDownLeft,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  ChevronRight,
  FileText,
  LayoutDashboard,
  ListOrdered,
  Loader2,
  Play,
  Receipt,
  RefreshCw,
  Repeat,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Upload,
  Wallet,
  X,
} from "lucide-react";

// The frontend only ever talks to our own backend. It never calls Gemini directly.
const API_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");
const MAX_FILE_BYTES = 12 * 1024 * 1024;
const DEFAULT_GOAL = "Audit my transactions for subscription traps and silent price hikes.";
const EXAMPLE_TEXT = [
  "2026-09-14 TST* SPOTIFY USA 11.99",
  "2026-09-18 UPI/FOOD DELIVERY/8231 18.75",
  "2026-09-20 POS HPCL FUEL HYD 25.00",
].join("\n");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const CATEGORY_COLORS = {
  Subscriptions: "#7c6ff0",
  Shopping: "#3b8bd4",
  Fuel: "#e3a33b",
  "Food delivery": "#e26d5a",
  Dining: "#2fa37a",
  Income: "#4cae4c",
  Bills: "#c05b8c",
  Other: "#8a90a6",
};

// ----------------------------------------------------------------- formatting helpers

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const formatMoney = (n) => usd.format(Number(n) || 0);
const round2 = (n) => Math.round((n + Number.EPSILON) * 100) / 100;

// Dates arrive as "YYYY-MM-DD"; parse by hand to avoid timezone shifts.
function formatShortDate(iso) {
  if (!iso) return "Date unclear";
  const [, m, d] = iso.split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}`;
}

function monthLabel(key) {
  const [y, m] = key.split("-");
  return `${MONTHS[Number(m) - 1]} ${y.slice(2)}`;
}

function formatClock(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

async function readError(res, fallback) {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join("; ");
  } catch {
    /* non-JSON error */
  }
  return fallback;
}

// ----------------------------------------------------------------- dashboard maths (spec L3)

function useDashboard(transactions) {
  return useMemo(() => {
    const positive = transactions.filter((t) => t.amount > 0);
    const totalOutflow = round2(positive.reduce((s, t) => s + t.amount, 0));
    const subscriptions = round2(
      positive.filter((t) => t.category === "Subscriptions").reduce((s, t) => s + t.amount, 0)
    );
    const merchants = new Set(transactions.map((t) => t.merchant)).size;

    const byMonth = {};
    positive
      .filter((t) => t.date)
      .forEach((t) => {
        const key = t.date.slice(0, 7);
        byMonth[key] = (byMonth[key] || 0) + t.amount;
      });
    const monthly = Object.keys(byMonth)
      .sort()
      .map((key) => ({ key, label: monthLabel(key), amount: round2(byMonth[key]) }));

    const byCat = {};
    positive.forEach((t) => {
      byCat[t.category] = (byCat[t.category] || 0) + t.amount;
    });
    const categories = Object.entries(byCat)
      .map(([name, amount]) => ({ name, amount: round2(amount) }))
      .sort((a, b) => b.amount - a.amount)
      .slice(0, 6);
    const maxCat = categories.length ? categories[0].amount : 0;

    const rows = [...transactions].sort((a, b) => {
      if (!a.date && !b.date) return 0;
      if (!a.date) return 1; // null dates last
      if (!b.date) return -1;
      return b.date.localeCompare(a.date);
    });

    return { totalOutflow, subscriptions, merchants, monthly, categories, maxCat, rows };
  }, [transactions]);
}

// ----------------------------------------------------------------- small components

function Spinner() {
  return <Loader2 className="spin" size={16} aria-hidden="true" />;
}

function MetricCard({ icon: Icon, tone, label, value, sub }) {
  return (
    <div className="card metric">
      <div className={`metric-icon tone-${tone}`}>
        <Icon size={18} aria-hidden="true" />
      </div>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-sub">{sub}</div>
    </div>
  );
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-label">{label}</div>
      <div className="chart-tooltip-value">{formatMoney(payload[0].value)}</div>
    </div>
  );
}

const TRACE_TONE = {
  started: "blue",
  plan: "blue",
  completed: "blue",
  tool: "green",
  approval_required: "amber",
  approved_simulation: "amber",
  rejected: "amber",
  error: "red",
};

const STATUS_LABEL = {
  analyzing: "analyzing",
  awaiting_approval: "awaiting approval",
  executing: "executing",
  done: "done",
  failed: "failed",
};

// ----------------------------------------------------------------- app

export default function App() {
  const [transactions, setTransactions] = useState([]);
  const [source, setSource] = useState("sample");
  const [geminiConfigured, setGeminiConfigured] = useState(false);
  const [error, setError] = useState("");

  const [text, setText] = useState("");
  const [file, setFile] = useState(null);
  const [notes, setNotes] = useState([]);
  const [loadingSample, setLoadingSample] = useState(false);
  const [extracting, setExtracting] = useState(false);

  const [goal, setGoal] = useState(DEFAULT_GOAL);
  const [audit, setAudit] = useState(null);
  const [auditing, setAuditing] = useState(false);
  const [deciding, setDeciding] = useState("");
  const [reason, setReason] = useState("");

  const fileInput = useRef(null);
  const txRef = useRef(null);
  const auditRef = useRef(null);
  const topRef = useRef(null);
  const [activeNav, setActiveNav] = useState("overview");

  const dash = useDashboard(transactions);

  // ------------------------------------------------------------- API calls

  async function loadHealth() {
    try {
      const res = await fetch(`${API_URL}/api/health`);
      const body = await res.json();
      setGeminiConfigured(Boolean(body.gemini_configured));
    } catch {
      setGeminiConfigured(false);
    }
  }

  async function loadSample() {
    setError("");
    setAudit(null);
    setNotes([]);
    setLoadingSample(true);
    try {
      const res = await fetch(`${API_URL}/api/sample`);
      if (!res.ok) throw new Error();
      const body = await res.json();
      setTransactions(body.transactions || []);
      setSource("sample");
    } catch {
      setError("Could not load sample data. Is the backend running?");
      setGeminiConfigured(false);
    } finally {
      setLoadingSample(false);
    }
  }

  async function extract() {
    if (!text.trim() && !file) return;
    setError("");
    setAudit(null);
    setNotes([]);
    setExtracting(true);
    try {
      const form = new FormData();
      if (text.trim()) form.append("text", text);
      if (file) form.append("file", file);
      const res = await fetch(`${API_URL}/api/extract`, { method: "POST", body: form });
      if (!res.ok) throw new Error(await readError(res, "Extraction failed."));
      const body = await res.json();
      setNotes(body.notes || []);
      if (!body.count) {
        // Keep the existing transactions when nothing valid came back.
        setError("No valid transactions were extracted. Check the input and try again.");
        return;
      }
      setTransactions(body.transactions);
      setSource("input");
      if (body.validation_errors?.length) {
        setNotes((n) => [
          ...n,
          `${body.validation_errors.length} row(s) failed validation and were dropped.`,
        ]);
      }
    } catch (e) {
      setError(e.message || "Could not reach the backend. Is it running?");
    } finally {
      setExtracting(false);
    }
  }

  async function runAudit() {
    if (!transactions.length) return;
    setError("");
    setAudit(null);
    setReason("");
    setAuditing(true);
    try {
      const res = await fetch(`${API_URL}/api/audits`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transactions, goal: goal.trim() || DEFAULT_GOAL }),
      });
      if (!res.ok) throw new Error(await readError(res, "The audit could not start."));
      setAudit(await res.json());
    } catch (e) {
      setError(e.message || "Could not reach the backend. Is it running?");
    } finally {
      setAuditing(false);
    }
  }

  async function decide(decision) {
    if (!audit) return;
    setError("");
    setDeciding(decision);
    try {
      const res = await fetch(`${API_URL}/api/audits/${audit.session_id}/approval`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, reason: reason.trim() || null }),
      });
      if (!res.ok) throw new Error(await readError(res, "The decision could not be recorded."));
      setAudit(await res.json());
    } catch (e) {
      setError(e.message || "Could not reach the backend. Is it running?");
    } finally {
      setDeciding("");
    }
  }

  useEffect(() => {
    loadHealth();
    loadSample();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------- handlers

  function onFileChange(e) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    if (f.size > MAX_FILE_BYTES) {
      setError("Please use a file smaller than 12 MB for this classroom demo.");
      return;
    }
    setError("");
    setFile(f);
  }

  function onDrop(e) {
    e.preventDefault();
    const f = e.dataTransfer.files?.[0];
    if (f) onFileChange({ target: { files: [f], value: "" } });
  }

  function scrollTo(ref, key) {
    setActiveNav(key);
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const findings = audit?.findings || [];
  const pending = audit?.pending_action;
  const awaiting = audit?.status === "awaiting_approval";
  const approved = pending?.approved === true;
  const rejected = pending?.approved === false;

  // ------------------------------------------------------------- render

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">
            <Activity size={18} aria-hidden="true" />
          </div>
          <div>
            <div className="brand-name">Finwise</div>
            <div className="brand-caption">Intelligence agent</div>
          </div>
        </div>

        <div className="nav-label">Workspace</div>
        <nav className="nav">
          <button
            className={`nav-item ${activeNav === "overview" ? "active" : ""}`}
            onClick={() => scrollTo(topRef, "overview")}
          >
            <LayoutDashboard size={17} aria-hidden="true" /> Overview
          </button>
          <button
            className={`nav-item ${activeNav === "transactions" ? "active" : ""}`}
            onClick={() => scrollTo(txRef, "transactions")}
          >
            <ListOrdered size={17} aria-hidden="true" /> Transactions
            <span className="nav-badge">{transactions.length}</span>
          </button>
          <button
            className={`nav-item ${activeNav === "audit" ? "active" : ""}`}
            onClick={() => scrollTo(auditRef, "audit")}
          >
            <Bot size={17} aria-hidden="true" /> Agent audit
          </button>
        </nav>

        <div className="sidebar-card">
          <ShieldCheck size={18} className="sidebar-card-icon" aria-hidden="true" />
          <div className="sidebar-card-title">Demo-safe mode</div>
          <p>Sample data is fictional. Approval actions are simulated and never sent.</p>
          <div className="sidebar-build">Workshop build · v1.0.0</div>
        </div>
      </aside>

      <main className="main" ref={topRef}>
        <header className="topbar">
          <div>
            <div className="eyebrow">Personal finance / Overview</div>
            <h1>Finance overview</h1>
            <p className="subtitle">Understand your spending. Let your agent investigate the details.</p>
          </div>
          <div className="topbar-right">
            <div className="status">
              <span className={`status-dot ${geminiConfigured ? "on" : ""}`} />
              {geminiConfigured ? "Gemini connected" : "Demo mode available"}
            </div>
            <button
              className="icon-btn"
              onClick={loadSample}
              disabled={loadingSample}
              aria-label="Reload sample data"
              title="Reload sample data"
            >
              <RefreshCw size={16} className={loadingSample ? "spin" : ""} />
            </button>
            <div className="avatar" aria-hidden="true">N</div>
          </div>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            <AlertCircle size={18} aria-hidden="true" />
            <span>{error}</span>
            <button className="error-close" onClick={() => setError("")} aria-label="Dismiss error">
              <X size={16} />
            </button>
          </div>
        )}

        {/* Metrics */}
        <section className="metrics">
          <MetricCard
            icon={Receipt}
            tone="blue"
            label="Total transactions"
            value={transactions.length}
            sub={`${dash.merchants} unique merchants`}
          />
          <MetricCard
            icon={Wallet}
            tone="violet"
            label="Total outflow"
            value={formatMoney(dash.totalOutflow)}
            sub="Across loaded transactions"
          />
          <MetricCard
            icon={Repeat}
            tone="amber"
            label="Subscription charges"
            value={formatMoney(dash.subscriptions)}
            sub="All visible periods combined"
          />
          <MetricCard
            icon={TrendingUp}
            tone="green"
            label="Potential price hikes"
            value={audit ? findings.length : "—"}
            sub={audit ? "From the latest agent audit" : "Run an agent audit"}
          />
        </section>

        {/* Chart + categories */}
        <section className="grid-2">
          <div className="card panel">
            <div className="panel-head">
              <div>
                <h2>Spending activity</h2>
                <div className="panel-sub">Monthly transaction outflow</div>
              </div>
              <span className="pill">{source === "sample" ? "Sample data" : "Your input"}</span>
            </div>
            {dash.monthly.length ? (
              <div className="chart">
                <ResponsiveContainer width="100%" height={240}>
                  <AreaChart data={dash.monthly} margin={{ top: 10, right: 8, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="spendFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#6d78e8" stopOpacity={0.32} />
                        <stop offset="100%" stopColor="#6d78e8" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="4 4" vertical={false} stroke="#eaecf2" />
                    <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: "#8a90a6", fontSize: 12 }} />
                    <YAxis
                      tickLine={false}
                      axisLine={false}
                      width={52}
                      tick={{ fill: "#8a90a6", fontSize: 12 }}
                      tickFormatter={(v) => `$${v}`}
                    />
                    <Tooltip content={<ChartTooltip />} cursor={{ stroke: "#c9cdf5" }} />
                    <Area
                      type="monotone"
                      dataKey="amount"
                      stroke="#6d78e8"
                      strokeWidth={2.5}
                      fill="url(#spendFill)"
                      dot={{ r: 3.5, fill: "#fff", stroke: "#6d78e8", strokeWidth: 2 }}
                      activeDot={{ r: 5 }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="empty">Add dated transactions to see spending activity.</div>
            )}
          </div>

          <div className="card panel">
            <div className="panel-head">
              <div>
                <h2>Spending by category</h2>
                <div className="panel-sub">Positive charges only</div>
              </div>
            </div>
            {dash.categories.length ? (
              <ul className="cat-list">
                {dash.categories.map((c) => {
                  const color = CATEGORY_COLORS[c.name] || CATEGORY_COLORS.Other;
                  const width = Math.max(3, dash.maxCat ? (c.amount / dash.maxCat) * 100 : 0);
                  return (
                    <li key={c.name} className="cat-row">
                      <span className="cat-icon" style={{ background: `${color}1f`, color }}>
                        {c.name[0]}
                      </span>
                      <div className="cat-body">
                        <div className="cat-line">
                          <span className="cat-name">{c.name}</span>
                          <span className="cat-amount">{formatMoney(c.amount)}</span>
                        </div>
                        <div className="bar">
                          <div className="bar-fill" style={{ width: `${width}%`, background: color }} />
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="empty">Category totals appear after transactions are loaded.</div>
            )}
          </div>
        </section>

        {/* Import */}
        <section className="card panel">
          <div className="panel-head">
            <div>
              <div className="kicker">Day 1 · GenAI extraction</div>
              <h2>Bring in financial data</h2>
            </div>
            <span className="tag">01 Extract &amp; validate</span>
          </div>

          <div className="import-grid">
            <div
              className="dropzone"
              onDragOver={(e) => e.preventDefault()}
              onDrop={onDrop}
            >
              {file ? (
                <div className="file-chosen">
                  <FileText size={26} aria-hidden="true" />
                  <div className="file-name">{file.name}</div>
                  <div className="file-size">{(file.size / 1024).toFixed(1)} KB</div>
                  <button className="link-btn" onClick={() => setFile(null)}>
                    Remove
                  </button>
                </div>
              ) : (
                <>
                  <div className="drop-icon">
                    <Upload size={20} aria-hidden="true" />
                  </div>
                  <div className="drop-title">Choose a receipt or statement</div>
                  <div className="drop-sub">PNG, JPG, PDF, CSV or TXT · max 12 MB</div>
                  <button className="btn btn-secondary btn-sm" onClick={() => fileInput.current?.click()}>
                    Browse files
                  </button>
                </>
              )}
              <input
                ref={fileInput}
                type="file"
                accept="image/*,.pdf,.csv,.txt"
                onChange={onFileChange}
                hidden
              />
            </div>

            <div className="or-divider">
              <span>OR</span>
            </div>

            <div className="paste">
              <label className="field-label" htmlFor="raw-text">
                Paste raw transaction text
              </label>
              <textarea
                id="raw-text"
                rows={4}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={"2026-09-14 TST* SPOTIFY USA 11.99\n2026-09-18 UPI/FOOD DELIVERY/8231 18.75"}
              />
              <div className="helper-row">
                <span>Raw text is sent to Gemini when configured.</span>
                <button className="link-btn" onClick={() => setText(EXAMPLE_TEXT)}>
                  Use example
                </button>
              </div>
            </div>
          </div>

          <div className="panel-foot">
            <div className="validation-note">
              <CheckCircle2 size={16} aria-hidden="true" />
              Structured output + Pydantic validation. Unclear fields should stay null, not guessed.
            </div>
            <div className="btn-row">
              <button className="btn btn-secondary" onClick={loadSample} disabled={loadingSample}>
                {loadingSample && <Spinner />} Load sample data
              </button>
              <button
                className="btn btn-primary"
                onClick={extract}
                disabled={extracting || (!text.trim() && !file)}
              >
                {extracting ? <Spinner /> : <Sparkles size={16} aria-hidden="true" />} Extract transactions
              </button>
            </div>
          </div>

          {notes.length > 0 && (
            <div className="notes">
              <div className="notes-title">Extraction notes</div>
              <ul>
                {notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </div>
          )}
        </section>

        {/* Transactions */}
        <section className="card panel" ref={txRef}>
          <div className="panel-head">
            <div>
              <h2>Transactions</h2>
              <div className="panel-sub">Validated, typed records feeding your dashboard</div>
            </div>
            <span className="pill">{transactions.length} records</span>
          </div>
          {dash.rows.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Merchant</th>
                    <th>Date</th>
                    <th>Category</th>
                    <th>Raw source</th>
                    <th className="right">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {dash.rows.map((t, i) => {
                    const color = CATEGORY_COLORS[t.category] || CATEGORY_COLORS.Other;
                    const refund = t.amount < 0;
                    return (
                      <tr key={`${t.merchant}-${t.date}-${i}`}>
                        <td>
                          <div className="merchant">
                            <span className="avatar-sm" style={{ background: `${color}1f`, color }}>
                              {t.merchant[0]}
                            </span>
                            <strong>{t.merchant}</strong>
                          </div>
                        </td>
                        <td className={t.date ? "" : "muted"}>{formatShortDate(t.date)}</td>
                        <td>
                          <span className="chip" style={{ background: `${color}17`, color }}>
                            {t.category}
                          </span>
                        </td>
                        <td className="raw">{t.raw_line || "—"}</td>
                        <td className={`right amount ${refund ? "refund" : ""}`}>
                          {refund ? (
                            <ArrowDownLeft size={14} aria-label="refund" />
                          ) : (
                            <ArrowUpRight size={14} aria-label="charge" />
                          )}
                          {formatMoney(Math.abs(t.amount))}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">No transactions yet. Load sample data or extract a statement above.</div>
          )}
        </section>

        {/* Agent */}
        <section className="agent-section" ref={auditRef}>
          <div className="section-head">
            <div className="kicker">Day 2 · Autonomous agent</div>
            <h2>Run a finance audit</h2>
            <p className="panel-sub">
              The agent investigates with Python tools, then asks before any consequential action.
            </p>
          </div>

          <div className="agent-grid">
            <div className="card panel goal-card">
              <label className="field-label" htmlFor="goal">
                Your audit goal
              </label>
              <textarea id="goal" rows={4} value={goal} onChange={(e) => setGoal(e.target.value)} />
              <div className="safety-note">
                <ShieldCheck size={16} aria-hidden="true" />
                <span>
                  Read-only analysis runs automatically. Cancellation / dispute actions are drafts and require
                  your approval. Nothing is sent.
                </span>
              </div>
              <button
                className="btn btn-primary btn-block"
                onClick={runAudit}
                disabled={auditing || !transactions.length}
              >
                {auditing ? <Spinner /> : <Play size={16} aria-hidden="true" />} Run agent audit
              </button>
            </div>

            <div className="card panel trace-card">
              <div className="panel-head">
                <div>
                  <h2>Agent trace</h2>
                  <div className="panel-sub">Tools, observations and decisions</div>
                </div>
                {audit && (
                  <span className={`status-pill st-${audit.status}`}>{STATUS_LABEL[audit.status]}</span>
                )}
              </div>

              {audit ? (
                <>
                  {audit.summary && <div className="summary">{audit.summary}</div>}
                  <ol className="timeline">
                    {audit.audit_log.map((e, i) => (
                      <li key={i} className="tl-item">
                        <span className={`tl-dot dot-${TRACE_TONE[e.type] || "blue"}`} />
                        <div className="tl-body">
                          <div className="tl-msg">{e.message}</div>
                          <div className="tl-meta">
                            {e.type} · {formatClock(e.time)}
                          </div>
                          {e.type === "plan" && (
                            <code className="tl-code">
                              {e.details?.tool}({JSON.stringify(e.details?.arguments || {})})
                            </code>
                          )}
                        </div>
                      </li>
                    ))}
                  </ol>
                </>
              ) : (
                <div className="trace-empty">
                  <div className="trace-empty-icon">
                    <Bot size={24} aria-hidden="true" />
                  </div>
                  <div className="trace-empty-title">Your audit trace will appear here</div>
                  <p>
                    Each planning step, tool call and approval request is recorded so you can see exactly what
                    the agent did.
                  </p>
                  <div className="mini-steps">
                    <span>Inspect</span>
                    <ChevronRight size={14} aria-hidden="true" />
                    <span>Calculate</span>
                    <ChevronRight size={14} aria-hidden="true" />
                    <span>Ask first</span>
                  </div>
                </div>
              )}
            </div>
          </div>

          {findings.length > 0 && (
            <div className="findings">
              {findings.map((f, i) => (
                <div key={i} className="card finding">
                  <div className="finding-badge">
                    <AlertCircle size={14} aria-hidden="true" /> Price increase detected
                    <strong>+{Math.round(f.change_pct)}%</strong>
                  </div>
                  <div className="finding-merchant">{f.merchant}</div>
                  <div className="finding-amounts">
                    {formatMoney(f.old_amount)} → <strong>{formatMoney(f.new_amount)}</strong>/month
                  </div>
                  <div className="finding-foot">
                    <span>Detected {formatShortDate(f.date)}</span>
                    <span>+{formatMoney(f.monthly_increase)} / month</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {pending && (
            <div className="card panel approval">
              <div className="kicker">Human-in-the-loop</div>
              <div className="approval-head">
                <div>
                  <h2>
                    {awaiting
                      ? "Action needs your approval"
                      : approved
                      ? "Action approved (simulation)"
                      : "Proposed action reviewed"}
                  </h2>
                  <div className="panel-sub">{pending.merchant} · Subscription change review</div>
                </div>
                <span className={`status-pill ${awaiting ? "st-awaiting_approval" : "st-done"}`}>
                  {awaiting ? "Awaiting decision" : "Decision recorded"}
                </span>
              </div>

              <div className="facts">
                <div className="fact">
                  <div className="fact-label">Price increase</div>
                  <div className="fact-value">+{pending.change_pct.toFixed(1)}%</div>
                </div>
                <div className="fact">
                  <div className="fact-label">Annualized cost now</div>
                  <div className="fact-value">{formatMoney(pending.annual_cost)}</div>
                </div>
                <div className="fact">
                  <div className="fact-label">Monthly difference</div>
                  <div className="fact-value">+{formatMoney(pending.monthly_increase)}</div>
                </div>
              </div>

              <div className="draft">
                <div className="draft-head">
                  <span>Cancellation / dispute draft</span>
                  <span className="not-sent">Not sent</span>
                </div>
                <pre>{pending.draft}</pre>
              </div>

              {awaiting && (
                <input
                  className="reason"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Optional: reason for your decision"
                  maxLength={300}
                />
              )}

              <div className="approval-foot">
                <p>
                  {awaiting &&
                    "Review the draft. Approving only records a local simulation; no message will be sent."}
                  {approved && "Approved and recorded locally. No message was sent to the merchant."}
                  {rejected && "You rejected this proposal. Nothing was sent."}
                </p>
                {awaiting && (
                  <div className="btn-row">
                    <button className="btn btn-secondary" onClick={() => decide("reject")} disabled={!!deciding}>
                      {deciding === "reject" && <Spinner />} Reject
                    </button>
                    <button className="btn btn-primary" onClick={() => decide("approve")} disabled={!!deciding}>
                      {deciding === "approve" ? <Spinner /> : <CheckCircle2 size={16} aria-hidden="true" />} Approve
                      simulation
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}
        </section>

        <footer className="footer">
          <span>Finwise · Finance intelligence agent</span>
          <span>Built for learning · Not financial advice</span>
        </footer>
      </main>
    </div>
  );
}
