/* ============================================================
   Token Usage — LLM token/cost accounting by user and by feature
   ("MCP server, chat, etc."), plus calendar rollups (month/year,
   MTD/YTD). Admin-oriented but not hardcoded admin-only — this
   screen participates in the normal auth.screen_permissions matrix
   (see user-config.jsx's Screen Access tab), so an admin can grant
   or restrict access per user like any other screen.

   Data comes from GET /api/mcp/token-usage/summary?days=N, which
   returns both the rolling by-user/by-feature breakdown for that
   window and all-time calendar rollups. cost_usd is always the
   value stored at the time each call was recorded (the pricing in
   effect then), never recomputed against today's pricing.
   ============================================================ */

const _TU_LABELS = {
  chat: "AI Chat",
  gate1: "Gate 1 Recommend",
  gate2: "Gate 2 Recommend",
  approval_review: "Approval Review Assist",
  pac_draft_rego: "Policy-as-Code: Draft Rego",
  narrative: "Risk Narrative Analysis",
  persona: "Persona Brief",
  report: "Audit Report",
  loop_calibrate: "Loop Calibration (Gate 3)",
  investigate: "Investigation Agent",
  investigate_stream: "Investigation Agent (live)",
  competitors: "Peer/Competitor Extraction",
  unlabeled: "Unlabeled",
};

function _tuLabel(raw) {
  const base = (raw || "unlabeled").split(":")[0]; // strip ":iteration" suffix from agent loops
  return _TU_LABELS[base] || base;
}

function _tuFmtTok(n) {
  n = n || 0;
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
  return String(n);
}

function _tuFmtUsd(n) {
  n = n || 0;
  return "$" + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function TUStatCard({ label, tokens, cost, calls, accent }) {
  return (
    <div style={{
      flex: 1, minWidth: 160, border: "1px solid var(--line)", borderRadius: 8,
      padding: "12px 14px", background: "var(--panel-bg, transparent)",
    }}>
      <div className="kicker" style={{ color: accent || "var(--ink-3)" }}>{label}</div>
      <div className="mono" style={{ fontSize: 20, fontWeight: 700, color: "var(--ink)", marginTop: 4 }}>
        {_tuFmtUsd(cost)}
      </div>
      <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>
        {_tuFmtTok(tokens)} tokens · {calls || 0} calls
      </div>
    </div>
  );
}

function TUBarRow({ label, sub, tokens, cost, calls, maxCost }) {
  const pct = maxCost > 0 ? Math.max(2, (cost / maxCost) * 100) : 0;
  return (
    <div style={{ padding: "7px 0", borderBottom: "1px solid var(--line)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5, marginBottom: 4 }}>
        <span style={{ color: "var(--ink)", fontWeight: 600 }}>
          {label}
          {sub && <span style={{ color: "var(--ink-4)", fontWeight: 400, marginLeft: 6 }}>{sub}</span>}
        </span>
        <span className="mono" style={{ color: "var(--ink-2)" }}>
          {_tuFmtUsd(cost)} <span style={{ color: "var(--ink-4)" }}>· {_tuFmtTok(tokens)} tok · {calls} calls</span>
        </span>
      </div>
      <div style={{ height: 5, borderRadius: 3, background: "var(--line)", overflow: "hidden" }}>
        <div style={{ width: pct + "%", height: "100%", background: "var(--acc)", borderRadius: 3 }} />
      </div>
    </div>
  );
}

function TokenUsageScreen() {
  const [days, setDays] = React.useState(30);
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`/api/mcp/token-usage/summary?days=${days}`, { credentials: "include" })
      .then(res => {
        if (!res.ok) throw new Error(`Failed to load token usage (${res.status})`);
        return res.json();
      })
      .then(d => { if (!cancelled) setData(d); })
      .catch(e => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [days]);

  const rows = data?.rows || [];

  const byUser = React.useMemo(() => {
    const m = new Map();
    for (const r of rows) {
      const key = r.username || "Unknown";
      const cur = m.get(key) || { username: key, input_tokens: 0, output_tokens: 0, cost_usd: 0, calls: 0 };
      cur.input_tokens += r.input_tokens; cur.output_tokens += r.output_tokens;
      cur.cost_usd += r.cost_usd; cur.calls += r.calls;
      m.set(key, cur);
    }
    return [...m.values()].sort((a, b) => b.cost_usd - a.cost_usd);
  }, [rows]);

  const byLabel = React.useMemo(() => {
    const m = new Map();
    for (const r of rows) {
      const key = _tuLabel(r.label);
      const cur = m.get(key) || { label: key, input_tokens: 0, output_tokens: 0, cost_usd: 0, calls: 0 };
      cur.input_tokens += r.input_tokens; cur.output_tokens += r.output_tokens;
      cur.cost_usd += r.cost_usd; cur.calls += r.calls;
      m.set(key, cur);
    }
    return [...m.values()].sort((a, b) => b.cost_usd - a.cost_usd);
  }, [rows]);

  const maxUserCost = byUser.length ? Math.max(...byUser.map(u => u.cost_usd)) : 0;
  const maxLabelCost = byLabel.length ? Math.max(...byLabel.map(l => l.cost_usd)) : 0;
  const maxMonthCost = data?.by_month?.length ? Math.max(...data.by_month.map(m => m.cost_usd)) : 0;

  return (
    <div className="scope-screen" data-screen-label="Token Usage">
      <div className="panel-head">
        <div>
          <div className="kicker">Governance · Configuration</div>
          <div className="panel-title mt-8">Token Usage</div>
          <div className="panel-sub">
            LLM token and cost accounting — by user and by feature/source, plus calendar totals.
            Historical calls made before this screen existed have no user attribution and appear under "Unknown".
          </div>
        </div>
      </div>

      {error && (
        <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 12 }}>{error}</div>
      )}

      {/* ── Calendar totals ─────────────────────────────────────────────── */}
      <div style={{ display: "flex", gap: 10, marginBottom: 18, flexWrap: "wrap" }}>
        <TUStatCard label="MONTH TO DATE" accent="var(--acc)"
          tokens={(data?.month_to_date?.input_tokens || 0) + (data?.month_to_date?.output_tokens || 0)}
          cost={data?.month_to_date?.cost_usd} calls={data?.month_to_date?.calls} />
        <TUStatCard label="YEAR TO DATE" accent="var(--violet-ink, var(--acc))"
          tokens={(data?.year_to_date?.input_tokens || 0) + (data?.year_to_date?.output_tokens || 0)}
          cost={data?.year_to_date?.cost_usd} calls={data?.year_to_date?.calls} />
      </div>

      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", marginBottom: 24 }}>
        <div style={{ flex: 1, minWidth: 320 }}>
          <div className="kicker" style={{ marginBottom: 8 }}>By Month (last 12)</div>
          {!data?.by_month?.length ? <Empty>No monthly usage recorded yet.</Empty> : (
            data.by_month.slice(0, 12).map(m => (
              <TUBarRow key={m.month} label={m.month}
                tokens={m.input_tokens + m.output_tokens} cost={m.cost_usd} calls={m.calls}
                maxCost={maxMonthCost} />
            ))
          )}
        </div>
        <div style={{ flex: 1, minWidth: 320 }}>
          <div className="kicker" style={{ marginBottom: 8 }}>By Year</div>
          {!data?.by_year?.length ? <Empty>No yearly usage recorded yet.</Empty> : (
            data.by_year.map(y => (
              <TUBarRow key={y.year} label={y.year}
                tokens={y.input_tokens + y.output_tokens} cost={y.cost_usd} calls={y.calls}
                maxCost={Math.max(...data.by_year.map(x => x.cost_usd), 1)} />
            ))
          )}
        </div>
      </div>

      {/* ── Rolling breakdown ────────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <div className="kicker">Breakdown</div>
        <div style={{ display: "flex", gap: 4 }}>
          {[7, 30, 90].map(d => (
            <button key={d} className={"btn btn-sm" + (days === d ? " btn-primary" : "")}
              onClick={() => setDays(d)}>{d}d</button>
          ))}
        </div>
      </div>

      {loading ? <Empty>Loading…</Empty> : (
        <>
          <div style={{ display: "flex", gap: 10, marginBottom: 18, flexWrap: "wrap" }}>
            <TUStatCard label={`TOTAL COST · ${days}D`}
              tokens={(data?.totals?.input_tokens || 0) + (data?.totals?.output_tokens || 0)}
              cost={data?.totals?.cost_usd} calls={data?.totals?.calls} />
          </div>

          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 320 }}>
              <div className="kicker" style={{ marginBottom: 8 }}>By User</div>
              {!byUser.length ? <Empty>No usage in this window.</Empty> : (
                byUser.map(u => (
                  <TUBarRow key={u.username} label={u.username}
                    tokens={u.input_tokens + u.output_tokens} cost={u.cost_usd} calls={u.calls}
                    maxCost={maxUserCost} />
                ))
              )}
            </div>
            <div style={{ flex: 1, minWidth: 320 }}>
              <div className="kicker" style={{ marginBottom: 8 }}>By Feature / Source</div>
              {!byLabel.length ? <Empty>No usage in this window.</Empty> : (
                byLabel.map(l => (
                  <TUBarRow key={l.label} label={l.label}
                    tokens={l.input_tokens + l.output_tokens} cost={l.cost_usd} calls={l.calls}
                    maxCost={maxLabelCost} />
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

Object.assign(window, { TokenUsageScreen });
