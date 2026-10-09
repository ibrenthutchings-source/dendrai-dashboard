/* ============================================================
   Operational Efficiency — the process-mining data Continuous
   Monitoring already computes (variants, conformance, cycle time,
   rework — see process_mining_tool.py), reframed and re-weighted
   for a Head of Operations rather than a compliance-monitoring one.

   Layout follows a deliberate priority order, not four equal tabs:
     1. Headline  — bottleneck (where time is lost) + SLA breaches
                    (what it costs). The two measures a Head of Ops
                    asks for first.
     2. Alerts    — rework rate and happy-path/template drift, shown
                    only when they cross a threshold. Leading
                    indicators: a process drifting or reworking today
                    is a bottleneck/breach next week.
     3. Cycle Time — full detail, visible by default (Tier 1).
     4. Variants  — full detail, visible but secondary (Tier 2).
     5. Case Flow Graph — volume drill-down, collapsed by default.
                    Answers "how much volume is backed up here" only
                    after Cycle Time has already said where "here" is.

   Conformance (missing/extra/out-of-order step deviations) is
   deliberately NOT surfaced here — it's audit/ITGC-framed, not an
   operations question, except for the repeated_step case, which is
   rework and already covered by the rework alert. It still lives on
   the Continuous Monitoring screen for that audience.

   Nothing here is a new computation: same GET /process-mining/* and
   GET /observability/events endpoints Continuous Monitoring already
   uses, same ProcessCycleTimeView/ProcessVariantsView/CaseFlowGraph
   components, just re-prioritized and paired with SLA data (GET
   /itsm/tickets) that screen never had.
   ============================================================ */
import { useEffect, useMemo, useState } from "react";
import {
  ProcessCycleTimeView, ProcessVariantsView, CaseFlowGraph, useThemeColors,
} from "./continuous-monitoring-viz.jsx";

function _fmtHours(h) {
  if (h == null) return "—";
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 48) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

function _obsBase() {
  return (window.MCP_API_BASE || "/api/mcp") + "/observability";
}

// Minimal breach count — just enough for the headline tile. The full
// aging-bucket breakdown lives in sla-tracker.jsx; duplicating that whole
// UI here would fight the "SLA breaches" tile's job, which is a single
// number, not a table.
function _slaBreachCount(tickets) {
  return tickets.filter(t => t.sla_breached_at && !["closed", "cancelled"].includes(t.status)).length;
}

function HeadlineTile({ label, value, sub, color, big }) {
  return (
    <div style={{
      flex: "1 1 220px", minWidth: 200, background: "var(--surface-2)", border: "1px solid var(--line)",
      borderRadius: 8, padding: "14px 18px",
    }}>
      <div style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: big ? 26 : 18, fontWeight: 700, fontVariantNumeric: "tabular-nums", color }}>{value}</div>
      {sub && <div style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

function OpsHeadline({ summary, breachCount, breachError }) {
  const processes = Object.values(summary?.processes || {});
  const worstBottleneck = processes
    .filter(p => p.bottleneck)
    .sort((a, b) => b.bottleneck.avg_hours - a.bottleneck.avg_hours)[0];
  const totalCases = summary?.total_cases || 0;

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 20 }}>
      <HeadlineTile big label="Worst bottleneck — where time is lost"
        value={worstBottleneck ? _fmtHours(worstBottleneck.bottleneck.avg_hours) : "—"}
        color={worstBottleneck ? "var(--red-ink)" : undefined}
        sub={worstBottleneck
          ? `${worstBottleneck.bottleneck.source} → ${worstBottleneck.bottleneck.target} (${worstBottleneck.label})`
          : "no case-tracked data yet"} />
      <HeadlineTile big label="SLA breaches — what it's costing"
        value={breachError ? "—" : breachCount}
        color={!breachError && breachCount > 0 ? "var(--red-ink)" : "var(--green-ink)"}
        sub={breachError || "open tickets past their remediation SLA"} />
      <HeadlineTile label="Cases in window" value={totalCases.toLocaleString()}
        sub={`${summary?.untemplated_cases || 0} untemplated`} />
    </div>
  );
}

// Rework and happy-path/template drift, both leading indicators — surfaced
// as alerts (only when they cross a threshold), not as permanent tiles,
// since a quiet process shouldn't compete for headline space with one
// that's actually degrading.
const _REWORK_ALERT_THRESHOLD = 0.08;

function OpsAlerts({ summary, driftProcesses, onShowRework }) {
  const processes = Object.entries(summary?.processes || {});
  const reworkFlags = processes.filter(([, p]) => (p.rework_rate || 0) > _REWORK_ALERT_THRESHOLD);

  if (!reworkFlags.length && !driftProcesses.length) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 20 }}>
      {reworkFlags.map(([id, p]) => (
        <div key={id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
          borderRadius: 6, border: "1px solid var(--amber)", background: "var(--amber-soft)" }}>
          <span style={{ fontSize: 11, flex: 1 }}>
            <b>{p.label}</b>: {(p.rework_rate * 100).toFixed(0)}% of cases revisited a completed step —
            rework is eating capacity here.
          </span>
          <button className="btn btn-sm" onClick={() => onShowRework(id)}>View reworked cases ▸</button>
        </div>
      ))}
      {driftProcesses.map(p => (
        <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
          borderRadius: 6, border: "1px solid var(--amber)", background: "var(--amber-soft)" }}>
          <span style={{ fontSize: 11, flex: 1 }}>
            <b>{p.label}</b>: the most common path no longer matches the documented process template —
            the "normal" way of working has drifted.
          </span>
        </div>
      ))}
    </div>
  );
}

// Rework has no dedicated view in continuous-monitoring-viz.jsx — built
// here directly from GET /process-mining/rework, same as before.
function OpsReworkDetail({ days, process, onClose }) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  useEffect(() => {
    setState(s => ({ ...s, loading: true }));
    window.MCP.pmRework(days, process)
      .then(d => setState({ data: d, loading: false, error: null }))
      .catch(e => setState({ data: null, loading: false, error: e.message || String(e) }));
  }, [days, process]);
  const { data, loading, error } = state;

  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8,
      padding: "14px 16px", marginBottom: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
        <div className="kicker">Reworked cases{process ? ` — ${process}` : ""}</div>
        <button className="btn btn-sm" onClick={onClose}>Close</button>
      </div>
      {loading && !data ? <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>Loading…</div>
       : error ? <div style={{ fontSize: 11.5, color: "var(--red-ink)" }}>{error}</div>
       : !data?.cases?.length ? <Empty>No case revisited a completed step in this window.</Empty>
       : data.cases.map(c => (
        <div key={c.case_id} style={{ padding: "8px 10px", marginBottom: 5, borderRadius: 6, border: "1px solid var(--line)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 11.5 }}>
            <span style={{ fontWeight: 700 }}>{c.case_id}</span>
            <span style={{ color: "var(--ink-4)" }}>{c.process || "untemplated"}</span>
          </div>
          <div style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 3 }}>Revisited: {c.repeated_steps.join(", ")}</div>
        </div>
      ))}
    </div>
  );
}

function OpsProcessTable({ summary }) {
  const processes = Object.entries(summary?.processes || {});
  if (!processes.length) return <Empty>No case-tracked process activity in this window yet.</Empty>;
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5, marginBottom: 24 }}>
      <thead>
        <tr style={{ borderBottom: "1px solid var(--line)" }}>
          {["Process", "Cases", "Rework", "Bottleneck", "Avg duration"].map(h => (
            <th key={h} style={{ textAlign: "left", padding: "6px 8px", color: "var(--ink-3)", fontSize: 10, fontWeight: 500 }}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {processes.map(([id, p]) => (
          <tr key={id} style={{ borderBottom: "1px solid var(--line-2)" }}>
            <td style={{ padding: "7px 8px", fontWeight: 600 }}>{p.label}</td>
            <td style={{ padding: "7px 8px" }}>{p.case_count}</td>
            <td style={{ padding: "7px 8px", color: p.rework_rate > _REWORK_ALERT_THRESHOLD ? "var(--red-ink)" : "var(--ink-2)" }}>
              {p.rework_rate == null ? "—" : `${(p.rework_rate * 100).toFixed(0)}%`}
            </td>
            <td style={{ padding: "7px 8px", color: "var(--ink-2)", fontSize: 10.5 }}>
              {p.bottleneck ? `${p.bottleneck.source} → ${p.bottleneck.target} (${_fmtHours(p.bottleneck.avg_hours)})` : "—"}
            </td>
            <td style={{ padding: "7px 8px" }}>{_fmtHours(p.avg_case_duration_hours)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function OpsBriefPanel({ days, process, summary }) {
  const [state, setState] = useState({ brief: null, loading: false, error: null });

  async function generate() {
    setState(s => ({ ...s, loading: true, error: null }));
    try {
      const [cycleTimes, rework, variants] = await Promise.all([
        window.MCP.pmCycleTimes(days, process),
        window.MCP.pmRework(days, process),
        window.MCP.pmVariants(days, process),
      ]);
      const brief = await window.MCP.aiOpsEfficiencyBrief(days, process, summary, cycleTimes, rework, variants.variants || []);
      setState({ brief, loading: false, error: null });
    } catch (e) {
      setState({ brief: null, loading: false, error: e.message || "AI brief unavailable" });
    }
  }

  const aiAvailable = typeof window !== "undefined" && window.MCP?.aiOpsEfficiencyBrief;
  if (!aiAvailable) return null;

  return (
    <div style={{ marginTop: 24, paddingTop: 20, borderTop: "1px solid var(--line)" }}>
      <SectionLabel right={
        <button className="btn btn-sm" onClick={generate} disabled={state.loading}>
          <Icon name="spark" size={10}/> {state.loading ? "Generating…" : state.brief ? "Regenerate" : "Generate Head of Operations Brief"}
        </button>
      }>Head of Operations Brief</SectionLabel>
      {state.error && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", margin: "4px 0" }}>{state.error}</div>}
      {state.brief ? (
        <>
          <AiReviewBanner review={state.brief._review} />
          <PersonaBriefBody brief={state.brief} />
        </>
      ) : !state.loading && (
        <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
          Narrates this screen's own bottleneck, rework, and variant-drift numbers for an operations audience — never the risk register.
        </div>
      )}
    </div>
  );
}

export function OpsEfficiencyScreen({ onNavigate } = {}) {
  const theme = useThemeColors();
  const [days, setDays] = useState(30);
  const [summary, setSummary] = useState(null);
  const [summaryError, setSummaryError] = useState(null);
  const [tickets, setTickets] = useState(null);
  const [breachError, setBreachError] = useState(null);
  const [driftProcesses, setDriftProcesses] = useState([]);
  const [reworkDetailFor, setReworkDetailFor] = useState(null);  // process id, or null
  const [showFlowGraph, setShowFlowGraph] = useState(false);
  const [rawEvents, setRawEvents] = useState(null);
  const [eventsError, setEventsError] = useState(null);

  useEffect(() => {
    window.MCP.pmSummary(days)
      .then(d => { setSummary(d); setSummaryError(d.note || null); })
      .catch(e => setSummaryError(e.message || String(e)));
  }, [days]);

  useEffect(() => {
    window.MCP.itsmListTickets({ limit: 500 })
      .then(d => { setTickets(d.tickets || []); setBreachError(null); })
      .catch(e => setBreachError(e.message || String(e)));
  }, [days]);

  // Drift check: for each active process, is the most common path (the
  // happy path) also the documented one (is_canonical)? One extra call per
  // active process — summary() doesn't carry is_canonical, only variant_
  // analysis() does. Process counts here are small (the template catalog
  // has 11 entries total), so this stays cheap.
  useEffect(() => {
    const ids = Object.keys(summary?.processes || {});
    if (!ids.length) { setDriftProcesses([]); return; }
    let cancelled = false;
    Promise.all(ids.map(id => window.MCP.pmVariants(days, id).then(d => ({ id, variants: d.variants || [] }))))
      .then(results => {
        if (cancelled) return;
        const drifted = results
          .filter(r => r.variants[0] && r.variants[0].is_happy_path && r.variants[0].process && !r.variants[0].is_canonical)
          .map(r => ({ id: r.id, label: summary.processes[r.id].label }));
        setDriftProcesses(drifted);
      })
      .catch(() => { if (!cancelled) setDriftProcesses([]); });
    return () => { cancelled = true; };
  }, [summary, days]);

  useEffect(() => {
    if (!showFlowGraph || rawEvents) return;
    fetch(`${_obsBase()}/events?days=${days}&limit=5000`, { credentials: "include" })
      .then(res => res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`)))
      .then(d => { setRawEvents(d.events || d || []); setEventsError(null); })
      .catch(e => setEventsError(e.message || String(e)));
  }, [showFlowGraph, days, rawEvents]);

  const breachCount = tickets ? _slaBreachCount(tickets) : 0;

  return (
    <div style={{ padding: "0 20px 32px", maxWidth: 1100 }}>
      <div className="panel-head" style={{ paddingLeft: 0, paddingRight: 0 }}>
        <div>
          <div className="kicker">Operations Intelligence</div>
          <div className="panel-title mt-8">Operational Efficiency</div>
          <div className="panel-sub">
            Where process time and SLA risk actually accumulate, ranked the way a Head of Operations would ask for
            it — bottleneck and breaches first, rework and process drift as early warnings, volume as a drill-down.
            Same process-mining data{" "}
            <a href="#" onClick={e => { e.preventDefault(); onNavigate?.("continuousmonitoring"); }}>
              Continuous Watch
            </a>{" "}
            computes; conformance detail stays there — it's an audit question, not an operations one.
          </div>
        </div>
        <select className="input" style={{ width: 140 }} value={days} onChange={e => setDays(Number(e.target.value))}>
          <option value={7}>Trailing 7d</option>
          <option value={30}>Trailing 30d</option>
          <option value={90}>Trailing 90d</option>
        </select>
      </div>

      {summaryError && <div className="mono" style={{ fontSize: 10.5, color: "var(--amber-ink)", marginBottom: 10 }}>{summaryError}</div>}

      <OpsHeadline summary={summary} breachCount={breachCount} breachError={breachError} />
      <OpsAlerts summary={summary} driftProcesses={driftProcesses} onShowRework={setReworkDetailFor} />
      {reworkDetailFor != null && (
        <OpsReworkDetail days={days} process={reworkDetailFor} onClose={() => setReworkDetailFor(null)} />
      )}

      <OpsProcessTable summary={summary} />

      <SectionLabel>Cycle Time — where time actually accumulates</SectionLabel>
      <div style={{ marginBottom: 24 }}>
        <ProcessCycleTimeView theme={theme} days={days} />
      </div>

      <SectionLabel>Variants — is the real process still the documented one?</SectionLabel>
      <div style={{ marginBottom: 24 }}>
        <ProcessVariantsView theme={theme} days={days} />
      </div>

      <div style={{ marginBottom: 24 }}>
        <button className="btn btn-sm" onClick={() => setShowFlowGraph(s => !s)}>
          {showFlowGraph ? "Hide" : "Show"} Case Flow Graph — volume drill-down ▾
        </button>
        {showFlowGraph && (
          <div style={{ marginTop: 10 }}>
            {eventsError && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 8 }}>{eventsError}</div>}
            <CaseFlowGraph theme={theme} days={days} rawEvents={rawEvents || []} loading={!rawEvents && !eventsError} error={eventsError} />
          </div>
        )}
      </div>

      <OpsBriefPanel days={days} process={null} summary={summary} />
    </div>
  );
}

Object.assign(window, { OpsEfficiencyScreen });
