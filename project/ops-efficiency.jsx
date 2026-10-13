/* ============================================================
   Operational Efficiency — the process-mining data Continuous
   Monitoring already computes (variants, conformance, cycle time,
   rework — see process_mining_tool.py), reframed and re-weighted
   for a Head of Operations rather than a compliance-monitoring one.

   Layout: a fixed, always-visible summary first, then the detail split
   into tabs (added once the screen grew to 7 distinct views stacked on
   top of each other — a tab bar keeps each one scannable instead of a
   long scroll).
     1. Headline  — bottleneck (where time is lost) + SLA breaches
                    (what it costs). The two measures a Head of Ops
                    asks for first. Always visible, above the tabs.
     2. Alerts    — rework rate and happy-path/template drift, shown
                    only when they cross a threshold. Leading
                    indicators: a process drifting or reworking today
                    is a bottleneck/breach next week. Always visible.
     3. Process table — one row per active process. Always visible.
     Tabs (OpsTabBar), in the same priority order the screen used to
     stack them in, Tier 1 first:
       Cycle Time    — full detail (default tab).
       Value Stream Map — the same Cycle Time edges, re-labeled
                    canonical (forward, template order) vs. deviation
                    (rework/skip/off-template), rolled into a process
                    cycle efficiency ratio. See process_mining_tool.py's
                    value_stream_map for why this isn't a textbook
                    touch-time VSM (no per-step start/end timestamp
                    exists to measure that honestly).
       Pareto        — those same deviation edges, ranked by total hours
                    lost with the 80/20 vital-few cutoff flagged — "fix
                    these first," not a flat deviation list.
       Control Chart — case-duration trend across trailing periods
                    against a center line + UCL/LCL, so drift shows up
                    before it becomes next week's bottleneck.
       Root Cause    — 5 Whys / Fishbone. AI-generated HYPOTHESES only,
                    always seeded from a real bottleneck picked off a
                    dropdown (never typed-in free text), every individual
                    why/cause self-labeled evidence vs. assumption by the
                    model, never auto-written into the risk register.
                    See ai_endpoints.py's five_whys/fishbone for the
                    full guardrail rationale.
       Variants      — Tier 2 detail.
       Case Flow Graph — volume drill-down; its rawEvents fetch is lazy,
                    firing only once this tab is actually selected.
                    Answers "how much volume is backed up here" only
                    after Cycle Time has already said where "here" is.

   Conformance (missing/extra/out-of-order step deviations) is
   deliberately NOT surfaced here — it's audit/ITGC-framed, not an
   operations question, except for the repeated_step case, which is
   rework and already covered by the rework alert. It still lives on
   the Continuous Monitoring screen for that audience.

   Nothing here is a new computation beyond 3b-3d: same GET
   /process-mining/* and GET /observability/events endpoints Continuous
   Monitoring already uses, same ProcessCycleTimeView/ProcessVariantsView/
   CaseFlowGraph/ValueStreamMapView/ParetoView/CycleTimeControlChartView
   components (all defined in continuous-monitoring-viz.jsx), just
   re-prioritized and paired with SLA data (GET /itsm/tickets) that
   screen never had.
   ============================================================ */
import { useEffect, useMemo, useState } from "react";
import {
  ProcessCycleTimeView, ProcessVariantsView, CaseFlowGraph, useThemeColors,
  ValueStreamMapView, ParetoView, CycleTimeControlChartView,
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

// ── Root Cause Investigation (5 Whys / Fishbone) ────────────────────────────
//
// Guardrails (see ai_endpoints.py's five_whys/fishbone for the full
// rationale): the seed is always picked from a dropdown of REAL,
// already-detected findings (bottlenecks straight from `summary` — no typed-
// in problem statement is accepted), every why/cause the model writes is
// schema-forced to self-label "evidence" or "assumption", and this panel
// never auto-writes anything into the risk register — it only ever renders
// what the model returned, behind the same pending/reviewed banner every
// other AI narrative in this app uses, for a human to read and act on (or
// not) themselves.

const _CAUSAL_DISCLAIMER =
  "AI-generated hypothesis from real process-mining data — not a measured cause. Verify with the process owner before acting on it.";

function CausalDisclaimer() {
  return (
    <div style={{
      display: "flex", alignItems: "flex-start", gap: 8, padding: "8px 12px", marginBottom: 10,
      borderRadius: 6, border: "1px solid var(--amber)", background: "var(--amber-soft)",
      fontSize: 10.5, color: "var(--amber-ink)", fontWeight: 600,
    }}>
      <span>⚠</span><span>{_CAUSAL_DISCLAIMER}</span>
    </div>
  );
}

function BasisTag({ basis }) {
  const isAssumption = basis === "assumption";
  return (
    <span style={{
      fontSize: 8.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.03em",
      color: isAssumption ? "var(--ink-4)" : "var(--green-ink)", marginLeft: 5,
    }}>({basis})</span>
  );
}

function FiveWhysResult({ data }) {
  return (
    <div>
      <AiReviewBanner review={data._review} />
      <CausalDisclaimer />
      <div style={{ fontSize: 11.5, fontWeight: 600, marginBottom: 10 }}>{data.problem_statement}</div>
      {data.whys.map((w, i) => (
        <div key={i} style={{
          marginBottom: 8, paddingLeft: 10,
          borderLeft: `2px solid ${w.basis === "assumption" ? "var(--ink-4)" : "var(--green-ink)"}`,
        }}>
          <div style={{ fontSize: 11, fontWeight: 600 }}>Why {i + 1}: {w.question}</div>
          <div style={{ fontSize: 11, marginTop: 2 }}>{w.answer}<BasisTag basis={w.basis} /></div>
        </div>
      ))}
      <div style={{ marginTop: 10, padding: "8px 10px", borderRadius: 6, background: "var(--surface-2)" }}>
        <div style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.04em" }}>Root cause hypothesis</div>
        <div style={{ fontSize: 11.5, marginTop: 2 }}>{data.root_cause_hypothesis}</div>
      </div>
      <div style={{ marginTop: 8, fontSize: 11, color: "var(--acc)" }}>
        ▸ Recommended next step (go verify — don't act on the hypothesis itself): {data.recommended_next_step}
      </div>
    </div>
  );
}

function FishboneResult({ data }) {
  return (
    <div>
      <AiReviewBanner review={data._review} />
      <CausalDisclaimer />
      <div style={{ fontSize: 11.5, fontWeight: 600, marginBottom: 10 }}>{data.problem_statement}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
        {data.categories.map(cat => (
          <div key={cat.name} style={{ flex: "1 1 200px", minWidth: 180 }}>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--ink-3)", marginBottom: 4 }}>{cat.name}</div>
            {cat.causes.map((c, i) => (
              <div key={i} style={{ fontSize: 10.5, marginBottom: 3, fontStyle: c.basis === "assumption" ? "italic" : "normal" }}>
                • {c.cause}<BasisTag basis={c.basis} />
              </div>
            ))}
          </div>
        ))}
      </div>
      <div style={{ marginTop: 12, padding: "8px 10px", borderRadius: 6, background: "var(--surface-2)" }}>
        <div style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.04em" }}>Investigate first</div>
        <ul style={{ margin: "4px 0 0 16px", fontSize: 11, padding: 0 }}>
          {data.root_cause_candidates.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      </div>
    </div>
  );
}

function RootCauseInvestigation({ days, summary }) {
  const [seedKey, setSeedKey] = useState("");
  const [whys, setWhys] = useState({ data: null, loading: false, error: null });
  const [fishbone, setFishbone] = useState({ data: null, loading: false, error: null });

  // Every option comes straight from summary's own per-process bottleneck —
  // already-computed real numbers, never a free-text field the caller fills
  // in. See process_mining_tool.py's cycle_time_stats for how `bottleneck`
  // is derived.
  const seeds = useMemo(() => Object.entries(summary?.processes || {})
    .filter(([, p]) => p.bottleneck)
    .map(([id, p]) => ({
      key: id, process: id, process_label: p.label,
      source: p.bottleneck.source, target: p.bottleneck.target,
      avg_hours: p.bottleneck.avg_hours, count: p.bottleneck.count, basis: "bottleneck",
    })), [summary]);

  const seed = seeds.find(s => s.key === seedKey) || null;

  useEffect(() => {
    setWhys({ data: null, loading: false, error: null });
    setFishbone({ data: null, loading: false, error: null });
  }, [seedKey]);

  async function generateWhys() {
    if (!seed) return;
    setWhys({ data: null, loading: true, error: null });
    try {
      const { process, process_label, source, target, avg_hours, count, basis } = seed;
      const d = await window.MCP.aiFiveWhys({ process, process_label, source, target, avg_hours, count, basis }, days);
      setWhys({ data: d, loading: false, error: null });
    } catch (e) {
      setWhys({ data: null, loading: false, error: e.message || "AI 5 Whys unavailable" });
    }
  }

  async function generateFishbone() {
    if (!seed) return;
    setFishbone({ data: null, loading: true, error: null });
    try {
      const { process, process_label, source, target, avg_hours, count, basis } = seed;
      const d = await window.MCP.aiFishbone({ process, process_label, source, target, avg_hours, count, basis }, days);
      setFishbone({ data: d, loading: false, error: null });
    } catch (e) {
      setFishbone({ data: null, loading: false, error: e.message || "AI Fishbone unavailable" });
    }
  }

  const aiAvailable = typeof window !== "undefined" && window.MCP?.aiFiveWhys;
  if (!aiAvailable) return null;

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "14px 16px" }}>
        {!seeds.length ? (
            <Empty>No bottleneck identified yet in this window — nothing to investigate.</Empty>
          ) : (
            <>
              <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 10 }}>
                Pick a real, already-detected finding to investigate — this never accepts a typed-in problem statement.
              </div>
              <select className="input" style={{ width: "100%", marginBottom: 12 }} value={seedKey} onChange={e => setSeedKey(e.target.value)}>
                <option value="">Select a finding…</option>
                {seeds.map(s => (
                  <option key={s.key} value={s.key}>
                    {s.process_label}: {s.source} → {s.target} ({_fmtHours(s.avg_hours)} avg, {s.count} case{s.count !== 1 ? "s" : ""})
                  </option>
                ))}
              </select>

              {seed && (
                <>
                  <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                    <button className="btn btn-sm btn-acc" onClick={generateWhys} disabled={whys.loading}>
                      {whys.loading ? "Generating…" : "Generate 5 Whys"}
                    </button>
                    <button className="btn btn-sm btn-acc" onClick={generateFishbone} disabled={fishbone.loading}>
                      {fishbone.loading ? "Generating…" : "Generate Fishbone"}
                    </button>
                  </div>

                  {whys.error && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 10 }}>{whys.error}</div>}
                  {whys.data && (
                    <div style={{ marginBottom: 20, paddingBottom: 16, borderBottom: "1px solid var(--line)" }}>
                      <div className="kicker" style={{ marginBottom: 8 }}>5 Whys</div>
                      <FiveWhysResult data={whys.data} />
                    </div>
                  )}

                  {fishbone.error && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 10 }}>{fishbone.error}</div>}
                  {fishbone.data && (
                    <div>
                      <div className="kicker" style={{ marginBottom: 8 }}>Fishbone</div>
                      <FishboneResult data={fishbone.data} />
                    </div>
                  )}
                </>
              )}
            </>
          )}
      </div>
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

// Detail tabs, in the same priority order the screen used to stack them in
// (see module docstring) — Cycle Time first (Tier 1), Variants/Case Flow
// Graph last (Tier 2/volume drill-down). Headline, Alerts, and the process
// table stay OUTSIDE the tabs: that's the "glance at this before anything
// else" summary every tab's detail elaborates on, not detail itself.
const _OPS_TABS = [
  { id: "cycletime", label: "Cycle Time" },
  { id: "vsm", label: "Value Stream Map" },
  { id: "pareto", label: "Pareto" },
  { id: "control", label: "Control Chart" },
  { id: "rootcause", label: "Root Cause" },
  { id: "variants", label: "Variants" },
  { id: "caseflow", label: "Case Flow Graph" },
];

function OpsTabBar({ tab, setTab }) {
  return (
    <div style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--line)", marginBottom: 16, flexWrap: "wrap" }}>
      {_OPS_TABS.map(t => (
        <button key={t.id}
          onClick={() => setTab(t.id)}
          style={{
            padding: "8px 14px", fontSize: 11, fontWeight: tab === t.id ? 600 : 400,
            background: "none", border: "none",
            borderBottom: tab === t.id ? "2px solid var(--acc-ink, var(--ink))" : "2px solid transparent",
            color: tab === t.id ? "var(--ink)" : "var(--ink-4)", cursor: "pointer", whiteSpace: "nowrap",
          }}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function OpsEfficiencyScreen({ onNavigate } = {}) {
  const theme = useThemeColors();
  const [days, setDays] = useState(30);
  const [tab, setTab] = useState("cycletime");
  const [summary, setSummary] = useState(null);
  const [summaryError, setSummaryError] = useState(null);
  const [tickets, setTickets] = useState(null);
  const [breachError, setBreachError] = useState(null);
  const [driftProcesses, setDriftProcesses] = useState([]);
  const [reworkDetailFor, setReworkDetailFor] = useState(null);  // process id, or null
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
    if (tab !== "caseflow" || rawEvents) return;
    fetch(`${_obsBase()}/events?days=${days}&limit=5000`, { credentials: "include" })
      .then(res => res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`)))
      .then(d => { setRawEvents(d.events || d || []); setEventsError(null); })
      .catch(e => setEventsError(e.message || String(e)));
  }, [tab, days, rawEvents]);

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

      <OpsTabBar tab={tab} setTab={setTab} />

      {tab === "cycletime" && (
        <>
          <SectionLabel>Cycle Time — where time actually accumulates</SectionLabel>
          <div style={{ marginBottom: 24 }}>
            <ProcessCycleTimeView theme={theme} days={days} />
          </div>
        </>
      )}

      {tab === "vsm" && (
        <>
          <SectionLabel>Value Stream Map — how much of that time was forward progress?</SectionLabel>
          <div style={{ marginBottom: 24 }}>
            <ValueStreamMapView theme={theme} days={days} />
          </div>
        </>
      )}

      {tab === "pareto" && (
        <>
          <SectionLabel>Pareto — which deviations to fix first</SectionLabel>
          <div style={{ marginBottom: 24 }}>
            <ParetoView theme={theme} days={days} />
          </div>
        </>
      )}

      {tab === "control" && (
        <>
          <SectionLabel>Control Chart — is cycle time drifting?</SectionLabel>
          <div style={{ marginBottom: 24 }}>
            <CycleTimeControlChartView theme={theme} />
          </div>
        </>
      )}

      {tab === "rootcause" && (
        <>
          <SectionLabel>Root Cause Investigation — 5 Whys / Fishbone</SectionLabel>
          <RootCauseInvestigation days={days} summary={summary} />
        </>
      )}

      {tab === "variants" && (
        <>
          <SectionLabel>Variants — is the real process still the documented one?</SectionLabel>
          <div style={{ marginBottom: 24 }}>
            <ProcessVariantsView theme={theme} days={days} />
          </div>
        </>
      )}

      {tab === "caseflow" && (
        <>
          <SectionLabel>Case Flow Graph — volume drill-down</SectionLabel>
          <div style={{ marginBottom: 24 }}>
            {eventsError && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 8 }}>{eventsError}</div>}
            <CaseFlowGraph theme={theme} days={days} rawEvents={rawEvents || []} loading={!rawEvents && !eventsError} error={eventsError} />
          </div>
        </>
      )}

      <OpsBriefPanel days={days} process={null} summary={summary} />
    </div>
  );
}

Object.assign(window, { OpsEfficiencyScreen });
