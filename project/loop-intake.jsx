/* ============================================================
   Run Loop: live progress + live Signal Intake
   - LoopProgress: a progress bar that checks off each milestone as the loop
     passes it (data sources first, then stages and review gates).
   - IntakePanels: the signal inputs that matter to the register — RSS feed
     detail, the risk register, the scoring methodology and the risk-score
     forecast — shown AS SOON AS each is available, not after Stage 1 ends.
   Pure presentation. The register shown here is the same deterministic
   RISK_ENGINE.scoreTaxonomy output, run over whatever evidence has arrived.
   ============================================================ */
const _ink = (s) => (typeof window !== "undefined" && window.scoreColorInk ? window.scoreColorInk(s) : "var(--ink)");
const RE = () => (typeof window !== "undefined" ? window.RISK_ENGINE : null);

export const CE_MULT = { STRONG: 0.80, ADEQUATE: 0.95, WEAK: 1.10, NONE: 1.20 };
export function forecastRisk(risk, quarters = 4) {
  const mult = CE_MULT[risk.ce] ?? 1.0;
  return Array.from({ length: quarters }, (_, i) =>
    +Math.min(25, Math.max(0, risk.score + risk.velocity * mult * Math.pow(0.85, i))).toFixed(1));
}

// ── Milestones ───────────────────────────────────────────────────────────────
// Data-source milestones are driven imperatively by the loop (status: pending |
// active | done | skipped | failed). Stage / gate milestones are derived from
// stageState / gateState so they can never disagree with the pipeline itself.
export const INTAKE_MILESTONES = [
  { id: "fa",    label: "Financial analysis" },
  { id: "rss",   label: "Industry RSS" },
  { id: "comp",  label: "Compliance feeds" },
  { id: "fred",  label: "FRED macro" },
  { id: "mat",   label: "Material accounts" },
  { id: "rf",    label: "10-K risk factors" },
  { id: "k8",    label: "8-K routing" },
  { id: "proxy", label: "DEF 14A governance" },
  { id: "comment", label: "SEC comment letters" },
  { id: "rfc",   label: "Item 1A / 9A changes" },
  { id: "peers", label: "SIC peers" },
];

const STAGE_MILESTONES = [
  { id: "s1", label: "Signal intake complete", stage: "s1" },
  { id: "s2", label: "Risk register scored", stage: "s2" },
  { id: "g1", label: "Gate 1 · risk review", gate: "g1" },
  { id: "s3", label: "Audit scope", stage: "s3" },
  { id: "g2", label: "Gate 2 · scope review", gate: "g2" },
  { id: "s4", label: "Findings → MAPs", stage: "s4" },
  { id: "s5", label: "Closure evidence", stage: "s5" },
  { id: "s6", label: "Calibration", stage: "s6" },
];

export function deriveMilestones(progress, stageState = {}, gateState = {}, hitl = {}) {
  const intake = (progress?.items || []);
  const stages = STAGE_MILESTONES.map(m => {
    if (m.stage) {
      const st = stageState[m.stage];
      return { ...m, status: st === "done" ? "done" : st === "running" || st === "waiting" ? "active" : "pending" };
    }
    const g = gateState[m.gate];
    return { ...m, status: g === "approved" || g === "overridden" ? "done" : g === "pending" ? "active" : "pending",
             note: g === "pending" ? "awaiting reviewer" : g === "overridden" ? "overridden" : undefined };
  });
  return { intake, stages };
}

const GLYPH = { done: "✓", active: "⟳", pending: "○", skipped: "–", failed: "✕" };
const COLOR = {
  done: "var(--green-ink)", active: "var(--amber-ink)", pending: "var(--ink-4)",
  skipped: "var(--ink-4)", failed: "var(--red-ink)",
};
const BG = {
  done: "var(--green-soft)", active: "var(--amber-soft)", pending: "transparent",
  skipped: "transparent", failed: "var(--red-soft)",
};

export function LoopProgress({ progress, stageState, gateState, running }) {
  if (!progress) return null;
  const { intake, stages } = deriveMilestones(progress, stageState, gateState);
  const all = [...intake, ...stages];
  const settled = all.filter(m => m.status === "done" || m.status === "skipped" || m.status === "failed").length;
  const pct = Math.round((settled / all.length) * 100);
  const current = all.find(m => m.status === "active");
  const chip = (m) => (
    <span key={m.id} className="mono" title={m.note ? `${m.label} — ${m.note}` : m.label}
      style={{ fontSize: 10, padding: "2px 7px", borderRadius: 4, border: "1px solid var(--line)",
               background: BG[m.status], color: COLOR[m.status], whiteSpace: "nowrap",
               textDecoration: m.status === "skipped" ? "line-through" : "none" }}>
      {GLYPH[m.status]} {m.label}{m.status === "failed" && m.note ? ` — ${m.note}` : ""}
    </span>
  );
  return (
    <div className="stage-detail" style={{ margin: "0 0 10px", padding: "8px 12px" }} data-testid="loop-progress">
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
        <span className="mono" style={{ fontSize: 10, color: "var(--ink-4)", letterSpacing: "0.06em" }}>RUN PROGRESS</span>
        <div style={{ flex: 1, height: 6, background: "var(--surface-2, var(--line))", borderRadius: 3, overflow: "hidden" }}
             role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <div style={{ width: `${pct}%`, height: "100%", background: "var(--acc, var(--green-ink))", transition: "width .3s" }} />
        </div>
        <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-2)", minWidth: 34, textAlign: "right" }}>{pct}%</span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>{intake.map(chip)}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>{stages.map(chip)}</div>
      <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 5 }}>
        {running && current ? <><span className="spin" style={{ width: 9, height: 9, borderWidth: 1.5, marginRight: 5 }} />{current.label}…</>
          : pct === 100 ? "All milestones complete" : running ? "Working…" : "Run stopped"}
      </div>
    </div>
  );
}

// ── Live intake panels ───────────────────────────────────────────────────────
const REL_LABEL = { company: "names the company", industry: "industry-relevant", macro: "macro", ambient: "ambient (no weight)" };
const REL_TONE = { company: "var(--red-ink)", industry: "var(--amber-ink)", macro: "var(--ink-2)", ambient: "var(--ink-4)" };

function Waiting({ children }) {
  return <div className="stage-detail" style={{ fontSize: 11.5, color: "var(--ink-3)" }}><span className="spin" style={{ width: 10, height: 10, borderWidth: 1.5, marginRight: 6 }} />{children}</div>;
}

export function RssFeedDetail({ signals = [], progress }) {
  const rss = signals.filter(s => s.src === "Industry RSS" || s.feedName);
  const status = progress?.items?.find(i => i.id === "rss")?.status;
  if (!rss.length) {
    if (status === "active" || status === "pending") return <Waiting>Waiting for industry RSS feeds…</Waiting>;
    return <div className="stage-detail" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>No RSS signals were returned{status === "skipped" ? " — industry RSS is switched off in the signal sources" : ""}.</div>;
  }
  const byFeed = {};
  rss.forEach(s => { const k = s.feedName || "RSS"; (byFeed[k] = byFeed[k] || []).push(s); });
  const weighted = rss.filter(s => (s.velocity || 0) > 0).length;
  return (
    <div className="stage-detail">
      <h5>RSS feed detail · {rss.length} signals · {weighted} carry weight</h5>
      {Object.entries(byFeed).map(([feed, list]) => (
        <details key={feed} open={list.some(s => (s.velocity || 0) > 0)} style={{ marginBottom: 6 }}>
          <summary style={{ fontSize: 11.5, cursor: "pointer" }}>
            <b>{feed}</b> <span className="mono" style={{ color: "var(--ink-3)" }}>{list.length} article{list.length === 1 ? "" : "s"} · {list.filter(s => (s.velocity || 0) > 0).length} weighted</span>
          </summary>
          <ul style={{ margin: "4px 0 0 0" }}>
            {[...list].sort((a, b) => (b.velocity || 0) - (a.velocity || 0)).slice(0, 12).map((s, i) => (
              <li key={i} style={{ fontSize: 11, display: "flex", gap: 8, alignItems: "baseline" }}>
                <span className="mono" style={{ minWidth: 22, color: (s.velocity || 0) >= 3 ? "var(--red-ink)" : (s.velocity || 0) >= 2 ? "var(--amber-ink)" : "var(--ink-4)" }}>v{s.velocity || 0}</span>
                <span style={{ flex: 1 }}>{s.label || s.title}</span>
                {s.relevance && <span className="mono" style={{ fontSize: 9.5, color: REL_TONE[s.relevance] || "var(--ink-3)" }}>{REL_LABEL[s.relevance] || s.relevance}</span>}
                {(s.affectedRisks || []).length > 0 && <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>→ {(s.affectedRisks || []).length} categor{(s.affectedRisks || []).length === 1 ? "y" : "ies"}</span>}
              </li>
            ))}
          </ul>
        </details>
      ))}
    </div>
  );
}

export function ScoringMethodology() {
  const R = RE();
  const red = R?.RAG_RED ?? 15, amber = R?.RAG_AMBER ?? 9, base = R?.BASE_LIKELIHOOD ?? 1.0;
  const box = { padding: "6px 10px", background: "var(--surface-2,var(--surface))", borderRadius: 5, border: "1px solid var(--line)" };
  const k = { fontSize: 10, color: "var(--ink-4)" }, v = { fontSize: 10, color: "var(--ink-2)" };
  return (
    <div className="stage-detail">
      <h5>Scoring methodology</h5>
      <div style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 11, color: "var(--ink-2)" }}>
        <div style={box}><span className="mono" style={k}>Score: </span><span className="mono" style={v}>impact × likelihood · likelihood = {base.toFixed(1)} + Σ evidence points (clamped 0.5–5.0) · max 25</span></div>
        <div style={box}><span className="mono" style={k}>Evidence: </span><span className="mono" style={v}>only this entity's own filings, ratios, 8-K routing, governance, risk-factor changes, peers, RSS and FRED. No industry baseline — a category with no evidence is Not assessed and withheld.</span></div>
        <div style={box}><span className="mono" style={k}>Deterministic: </span><span className="mono" style={v}>the same evidence always yields the same score; AI never sets a rating.</span></div>
        <div style={box}><span className="mono" style={k}>Recency (8-K): </span><span className="mono" style={v}>≤90d ×1.0 · ≤180d ×0.75 · ≤1y ×0.5 · ≤2y ×0.2 · older 0 (cap 4.0 per category)</span></div>
        <div style={box}>
          <span className="mono" style={k}>RAG: </span>
          <span className="mono" style={{ fontSize: 10, color: "var(--red-ink)" }}>RED ≥ {red}</span>
          <span className="mono" style={{ fontSize: 10, color: "var(--amber-ink)", marginLeft: 10 }}>AMBER ≥ {amber}</span>
          <span className="mono" style={{ fontSize: 10, color: "var(--green-ink)", marginLeft: 10 }}>GREEN &lt; {amber}</span>
        </div>
      </div>
    </div>
  );
}

function DraftRegister({ intake }) {
  const risks = intake?.risks || [];
  const notAssessed = intake?.notAssessed || [];
  if (!risks.length) {
    return <Waiting>No evidence has reached the register yet — it fills in as each source arrives.</Waiting>;
  }
  return (
    <div className="stage-detail">
      <h5>Risk register · live draft <span className="mono" style={{ fontWeight: 400, color: "var(--ink-3)" }}>({risks.length} assessed · {notAssessed.length} not yet assessed · from {intake.sources?.length || 0} source{intake.sources?.length === 1 ? "" : "s"})</span></h5>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
        <tbody>
          {[...risks].sort((a, b) => b.score - a.score).map(r => (
            <tr key={r.id} style={{ borderTop: "1px solid var(--line)" }}>
              <td style={{ padding: "4px 6px" }}><span className={`rag-dot ${r.rag}`} style={{ marginRight: 5 }} />{r.name}</td>
              <td className="mono" style={{ textAlign: "right", padding: "4px 6px", fontWeight: 600, color: _ink(r.score) }}>{r.score.toFixed(1)}</td>
              <td className="mono" style={{ textAlign: "right", padding: "4px 6px", color: "var(--ink-3)", fontSize: 10 }}>{(r.evidence || []).length} evidence</td>
            </tr>
          ))}
        </tbody>
      </table>
      {notAssessed.length > 0 && (
        <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 6 }}>
          Not yet assessed: {notAssessed.map(n => n.name).join(" · ")}
        </div>
      )}
      <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", marginTop: 4 }}>
        Draft: later sources can still move these scores. The final register is fixed at Stage 2.
      </div>
    </div>
  );
}

function DraftForecast({ intake }) {
  const risks = intake?.risks || [];
  if (!risks.length) return <Waiting>The risk-score forecast appears once the first category is scored.</Waiting>;
  return (
    <div className="stage-detail">
      <h5>Risk score forecast · velocity-dampened</h5>
      <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)", marginBottom: 6 }}>Q_n = score + velocity × CE_mult × 0.85^(n−1) · capped at 25</div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
        <thead>
          <tr style={{ textAlign: "right", color: "var(--ink-4)", fontSize: 9.5 }}>
            <th style={{ textAlign: "left", padding: "3px 6px" }}>RISK</th><th>NOW</th><th>Q+1</th><th>Q+2</th><th>Q+3</th><th>Q+4</th>
          </tr>
        </thead>
        <tbody>
          {[...risks].sort((a, b) => b.score - a.score).slice(0, 8).map(r => {
            const qs = forecastRisk(r, 4);
            return (
              <tr key={r.id} style={{ borderTop: "1px solid var(--line)" }}>
                <td style={{ padding: "4px 6px" }}><span className={`rag-dot ${r.rag}`} style={{ marginRight: 5 }} />{r.name}</td>
                <td className="mono" style={{ textAlign: "right", fontWeight: 600, color: _ink(r.score) }}>{r.score.toFixed(1)}</td>
                {qs.map((q, i) => <td key={i} className="mono" style={{ textAlign: "right", color: _ink(q) }}>{q.toFixed(1)}</td>)}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Tab ids the Signal Intake canvas adds. `available` drives a ● marker so the
// reviewer can see which inputs have landed.
export function intakeTabs(intake, rssCount) {
  return [
    { id: "rss", label: `RSS Feeds${rssCount ? ` (${rssCount})` : ""}` },
    { id: "register", label: `Risk Register${intake?.risks?.length ? ` (${intake.risks.length})` : ""}` },
    { id: "method", label: "Scoring & Methodology" },
    { id: "forecast", label: "Risk Score Forecast" },
  ];
}

export function IntakePanel({ tab, intake, signals, progress }) {
  if (tab === "rss") return <RssFeedDetail signals={signals} progress={progress} />;
  if (tab === "register") return <DraftRegister intake={intake} />;
  if (tab === "method") return <ScoringMethodology />;
  if (tab === "forecast") return <DraftForecast intake={intake} />;
  return null;
}

if (typeof window !== "undefined") Object.assign(window, { LoopProgress, IntakePanel, INTAKE_MILESTONES });

// ── In-app recurring schedule ────────────────────────────────────────────────
// Pure logic lives in loop-schedule.js (tested); this is only the control.
import { CADENCES, WEEKDAYS, nextRun, describe as describeSchedule, normalize as normalizeSchedule } from "./loop-schedule.js";

const _fmtWhen = (ms) => ms ? new Date(ms).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

export function LoopScheduleControl({ schedule, onChange, blockedReason = null, ticker = "" }) {
  const [open, setOpen] = React.useState(false);
  const s = normalizeSchedule(schedule);
  const anchor = Math.max(s.lastRunAt || 0, s.enabledAt || 0, s.skippedAt || 0) || Date.now();
  const next = s.enabled ? nextRun(s, anchor) : null;
  const set = (patch) => {
    const merged = { ...s, ...patch };
    // Switching on starts the clock now — it must not fire for a slot that already passed.
    if (patch.enabled === true && !s.enabled) merged.enabledAt = Date.now();
    if (patch.cadence || patch.time || patch.weekday != null || patch.day != null) merged.enabledAt = Date.now();
    onChange(normalizeSchedule(merged));
  };
  const row = { display: "flex", alignItems: "center", gap: 8, marginBottom: 8, fontSize: 12 };
  const lbl = { width: 92, color: "var(--ink-3)", fontSize: 11 };
  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <button className="btn" onClick={() => setOpen(o => !o)} aria-expanded={open}
        title={s.enabled ? `${describeSchedule(s)} — runs while this app is open` : "Run the loop automatically on a schedule"}>
        <span aria-hidden="true">⏱</span> {s.enabled ? `Scheduled · next ${_fmtWhen(next)}` : "Schedule"}
      </button>
      {open && (
        <div className="stage-detail" role="dialog" aria-label="Loop schedule"
          style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 30, width: 340, padding: 14,
                   background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, boxShadow: "0 6px 24px rgba(0,0,0,.12)" }}>
          <div style={{ fontWeight: 600, fontSize: 12.5, marginBottom: 10 }}>Recurring Run Loop{ticker ? ` · ${ticker}` : ""}</div>
          <label style={row}><input type="checkbox" checked={s.enabled} onChange={e => set({ enabled: e.target.checked })} /> <span>Run automatically</span></label>
          <div style={row}><span style={lbl}>Repeat</span>
            <select value={s.cadence} onChange={e => set({ cadence: e.target.value })} disabled={!s.enabled} style={{ flex: 1 }}>
              {CADENCES.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select></div>
          {s.cadence === "weekly" && (
            <div style={row}><span style={lbl}>On</span>
              <select value={s.weekday} onChange={e => set({ weekday: +e.target.value })} disabled={!s.enabled} style={{ flex: 1 }}>
                {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
              </select></div>
          )}
          {(s.cadence === "monthly" || s.cadence === "quarterly") && (
            <div style={row}><span style={lbl}>Day of month</span>
              <select value={s.day} onChange={e => set({ day: +e.target.value })} disabled={!s.enabled} style={{ flex: 1 }}>
                {Array.from({ length: 28 }, (_, i) => i + 1).map(d => <option key={d} value={d}>{d}</option>)}
              </select></div>
          )}
          <div style={row}><span style={lbl}>At (local)</span>
            <input type="time" value={s.time} onChange={e => e.target.value && set({ time: e.target.value })} disabled={!s.enabled} style={{ flex: 1 }} /></div>
          <label style={row}><input type="checkbox" checked={s.runIfMissed} onChange={e => set({ runIfMissed: e.target.checked })} disabled={!s.enabled} />
            <span>If a run was missed while the app was closed, run once when it opens</span></label>
          <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-2)", lineHeight: 1.6, borderTop: "1px dashed var(--line)", paddingTop: 8 }}>
            {s.enabled ? <>{describeSchedule(s)}<br />Next run: <b>{_fmtWhen(next)}</b></> : "Off"}<br />
            Last scheduled run: {_fmtWhen(s.lastRunAt)}
            {blockedReason && <div style={{ color: "var(--amber-ink)", marginTop: 4 }}>Postponed: {blockedReason}</div>}
          </div>
          <div style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 8, lineHeight: 1.5 }}>
            Runs inside this app, so it fires while the app is open in a browser tab. A scheduled run still stops at the review gates — nothing is auto-approved — and waits if a gate is already awaiting review.
          </div>
        </div>
      )}
    </span>
  );
}
if (typeof window !== "undefined") Object.assign(window, { LoopScheduleControl });
