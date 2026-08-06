/* ============================================================
   Continuous Monitoring — command center answering "what's being
   watched right now, what fired recently, what's stale," by
   composing existing observability building blocks (registered
   systems, poll connectors, holds, coverage, PaC, Model Health)
   behind one call rather than duplicating any of their logic.

   Data comes from GET /api/mcp/observability/command-center.
   ============================================================ */
import { ContinuousMonitoringDomainViz, ContinuousMonitoringSourceSystemViz } from "./continuous-monitoring-viz.jsx";

function _cmBase() {
  return (window.MCP_API_BASE || "/api/mcp") + "/observability";
}

function CMTile({ label, value, sub, tone = "neutral", onClick, trend }) {
  const toneColor = {
    neutral: "var(--ink)",
    good: "var(--green-ink)",
    warn: "var(--amber-ink)",
    bad: "var(--red-ink)",
  }[tone] || "var(--ink)";
  const clickable = !!onClick;
  return (
    <div
      onClick={onClick}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={clickable ? (e => { if (e.key === "Enter") onClick(e); }) : undefined}
      style={{
        flex: "1 1 160px", minWidth: 160, border: "1px solid var(--line)", borderRadius: 8,
        padding: "12px 14px", background: "var(--surface)",
        cursor: clickable ? "pointer" : "default",
        transition: "border-color .12s, background .12s",
      }}
      onMouseEnter={clickable ? (e => { e.currentTarget.style.borderColor = "var(--acc)"; e.currentTarget.style.background = "var(--hover)"; }) : undefined}
      onMouseLeave={clickable ? (e => { e.currentTarget.style.borderColor = "var(--line)"; e.currentTarget.style.background = "var(--surface)"; }) : undefined}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>
          {label}
        </div>
        {clickable && <Icon name="chev-r" size={10} className="muted"/>}
      </div>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 8, marginTop: 4 }}>
        <div className="mono" style={{ fontSize: 22, fontWeight: 700, color: toneColor }}>
          {value}
        </div>
        {trend && trend.some(v => v > 0) && (
          <Sparkline data={trend} w={54} h={20} color={toneColor} />
        )}
      </div>
      {sub && <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

function _ageLabel(ts) {
  if (!ts) return "never";
  const ms = Date.now() - new Date(ts).getTime();
  if (ms < 60000) return `${Math.round(ms / 1000)}s ago`;
  if (ms < 3600000) return `${Math.round(ms / 60000)}m ago`;
  if (ms < 86400000) return `${Math.round(ms / 3600000)}h ago`;
  return `${Math.round(ms / 86400000)}d ago`;
}

function CMLiveFeedTable({ title, rows, columns, emptyLabel, onTitleClick }) {
  return (
    <div>
      <div
        className="kicker"
        onClick={onTitleClick}
        style={{
          marginBottom: 8, display: "flex", alignItems: "center", gap: 5,
          cursor: onTitleClick ? "pointer" : "default",
          color: onTitleClick ? "var(--acc-ink)" : undefined,
        }}
      >
        {title}
        {onTitleClick && <Icon name="chev-r" size={9}/>}
      </div>
      {!rows.length ? <Empty>{emptyLabel}</Empty> : (
        <div style={{ border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
          {rows.map((r, i) => (
            <div key={i} style={{
              display: "grid", gridTemplateColumns: columns.map(c => c.width || "1fr").join(" "),
              gap: 10, padding: "8px 12px", fontSize: 11.5,
              borderBottom: i < rows.length - 1 ? "1px solid var(--line)" : "none",
            }}>
              {columns.map(c => (
                <div key={c.key} style={c.style}>{c.render ? c.render(r) : r[c.key]}</div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ContinuousMonitoringScreen({ onNavigate } = {}) {
  const LiveBadge = window.LiveBadge;
  const goTo = (screen, opts) => onNavigate && onNavigate(screen, opts);
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [lastRefresh, setLastRefresh] = React.useState(null);
  const [isPaused, setIsPaused] = React.useState(false);
  const [evidenceView, setEvidenceView] = React.useState("domain");

  const load = React.useCallback(() => {
    return fetch(`${_cmBase()}/command-center`, { credentials: "include" })
      .then(res => {
        if (!res.ok) throw new Error(`Failed to load command center (${res.status})`);
        return res.json();
      })
      .then(d => { setData(d); setError(null); setLastRefresh(new Date()); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    load();
    if (isPaused) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load, isPaused]);

  const systems = data?.systems || [];
  const connectors = data?.connectors || [];
  const pacProcesses = data?.pac_processes || [];
  const last24h = data?.last_24h || { adjudicated: 0, escalated: 0, pac_violations: 0 };
  const hourly = data?.hourly || { hours: [], adjudicated: [], escalated: [] };

  const liveSystemsCount = systems.filter(s => s.last_seen && (Date.now() - new Date(s.last_seen).getTime()) < 3600000).length;
  const activeConnectorsCount = connectors.filter(c => c.active).length;
  const erroringConnectorsCount = connectors.filter(c => c.active && c.last_poll_status === "error").length;

  return (
    <div className="scope-screen" data-screen-label="Continuous Monitoring">
      <div className="panel-head">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div>
            <div className="kicker">Audit & Compliance · Command Center</div>
            <div className="panel-title mt-8">Continuous Monitoring</div>
            <div className="panel-sub">
              What's being watched right now, what's fired in the last 24 hours, and what's gone stale —
              across pushed telemetry, polled connectors, Policy-as-Code coverage, and Model Health drift.
            </div>
          </div>
          {LiveBadge && (
            <LiveBadge lastRefresh={lastRefresh} isPaused={isPaused}
              onToggle={() => setIsPaused(p => !p)} intervalLabel="5s" />
          )}
        </div>
      </div>

      {error && (
        <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 12 }}>{error}</div>
      )}

      {loading && !data ? <Empty>Loading…</Empty> : (
        <>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 24 }}>
            <CMTile label="Systems live (1h)" value={`${liveSystemsCount} / ${systems.length}`}
              sub="Pushed telemetry, seen in the last hour" tone={liveSystemsCount > 0 ? "good" : "neutral"}
              onClick={() => goTo("uboconfig")} />
            <CMTile label="Connectors active" value={`${activeConnectorsCount} / ${connectors.length}`}
              sub={erroringConnectorsCount > 0 ? `${erroringConnectorsCount} erroring` : "Polling on schedule"}
              tone={erroringConnectorsCount > 0 ? "bad" : "good"}
              onClick={() => goTo("uboconfig")} />
            <CMTile label="Adjudicated (24h)" value={last24h.adjudicated} sub="Tool calls reviewed"
              trend={hourly.adjudicated} onClick={() => goTo("ubogov", { cemTab: "adjudications" })} />
            <CMTile label="Escalated (24h)" value={last24h.escalated}
              tone={last24h.escalated > 0 ? "warn" : "good"} sub="Sent to human hold"
              trend={hourly.escalated} onClick={() => goTo("ubogov", { cemTab: "adjudications" })} />
            <CMTile label="PaC violations (24h)" value={last24h.pac_violations}
              tone={last24h.pac_violations > 0 ? "bad" : "good"}
              onClick={() => goTo("policycode")} />
            <CMTile label="Pending holds" value={data?.pending_holds ?? 0}
              tone={(data?.pending_holds ?? 0) > 0 ? "warn" : "good"} sub="Awaiting human decision"
              onClick={() => goTo("ubogov", { cemTab: "holds" })} />
            <CMTile label="Coverage blind spots" value={data?.coverage_blind_spots ?? 0}
              tone={(data?.coverage_blind_spots ?? 0) > 0 ? "warn" : "good"} sub="MCP + system tools with zero flag history"
              onClick={() => goTo("ubogov", { cemTab: "coverage" })} />
            <CMTile label="Model Health drift" value={data?.model_health_drift ? "Drift" : "Stable"}
              tone={data?.model_health_drift ? "bad" : "good"} sub="Ratio + FRED regime PSI"
              onClick={() => goTo("modelhealth")} />
          </div>

          <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
            {[["domain", "By Core Domain & Risk"], ["legacy", "Adjudication Flow"]].map(([v, label]) => (
              <button key={v} type="button" onClick={() => setEvidenceView(v)}
                style={{
                  fontSize: 11, padding: "5px 12px", borderRadius: 5, cursor: "pointer",
                  border: v === evidenceView ? "1px solid var(--acc,#2563eb)" : "1px solid var(--line,#ddd)",
                  background: v === evidenceView ? "var(--acc,#2563eb)" : "transparent",
                  color: v === evidenceView ? "#fff" : "var(--ink-2,#555)",
                  fontWeight: v === evidenceView ? 600 : 400,
                }}>
                {label}
              </button>
            ))}
          </div>

          {evidenceView === "domain"
            ? <ContinuousMonitoringDomainViz onNavigate={goTo} />
            : <ContinuousMonitoringSourceSystemViz onNavigate={goTo} />}

          <div style={{ height: 28 }} />

          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            <div style={{ flex: 2, minWidth: 380 }}>
              <CMLiveFeedTable
                title="Registered systems"
                onTitleClick={() => goTo("uboconfig")}
                rows={systems}
                emptyLabel="No systems registered yet — add one in Dendrai UBO™ Configuration."
                columns={[
                  { key: "name", width: "1.4fr", render: r => <span style={{ fontWeight: 600 }}>{r.display_name || r.server_name}</span> },
                  { key: "calls", width: "0.8fr", render: r => `${r.total_calls ?? 0} calls` },
                  { key: "flagged", width: "0.8fr", render: r => `${r.flagged_calls ?? 0} flagged` },
                  { key: "last_seen", width: "1fr", style: { color: "var(--ink-4)", fontSize: 10.5 }, render: r => _ageLabel(r.last_seen) },
                ]} />

              <div style={{ height: 20 }} />

              <CMLiveFeedTable
                title="Poll-based connectors"
                onTitleClick={() => goTo("uboconfig")}
                rows={connectors}
                emptyLabel="No poll connectors configured — add Oracle Fusion, SAP HANA, SailPoint, Dynamics 365, or NetSuite in Dendrai UBO™ Configuration."
                columns={[
                  { key: "name", width: "1.4fr", render: r => <span style={{ fontWeight: 600 }}>{r.display_name}</span> },
                  { key: "type", width: "0.9fr", style: { fontSize: 10.5, color: "var(--ink-4)" }, render: r => r.connector_type },
                  {
                    key: "status", width: "0.9fr",
                    render: r => (
                      <span style={{
                        color: !r.active ? "var(--ink-4)" : r.last_poll_status === "error" ? "var(--red-ink)" : "var(--green-ink)",
                        fontSize: 10.5,
                      }}>
                        {!r.active ? "Inactive" : r.last_poll_status === "error" ? "Error" : r.last_poll_status === "ok" ? "OK" : "Pending"}
                      </span>
                    ),
                  },
                  { key: "last_poll_at", width: "1fr", style: { color: "var(--ink-4)", fontSize: 10.5 }, render: r => _ageLabel(r.last_poll_at) },
                ]} />
            </div>

            <div style={{ flex: 1, minWidth: 300 }}>
              <div
                className="kicker"
                onClick={() => goTo("policycode")}
                style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 5, cursor: "pointer", color: "var(--acc-ink)" }}
              >
                Policy-as-Code coverage
                <Icon name="chev-r" size={9}/>
              </div>
              {!pacProcesses.length ? <Empty>No PaC processes registered yet.</Empty> : (
                <div style={{ border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
                  {pacProcesses.map(p => {
                    const cov = p.rule_coverage;
                    const pct = cov && cov.total > 0 ? Math.round((cov.with_control_id / cov.total) * 100) : null;
                    return (
                      <div key={p.id}
                        onClick={() => goTo("policycode", { pacProcess: p.id })}
                        onMouseEnter={e => { e.currentTarget.style.background = "var(--hover)"; }}
                        onMouseLeave={e => { e.currentTarget.style.background = "transparent"; }}
                        style={{
                          display: "flex", alignItems: "center", justifyContent: "space-between",
                          padding: "8px 12px", fontSize: 11.5, borderBottom: "1px solid var(--line)", gap: 8,
                          cursor: "pointer", transition: "background .12s",
                        }}>
                        <div>
                          <div style={{ fontWeight: 600 }}>{p.label}</div>
                          <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 1 }}>
                            {p.source_format} · {p.source === "github_discovered" ? "auto-discovered" : p.source}
                          </div>
                        </div>
                        <div className="mono" style={{
                          fontSize: 11, fontWeight: 700,
                          color: pct == null ? "var(--ink-4)" : pct === 100 ? "var(--green-ink)" : "var(--amber-ink)",
                        }}>
                          {pct == null ? "—" : `${cov.with_control_id}/${cov.total} (${pct}%)`}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

Object.assign(window, { ContinuousMonitoringScreen });
