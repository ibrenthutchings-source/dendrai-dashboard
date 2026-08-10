/* ============================================================
   Risk Quantification — FAIR-style Monte Carlo loss modeling.

   Turns an adjudicated control's fire history and a SOX process's/risk's
   already-derived dollar exposure into a real annual-loss DISTRIBUTION
   (ALE + percentiles + a loss-exceedance curve) instead of the ordinal
   P1/P2/P3 severity or a single hardcoded exposure label the rest of the
   platform carries natively. See fair_endpoints.py / fair_tool.py.

   Three panels:
     - ALE Summary — every resource quantified so far, highest ALE first,
       with a loss-exceedance curve + inputs for whichever row is selected.
     - Run Quantification — point this at a CEM event, a SOX process, a
       risk, or a control and get back ALE/percentiles/curve immediately.
     - Control ROI — two ALE figures (control absent vs. holding) + its
       annual cost -> risk-adjusted ROI, for sizing a proposed MAP.

   Data: window.MCP.fair* (mcp-data.js) -> /fair/* (fair_endpoints.py).
   ============================================================ */

const _FAIR_RESOURCE_TYPES = [
  {
    id: "cem_event", label: "CEM Event",
    what: "A specific incident already logged by the Control Event Monitor (CEM) — e.g. a control that actually "
      + "fired, or a threshold that was actually breached, during a pipeline run. Pick this to price one real, "
      + "already-happened incident.",
    pickHint: "Pick the incident to price.",
  },
  {
    id: "cem_event_template", label: "CEM Event Template",
    what: "The reusable definition behind a category of CEM event — the control/area/risk/severity combination CEM "
      + "incidents of this kind are generated from, before any specific one has fired. Pick this to price a risk "
      + "category in general (\"what would this cost us on average\"), not one specific occurrence.",
    pickHint: "Pick the template to price.",
  },
  {
    id: "sox_process", label: "SOX Process",
    what: "One of the standard financial process lifecycles Process Mining tracks end-to-end on the Continuous "
      + "Watch screen (Procure to Pay, Order to Cash, Receive to Ship). Pricing a process estimates exposure from "
      + "its account-balance materiality, independent of any one incident.",
    pickHint: "Pick the process to price.",
  },
  {
    id: "risk", label: "Risk Register Entry",
    what: "A risk already tracked on the Risk & Control Ledger screen, identified by its risk_ref. Pricing a risk "
      + "uses the dollar exposure already allocated to it there, if any.",
    pickHint: "Type or paste the risk_ref shown on the Risk & Control Ledger screen (e.g. R-strategic-01).",
  },
  {
    id: "control", label: "Control",
    what: "A specific control from the master control catalog, identified by its control_id. Pricing a control "
      + "directly is how you build the \"before\" and \"after\" ALE pair the Control ROI calculator below compares.",
    pickHint: "Pick the control to price.",
  },
];
const _FAIR_RESOURCE_META = Object.fromEntries(_FAIR_RESOURCE_TYPES.map(r => [r.id, r]));

const _FAIR_SEVERITY_TONE = { P1: "bad", P2: "warn", P3: "neutral" };

function _fmtM(v, digits = 2) { return v == null ? "—" : `$${Number(v).toFixed(digits)}M`; }
function _fmtPct1(v) { return v == null ? "—" : `${Number(v).toFixed(1)}%`; }
function _fmtDate(iso) { return iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "—"; }

// ── Loss Exceedance Curve — single-series line, thin stroke, rounded data
// end, hover crosshair + tooltip. One series, no legend needed (title names
// it). Y = loss ($M), X = probability of exceedance (1.0 -> ~0.01). ──
function ExceedanceCurve({ curve, height = 220 }) {
  const hostRef = React.useRef(null);
  const [box, setBox] = React.useState({ w: 480, h: height });
  const [hover, setHover] = React.useState(null);

  React.useEffect(() => {
    if (!hostRef.current) return;
    const ro = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect?.width;
      if (w) setBox(b => ({ ...b, w }));
    });
    ro.observe(hostRef.current);
    return () => ro.disconnect();
  }, []);

  if (!curve || !curve.length) {
    return <Empty>Run a quantification to see its loss-exceedance curve.</Empty>;
  }

  const PAD = { l: 52, r: 16, t: 14, b: 28 };
  const w = box.w, h = box.h;
  const plotW = Math.max(10, w - PAD.l - PAD.r);
  const plotH = Math.max(10, h - PAD.t - PAD.b);
  const maxLoss = Math.max(...curve.map(p => p.loss), 0.0001);

  const x = p => PAD.l + (1 - p.probability) * plotW;   // rare (low p) -> right
  const y = p => PAD.t + (1 - p.loss / maxLoss) * plotH;

  const pathD = curve.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p).toFixed(1)} ${y(p).toFixed(1)}`).join(" ");

  function onMove(e) {
    const rect = hostRef.current.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    let nearest = curve[0], best = Infinity;
    curve.forEach(p => { const d = Math.abs(x(p) - mx); if (d < best) { best = d; nearest = p; } });
    setHover({ point: nearest, mx: e.clientX, my: e.clientY });
  }

  const yTicks = [0, 0.25, 0.5, 0.75, 1].map(f => f * maxLoss);

  return (
    <div ref={hostRef} style={{ position: "relative", width: "100%" }}
      onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block", overflow: "visible" }}>
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={w - PAD.r} y1={y({ loss: t, probability: 0 })} y2={y({ loss: t, probability: 0 })}
              style={{ stroke: "var(--line)" }} strokeWidth={1} strokeDasharray={i === 0 ? "" : "2 3"} />
            <text x={PAD.l - 8} y={y({ loss: t, probability: 0 }) + 3} textAnchor="end"
              style={{ fill: "var(--ink-4)", fontSize: 9.5, fontFamily: "var(--mono, ui-monospace)" }}>
              {_fmtM(t, t < 1 ? 2 : 1)}
            </text>
          </g>
        ))}
        {[0, 0.5, 1].map((p, i) => (
          <text key={i} x={PAD.l + p * plotW} y={h - 8} textAnchor="middle"
            style={{ fill: "var(--ink-4)", fontSize: 9.5, fontFamily: "var(--mono, ui-monospace)" }}>
            {i === 0 ? "certain" : i === 1 ? "p=0.50" : "rare"}
          </text>
        ))}
        <path d={pathD} fill="none" style={{ stroke: "var(--acc)" }} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
        {curve.length > 0 && (
          <circle cx={x(curve[curve.length - 1])} cy={y(curve[curve.length - 1])} r={4}
            style={{ fill: "var(--acc)" }} />
        )}
        {hover && (
          <line x1={x(hover.point)} x2={x(hover.point)} y1={PAD.t} y2={h - PAD.b}
            style={{ stroke: "var(--line-strong)" }} strokeWidth={1} strokeDasharray="2 3" />
        )}
      </svg>
      {hover && (
        <div style={{
          position: "fixed", left: hover.mx + 14, top: hover.my - 12, pointerEvents: "none", zIndex: 20,
          background: "var(--surface)", border: "1px solid var(--line-strong)", borderRadius: 6,
          padding: "6px 10px", fontSize: 11, boxShadow: "0 6px 24px oklch(0% 0 0 / .35)",
        }}>
          <div style={{ color: "var(--ink)", fontWeight: 700 }}>{_fmtM(hover.point.loss)}</div>
          <div style={{ color: "var(--ink-3)", fontSize: 10 }}>
            {_fmtPct1(hover.point.probability * 100)} chance of being exceeded in a given year
          </div>
        </div>
      )}
    </div>
  );
}

function AleSummaryTable({ resources, selected, onSelect }) {
  if (!resources.length) {
    return <Empty>No FAIR quantifications yet — run one below to start pricing an event, process, risk, or control.</Empty>;
  }
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
      <div style={{
        display: "grid", gridTemplateColumns: "1fr 1fr 0.9fr 0.9fr 0.9fr 1fr", gap: 10,
        padding: "7px 12px", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em",
        color: "var(--ink-4)", background: "var(--surface-2)", borderBottom: "1px solid var(--line)",
      }}>
        <div>Resource</div><div>Type</div><div>ALE</div><div>P90</div><div>Source</div><div>Quantified</div>
      </div>
      {resources.map(r => {
        const key = `${r.resource_type}:${r.resource_ref}`;
        const active = key === selected;
        return (
          <div key={r.id} onClick={() => onSelect(r)}
            style={{
              display: "grid", gridTemplateColumns: "1fr 1fr 0.9fr 0.9fr 0.9fr 1fr", gap: 10,
              padding: "8px 12px", fontSize: 11.5, cursor: "pointer",
              borderBottom: "1px solid var(--line)", background: active ? "var(--hover)" : "transparent",
            }}
            onMouseEnter={e => { if (!active) e.currentTarget.style.background = "var(--hover)"; }}
            onMouseLeave={e => { if (!active) e.currentTarget.style.background = "transparent"; }}>
            <div style={{ fontWeight: 600 }} className="mono">{r.resource_ref}</div>
            <div style={{ color: "var(--ink-3)", fontSize: 10.5 }}>{r.resource_type}</div>
            <div className="mono" style={{ fontWeight: 700, color: "var(--red-ink)" }}>{_fmtM(r.ale)}</div>
            <div className="mono" style={{ color: "var(--ink-2)" }}>{_fmtM(r.p90)}</div>
            <div style={{ fontSize: 10.5, color: "var(--ink-4)" }}>{r.tef_source} / {r.magnitude_source}</div>
            <div style={{ fontSize: 10.5, color: "var(--ink-4)" }}>{_fmtDate(r.created_at)}</div>
          </div>
        );
      })}
    </div>
  );
}

// The "Resource reference" field's job changes completely with resource
// type — for four of the five types there's a real, finite list of things
// to price, so a dropdown of actual data beats asking the user to already
// know a raw DB id/ref. Only "risk" has no clean full listing (risk_ref
// values live per-ticker on the Risk & Control Ledger screen, not in one
// global table this screen can safely reach without that screen's own
// permission gate) — that one stays a labeled free-text field.
function ResourceRefField({ resourceType, value, onChange, lookups, lookupsLoading, inputStyle }) {
  if (resourceType === "risk") {
    return <input value={value} onChange={e => onChange(e.target.value)} style={inputStyle}
      placeholder="e.g. R-strategic-01" required />;
  }
  if (lookupsLoading) {
    return <select disabled style={inputStyle}><option>Loading…</option></select>;
  }
  if (resourceType === "cem_event") {
    const events = lookups?.cem_events || [];
    return (
      <select value={value} onChange={e => onChange(e.target.value)} style={inputStyle} required>
        <option value="">{events.length ? "— select a logged incident —" : "No CEM events logged yet"}</option>
        {events.map(ev => (
          <option key={ev.id} value={ev.id}>
            #{ev.id} · {ev.risk_label || ev.control || "Untitled"} · {ev.severity || "—"}{ev.ticker ? ` · ${ev.ticker}` : ""}
          </option>
        ))}
      </select>
    );
  }
  if (resourceType === "cem_event_template") {
    const templates = (lookups?.cem_event_templates || []).filter(t => t.id != null);
    return (
      <select value={value} onChange={e => onChange(e.target.value)} style={inputStyle} required>
        <option value="">{templates.length ? "— select a template —" : "No templates saved yet"}</option>
        {templates.map(t => <option key={t.id} value={t.id}>{t.control} — {t.risk}</option>)}
      </select>
    );
  }
  if (resourceType === "sox_process") {
    const processes = lookups?.sox_processes || [];
    return (
      <select value={value} onChange={e => onChange(e.target.value)} style={inputStyle} required>
        <option value="">— select a process —</option>
        {processes.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
      </select>
    );
  }
  // control
  const controls = lookups?.controls || [];
  return (
    <select value={value} onChange={e => onChange(e.target.value)} style={inputStyle} required>
      <option value="">{controls.length ? "— select a control —" : "No controls in the catalog yet"}</option>
      {controls.map(c => <option key={c.control_id} value={c.control_id}>{c.control_id} — {c.name}</option>)}
    </select>
  );
}

function QuantifyForm({ onResult, lookups, lookupsLoading }) {
  const [resourceType, setResourceType] = React.useState("cem_event");
  const [resourceRef, setResourceRef] = React.useState("");
  const [controlId, setControlId] = React.useState("");
  const [controlIdAuto, setControlIdAuto] = React.useState(true);
  const [windowDays, setWindowDays] = React.useState(90);
  const [ticker, setTicker] = React.useState("");
  const [severity, setSeverity] = React.useState("P2");
  const [soxExposure, setSoxExposure] = React.useState("");
  const [riskExposure, setRiskExposure] = React.useState("");
  const [manualMin, setManualMin] = React.useState("");
  const [manualLikely, setManualLikely] = React.useState("");
  const [manualMax, setManualMax] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState(null);

  const resourceMeta = _FAIR_RESOURCE_META[resourceType];

  // Switching resource type invalidates whatever was picked under the old
  // one (a cem_event id means nothing as a sox_process id) — clear it so
  // the dropdown/input starts empty rather than showing a stale, wrong-shaped value.
  function changeResourceType(next) {
    setResourceType(next);
    setResourceRef("");
  }

  // Pricing a control directly almost always means "use this control's own
  // fire history" too — auto-mirror the picked control into Control ID so
  // the common case needs no second click, but stop following once the
  // user has touched Control ID themselves (controlIdAuto flips off on any
  // manual edit, including picking "— none —").
  React.useEffect(() => {
    if (resourceType === "control" && controlIdAuto) setControlId(resourceRef);
  }, [resourceType, resourceRef, controlIdAuto]);

  async function submit(e) {
    e.preventDefault();
    if (!resourceRef.trim()) { setError("Resource reference is required."); return; }
    setBusy(true); setError(null);
    try {
      const num = v => (v === "" || v == null ? null : Number(v));
      const req = {
        resource_type: resourceType,
        resource_ref: resourceRef.trim(),
        control_id: controlId.trim() || null,
        window_days: Number(windowDays) || 90,
        ticker: ticker.trim() || null,
        cem_severity: severity,
        sox_estimated_exposure: num(soxExposure),
        risk_dollar_exposure_m: num(riskExposure),
        manual_loss_min: num(manualMin),
        manual_loss_likely: num(manualLikely),
        manual_loss_max: num(manualMax),
      };
      const result = await window.MCP.fairQuantify(req);
      onResult(result);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  const inputStyle = {
    width: "100%", fontSize: 12, padding: "6px 9px", borderRadius: 5,
    border: "1px solid var(--line)", background: "var(--surface)", color: "var(--ink)",
  };
  const labelStyle = { fontSize: 10.5, color: "var(--ink-3)", marginBottom: 3, fontWeight: 600 };

  const controls = lookups?.controls || [];

  return (
    <form onSubmit={submit}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <div style={labelStyle}>1. What are you pricing?</div>
          <select value={resourceType} onChange={e => changeResourceType(e.target.value)} style={inputStyle}>
            {_FAIR_RESOURCE_TYPES.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
        </div>
        <div>
          <div style={labelStyle}>2. Which one — {resourceMeta.pickHint}</div>
          <ResourceRefField resourceType={resourceType} value={resourceRef} onChange={setResourceRef}
            lookups={lookups} lookupsLoading={lookupsLoading} inputStyle={inputStyle} />
        </div>
      </div>
      <div style={{ fontSize: 11, color: "var(--ink-3)", lineHeight: 1.5, margin: "6px 0 14px" }}>{resourceMeta.what}</div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
        <div>
          <div style={labelStyle}>Control ID — whose real fire history sets the frequency (optional)</div>
          <select value={controlId} onChange={e => { setControlId(e.target.value); setControlIdAuto(false); }} style={inputStyle}>
            <option value="">{controls.length ? "— none: use a default frequency instead —" : "No controls in the catalog yet"}</option>
            {controls.map(c => <option key={c.control_id} value={c.control_id}>{c.control_id} — {c.name}</option>)}
          </select>
        </div>
        <div>
          <div style={labelStyle}>Lookback window (days) — how far back to count that control's firings</div>
          <input type="number" min={1} value={windowDays} onChange={e => setWindowDays(e.target.value)} style={inputStyle} />
        </div>
      </div>

      <div className="kicker" style={{ marginBottom: 4 }}>Magnitude — how much a single occurrence would cost</div>
      <div style={{ fontSize: 10.5, color: "var(--ink-4)", marginBottom: 8 }}>
        Leave these blank to let the backend resolve a real figure on its own (a SOX process's account-balance
        exposure, or a risk's already-allocated dollar exposure). Fill one in only to override that with your own
        estimate — the first one supplied here wins, left to right, top to bottom.
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 12 }}>
        <div>
          <div style={labelStyle}>Ticker — whose SOX exposure to look up (for a SOX Process)</div>
          <input value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())} style={inputStyle} placeholder="e.g. AAPL" />
        </div>
        <div>
          <div style={labelStyle}>SOX estimated exposure ($M) — for a SOX Process</div>
          <input type="number" step="0.01" value={soxExposure} onChange={e => setSoxExposure(e.target.value)} style={inputStyle} placeholder="auto-resolved from ticker" />
        </div>
        <div>
          <div style={labelStyle}>Risk dollar exposure ($M) — for a Risk Register Entry</div>
          <input type="number" step="0.01" value={riskExposure} onChange={e => setRiskExposure(e.target.value)} style={inputStyle} placeholder="auto-resolved if left blank" />
        </div>
        <div>
          <div style={labelStyle}>CEM severity — last-resort default band</div>
          <select value={severity} onChange={e => setSeverity(e.target.value)} style={inputStyle}>
            <option value="P1">P1 — highest severity</option>
            <option value="P2">P2 — moderate severity</option>
            <option value="P3">P3 — lowest severity</option>
          </select>
        </div>
      </div>
      <div style={{ fontSize: 10.5, color: "var(--ink-4)", marginBottom: 8 }}>
        Or override the whole magnitude distribution by hand (all three required together to take effect):
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 14 }}>
        <div>
          <div style={labelStyle}>Manual min ($M)</div>
          <input type="number" step="0.01" value={manualMin} onChange={e => setManualMin(e.target.value)} style={inputStyle} />
        </div>
        <div>
          <div style={labelStyle}>Manual most-likely ($M)</div>
          <input type="number" step="0.01" value={manualLikely} onChange={e => setManualLikely(e.target.value)} style={inputStyle} />
        </div>
        <div>
          <div style={labelStyle}>Manual max ($M)</div>
          <input type="number" step="0.01" value={manualMax} onChange={e => setManualMax(e.target.value)} style={inputStyle} />
        </div>
      </div>

      {error && <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 10 }}>{error}</div>}
      <button type="submit" disabled={busy} className="btn btn-acc" style={{ fontSize: 12, padding: "8px 18px" }}>
        {busy ? "Running Monte Carlo…" : "Run Quantification"}
      </button>
    </form>
  );
}

function ControlRoiCalculator() {
  const [aleBefore, setAleBefore] = React.useState("");
  const [aleAfter, setAleAfter] = React.useState("");
  const [cost, setCost] = React.useState("");
  const [result, setResult] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await window.MCP.fairControlRoi(Number(aleBefore) || 0, Number(aleAfter) || 0, Number(cost) || 0);
      setResult(r);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  const inputStyle = {
    width: "100%", fontSize: 12, padding: "6px 9px", borderRadius: 5,
    border: "1px solid var(--line)", background: "var(--surface)", color: "var(--ink)",
  };

  return (
    <form onSubmit={submit}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 10.5, color: "var(--ink-3)", marginBottom: 3, fontWeight: 600 }}>ALE — control absent/failing ($M)</div>
          <input type="number" step="0.01" value={aleBefore} onChange={e => setAleBefore(e.target.value)} style={inputStyle} required />
        </div>
        <div>
          <div style={{ fontSize: 10.5, color: "var(--ink-3)", marginBottom: 3, fontWeight: 600 }}>ALE — control holding ($M)</div>
          <input type="number" step="0.01" value={aleAfter} onChange={e => setAleAfter(e.target.value)} style={inputStyle} required />
        </div>
        <div>
          <div style={{ fontSize: 10.5, color: "var(--ink-3)", marginBottom: 3, fontWeight: 600 }}>Annual control cost ($M)</div>
          <input type="number" step="0.01" value={cost} onChange={e => setCost(e.target.value)} style={inputStyle} required />
        </div>
      </div>
      {error && <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 10 }}>{error}</div>}
      <button type="submit" disabled={busy} className="btn" style={{ fontSize: 12, padding: "7px 16px" }}>
        {busy ? "Computing…" : "Compute ROI"}
      </button>
      {result && (
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginTop: 14, padding: "12px 14px", border: "1px solid var(--line)", borderRadius: 6, background: "var(--surface-2)" }}>
          <div>
            <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase" }}>Risk reduction</div>
            <div className="mono" style={{ fontSize: 16, fontWeight: 700 }}>{_fmtM(result.risk_reduction)}</div>
            <div style={{ fontSize: 10.5, color: "var(--ink-3)" }}>{_fmtPct1(result.ale_reduction_pct)} of ALE removed</div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase" }}>Net benefit</div>
            <div className="mono" style={{ fontSize: 16, fontWeight: 700, color: result.worth_it ? "var(--green-ink)" : "var(--red-ink)" }}>
              {_fmtM(result.net_benefit)}
            </div>
            <div style={{ fontSize: 10.5, color: "var(--ink-3)" }}>{result.worth_it ? "Worth the cost" : "Costs more than it saves"}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase" }}>ROI</div>
            <div className="mono" style={{ fontSize: 16, fontWeight: 700 }}>{result.roi_pct == null ? "—" : _fmtPct1(result.roi_pct)}</div>
          </div>
        </div>
      )}
    </form>
  );
}

function RiskQuantificationScreen({ onNavigate } = {}) {
  const [resources, setResources] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [selected, setSelected] = React.useState(null);
  const [totalAle, setTotalAle] = React.useState(0);
  const [lookups, setLookups] = React.useState(null);
  const [lookupsLoading, setLookupsLoading] = React.useState(true);

  // Real, pickable values (CEM events/templates, SOX processes, controls)
  // for the form's dropdowns below — fetched once, not per resource-type
  // switch, since switching types is just picking a different array out of
  // this same response.
  React.useEffect(() => {
    window.MCP.fairLookups()
      .then(setLookups)
      .catch(() => setLookups({ cem_events: [], cem_event_templates: [], sox_processes: [], controls: [] }))
      .finally(() => setLookupsLoading(false));
  }, []);

  const load = React.useCallback(() => {
    setLoading(true);
    return window.MCP.fairAleSummary(365)
      .then(d => {
        setResources(d.resources || []);
        setTotalAle(d.total_ale || 0);
        if (!selected && (d.resources || []).length) setSelected(d.resources[0]);
        setError(null);
      })
      .catch(e => setError(e.message || String(e)))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useEffect(() => { load(); }, [load]);

  function handleNewResult(result) {
    load();
    setSelected({ ...result, id: result.id, created_at: new Date().toISOString() });
  }

  const selectedKey = selected ? `${selected.resource_type}:${selected.resource_ref}` : null;
  const highestAle = resources[0];

  return (
    <div className="scope-screen" data-screen-label="Risk Quantification">
      <div className="panel-head">
        <div className="kicker">Audit & Compliance · FAIR Loss Modeling</div>
        <div className="panel-title mt-8">Risk Quantification</div>
        <div className="panel-sub">
          Threat Event Frequency (real control fire history) x Loss Magnitude (SOX exposure, allocated risk
          dollar exposure, or a labeled CEM-severity default) via Monte Carlo — a dollar distribution instead
          of an ordinal severity or a static exposure label. Every figure below shows exactly which real
          source produced it.
        </div>
      </div>

      <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "14px 16px", marginBottom: 22, background: "var(--surface-2)" }}>
        <div className="kicker" style={{ marginBottom: 8 }}>New here? How this screen works</div>
        <ol style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.75 }}>
          <li>
            <strong>Pick what you're pricing</strong> in "Run a New Quantification" below. You're choosing between
            five kinds of thing: a <strong>CEM Event</strong> (one specific incident already logged by the Control
            Event Monitor), a <strong>CEM Event Template</strong> (the category that kind of incident belongs to,
            priced in general rather than one occurrence), a <strong>SOX Process</strong> (a financial process
            lifecycle from Continuous Watch), a <strong>Risk Register Entry</strong> (from the Risk & Control
            Ledger), or a <strong>Control</strong> (from the control catalog). Once you pick a type, the field next
            to it becomes a dropdown of the real, actual ones on file — and a description explains exactly what
            you're about to price.
          </li>
          <li>
            <strong>Run it.</strong> The backend fills in frequency and magnitude from real data wherever it can
            (a control's actual fire history; a SOX process's account-balance exposure; a risk's already-allocated
            dollar exposure) — you only need to type a number yourself for whatever it can't resolve on its own.
          </li>
          <li>
            <strong>Read the result</strong> above, in the ALE Summary table and Loss Exceedance curve.
            <strong> ALE (Annualized Loss Expectancy)</strong> is the dollar amount you should expect to lose per
            year from this resource, on average — <strong>P90/P95</strong> are the loss levels a bad year has a
            10%/5% chance of exceeding.
          </li>
        </ol>
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 22 }}>
        <div style={{ flex: "1 1 200px", minWidth: 200, border: "1px solid var(--line)", borderRadius: 8, padding: "12px 14px", background: "var(--surface)" }}>
          <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>Total ALE at risk (365d)</div>
          <div className="mono" style={{ fontSize: 22, fontWeight: 700, color: "var(--red-ink)", marginTop: 4 }}>{_fmtM(totalAle)}</div>
        </div>
        <div style={{ flex: "1 1 200px", minWidth: 200, border: "1px solid var(--line)", borderRadius: 8, padding: "12px 14px", background: "var(--surface)" }}>
          <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>Resources quantified</div>
          <div className="mono" style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{resources.length}</div>
        </div>
        <div style={{ flex: "1 1 200px", minWidth: 200, border: "1px solid var(--line)", borderRadius: 8, padding: "12px 14px", background: "var(--surface)" }}>
          <div style={{ fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>Most expensive open risk</div>
          <div className="mono" style={{ fontSize: 15, fontWeight: 700, marginTop: 4 }}>
            {highestAle ? `${highestAle.resource_ref} — ${_fmtM(highestAle.ale)}` : "—"}
          </div>
        </div>
      </div>

      {error && <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 12 }}>{error}</div>}

      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", marginBottom: 28 }}>
        <div style={{ flex: 2, minWidth: 420 }}>
          <div className="kicker" style={{ marginBottom: 8 }}>ALE Summary — highest annualized loss first</div>
          {loading && !resources.length ? <Empty>Loading…</Empty> : (
            <AleSummaryTable resources={resources} selected={selectedKey} onSelect={setSelected} />
          )}
        </div>
        <div style={{ flex: 1, minWidth: 320 }}>
          <div className="kicker" style={{ marginBottom: 8 }}>
            {selected ? `Loss Exceedance — ${selected.resource_ref}` : "Loss Exceedance Curve"}
          </div>
          {selected ? (
            <>
              <ExceedanceCurve curve={selected.exceedance_curve} />
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 10, fontSize: 11 }}>
                <div><span style={{ color: "var(--ink-4)" }}>ALE</span> <span className="mono" style={{ fontWeight: 700 }}>{_fmtM(selected.ale)}</span></div>
                <div><span style={{ color: "var(--ink-4)" }}>P50</span> <span className="mono">{_fmtM(selected.p50)}</span></div>
                <div><span style={{ color: "var(--ink-4)" }}>P90</span> <span className="mono">{_fmtM(selected.p90)}</span></div>
                <div><span style={{ color: "var(--ink-4)" }}>P95</span> <span className="mono">{_fmtM(selected.p95)}</span></div>
              </div>
              <div style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 8 }}>
                TEF {selected.tef_mean != null ? selected.tef_mean.toFixed(2) : "—"} events/yr ({selected.tef_source}) ·
                {" "}magnitude {_fmtM(selected.loss_min)}–{_fmtM(selected.loss_max)} ({selected.magnitude_source}) ·
                {" "}{selected.simulations?.toLocaleString()} simulations
              </div>
            </>
          ) : <Empty>Select a row, or run a new quantification below.</Empty>}
        </div>
      </div>

      <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "16px 18px", marginBottom: 24, background: "var(--surface)" }}>
        <div className="kicker" style={{ marginBottom: 12 }}>Run a New Quantification</div>
        <QuantifyForm onResult={handleNewResult} lookups={lookups} lookupsLoading={lookupsLoading} />
      </div>

      <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "16px 18px" }}>
        <div className="kicker" style={{ marginBottom: 12 }}>Control ROI Calculator</div>
        <div className="panel-sub" style={{ marginBottom: 12 }}>
          Run two quantifications above for the same control — once as it stands today ("holding"), and once with
          worse manual min/likely/max magnitude figures standing in for it absent or failing — then read each
          run's ALE off the ALE Summary table above and enter both figures below, next to the control's annual
          cost, to see whether it's worth what it costs.
        </div>
        <ControlRoiCalculator />
      </div>
    </div>
  );
}

Object.assign(window, { RiskQuantificationScreen });
