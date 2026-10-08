/* ============================================================
   Operational Efficiency — the process-mining data Continuous
   Monitoring already computes (variants, conformance, cycle time,
   rework — see process_mining_tool.py), reframed as a Head of
   Operations screen rather than a compliance-monitoring one.

   Nothing here is a new computation: it reuses the same
   GET /process-mining/* endpoints and the same ProcessVariantsView /
   ProcessConformanceView / ProcessCycleTimeView components
   continuous-monitoring-viz.jsx already built. What's new is the
   headline framing (bottleneck/rework-first, not conformance-first)
   and the Head of Operations AI brief at the bottom, which narrates
   this screen's own real numbers — never the risk register.
   ============================================================ */
import { useEffect, useMemo, useState } from "react";
import {
  ProcessVariantsView, ProcessConformanceView, ProcessCycleTimeView, useThemeColors,
} from "./continuous-monitoring-viz.jsx";

const _OPS_TABS = [
  { id: "cycletime", label: "Cycle Time", Comp: ProcessCycleTimeView },
  { id: "rework", label: "Rework" },
  { id: "variants", label: "Variants", Comp: ProcessVariantsView },
  { id: "conformance", label: "Conformance", Comp: ProcessConformanceView },
];

function _fmtHours(h) {
  if (h == null) return "—";
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 48) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

function OpsHeadlineTiles({ summary, theme }) {
  if (!summary) return null;
  const processes = Object.values(summary.processes || {});
  const totalCases = summary.total_cases || 0;
  const avgConformance = processes.length
    ? processes.reduce((s, p) => s + (p.conformance_rate || 0), 0) / processes.length
    : null;
  const avgRework = processes.length
    ? processes.reduce((s, p) => s + (p.rework_rate || 0), 0) / processes.length
    : null;
  const worstBottleneck = processes
    .filter(p => p.bottleneck)
    .sort((a, b) => b.bottleneck.avg_hours - a.bottleneck.avg_hours)[0];

  const tiles = [
    { label: "Cases in window", value: totalCases.toLocaleString(), sub: `${summary.untemplated_cases || 0} untemplated` },
    { label: "Avg conformance", value: avgConformance == null ? "—" : `${(avgConformance * 100).toFixed(0)}%`, sub: "across templated processes" },
    { label: "Avg rework rate", value: avgRework == null ? "—" : `${(avgRework * 100).toFixed(0)}%`, sub: "cases that revisited a step" },
    {
      label: "Worst bottleneck", value: worstBottleneck ? _fmtHours(worstBottleneck.bottleneck.avg_hours) : "—",
      sub: worstBottleneck ? `${worstBottleneck.bottleneck.source} → ${worstBottleneck.bottleneck.target}` : "no data yet",
    },
  ];

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 18 }}>
      {tiles.map(t => (
        <div key={t.label} style={{ flex: "1 1 180px", minWidth: 160, background: "var(--surface-2)",
          border: "1px solid var(--line)", borderRadius: 8, padding: "10px 14px" }}>
          <div style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4 }}>{t.label}</div>
          <div style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{t.value}</div>
          <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 2 }}>{t.sub}</div>
        </div>
      ))}
    </div>
  );
}

function OpsProcessTable({ summary }) {
  const processes = Object.entries(summary?.processes || {});
  if (!processes.length) {
    return <Empty>No case-tracked process activity in this window yet.</Empty>;
  }
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5, marginBottom: 20 }}>
      <thead>
        <tr style={{ borderBottom: "1px solid var(--line)" }}>
          {["Process", "Cases", "Conformance", "Rework", "Bottleneck", "Avg duration"].map(h => (
            <th key={h} style={{ textAlign: "left", padding: "6px 8px", color: "var(--ink-3)", fontSize: 10, fontWeight: 500 }}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {processes.map(([id, p]) => (
          <tr key={id} style={{ borderBottom: "1px solid var(--line-2)" }}>
            <td style={{ padding: "7px 8px", fontWeight: 600 }}>{p.label}</td>
            <td style={{ padding: "7px 8px" }}>{p.case_count}</td>
            <td style={{ padding: "7px 8px", color: p.conformance_rate >= 0.9 ? "var(--green-ink)" : p.conformance_rate >= 0.6 ? "var(--amber-ink)" : "var(--red-ink)" }}>
              {p.conformance_rate == null ? "—" : `${(p.conformance_rate * 100).toFixed(0)}%`}
            </td>
            <td style={{ padding: "7px 8px", color: p.rework_rate > 0.1 ? "var(--red-ink)" : "var(--ink-2)" }}>
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

// Rework has no dedicated view in continuous-monitoring-viz.jsx (that file's
// tabs cover variants/conformance/cycle-time only) — built here directly
// from GET /process-mining/rework rather than adding a view export that
// screen never needed.
function OpsReworkView({ days, process }) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  useEffect(() => {
    setState(s => ({ ...s, loading: true }));
    window.MCP.pmRework(days, process)
      .then(d => setState({ data: d, loading: false, error: null }))
      .catch(e => setState({ data: null, loading: false, error: e.message || String(e) }));
  }, [days, process]);
  const { data, loading, error } = state;

  if (loading && !data) return <div style={{ padding: 20, color: "var(--ink-3)", fontSize: 11.5 }}>Loading…</div>;
  if (error) return <div style={{ padding: 20, color: "var(--red-ink)", fontSize: 11.5 }}>{error}</div>;
  if (!data || !data.total_cases) return <Empty>No case-tracked transactions in this window yet.</Empty>;

  return (
    <div>
      <div style={{ display: "flex", gap: 14, marginBottom: 16 }}>
        <div style={{ minWidth: 150, padding: "10px 14px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--surface-2)" }}>
          <div style={{ fontSize: 9.5, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Rework rate</div>
          <div style={{ fontSize: 20, fontWeight: 700, color: data.rework_rate > 0.1 ? "var(--red-ink)" : "var(--ink)" }}>
            {data.rework_rate == null ? "—" : `${(data.rework_rate * 100).toFixed(0)}%`}
          </div>
          <div style={{ fontSize: 10, color: "var(--ink-3)" }}>{data.reworked_cases} / {data.total_cases} cases</div>
        </div>
      </div>
      <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
        Reworked cases ({data.cases.length})
      </div>
      {!data.cases.length ? (
        <div style={{ fontSize: 12, color: "var(--green-ink)" }}>No case revisited a completed step in this window.</div>
      ) : data.cases.map(c => (
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
  const [process, setProcess] = useState(null);
  const [tab, setTab] = useState("cycletime");
  const [summary, setSummary] = useState(null);
  const [summaryError, setSummaryError] = useState(null);

  useEffect(() => {
    window.MCP.pmSummary(days)
      .then(d => { setSummary(d); setSummaryError(d.note || null); })
      .catch(e => setSummaryError(e.message || String(e)));
  }, [days]);

  const activeTab = _OPS_TABS.find(t => t.id === tab) || _OPS_TABS[0];

  return (
    <div style={{ padding: "0 20px 32px", maxWidth: 1100 }}>
      <div className="panel-head" style={{ paddingLeft: 0, paddingRight: 0 }}>
        <div>
          <div className="kicker">Operations Intelligence</div>
          <div className="panel-title mt-8">Operational Efficiency</div>
          <div className="panel-sub">
            Where process time and rework actually occur, and whether the real workflow still matches the
            documented one — the same process-mining data{" "}
            <a href="#" onClick={e => { e.preventDefault(); onNavigate?.("continuousmonitoring"); }}>
              Continuous Watch
            </a>{" "}
            computes, reframed for operations rather than compliance.
          </div>
        </div>
        <select className="input" style={{ width: 140 }} value={days} onChange={e => setDays(Number(e.target.value))}>
          <option value={7}>Trailing 7d</option>
          <option value={30}>Trailing 30d</option>
          <option value={90}>Trailing 90d</option>
        </select>
      </div>

      {summaryError && <div className="mono" style={{ fontSize: 10.5, color: "var(--amber-ink)", marginBottom: 10 }}>{summaryError}</div>}
      <OpsHeadlineTiles summary={summary} theme={theme} />
      <OpsProcessTable summary={summary} />

      <div style={{ display: "flex", gap: 6, marginBottom: 14, borderBottom: "1px solid var(--line)" }}>
        {_OPS_TABS.map(t => (
          <button key={t.id} className={"btn btn-sm" + (tab === t.id ? " btn-acc" : "")}
            style={{ borderRadius: "6px 6px 0 0" }} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      {activeTab.Comp ? <activeTab.Comp theme={theme} days={days} /> : <OpsReworkView days={days} process={process} />}

      <OpsBriefPanel days={days} process={process} summary={summary} />
    </div>
  );
}

Object.assign(window, { OpsEfficiencyScreen });
