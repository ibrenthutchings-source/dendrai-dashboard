/* ============================================================
   Exception Report — board/executive period reporting over Exception
   Management data (exception_control_events). Distinct from the
   Exception Management screen (exceptions.jsx): that screen is the
   operational triage queue (what needs a decision right now); this one
   answers "what happened between two dates, and what did it cost" for an
   audience that isn't triaging anything, drilling from a summary down to
   individual occurrences instead.

   Data: window.MCP.exceptionsReport/exceptionsReportDetail (mcp-data.js) ->
   GET /exceptions/report[/detail] (exceptions_endpoints.py). $ impact per
   control is the literal transaction amount when one was captured (JE
   Testing findings), otherwise a FAIR Monte Carlo estimate — see that
   endpoint's docstring. A mixed group (some priced, some not) reports the
   literal sum only, flagged partial, rather than blending a real dollar
   figure with a modeled one.
   ============================================================ */

const _RISK_ORDER = { R: 0, A: 1, G: 2, unrated: 3 };

function _todayISO() {
  return new Date().toISOString().slice(0, 10);
}
function _daysAgoISO(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function ImpactCell({ row }) {
  const label = fmt$M(row.impact_usd);
  if (row.impact_source === "fair_estimate") {
    return (
      <span title="No transaction amount was captured for these occurrences — estimated via FAIR (Factor Analysis of Information Risk) Monte Carlo simulation, using this period's occurrence count as the threat-event frequency.">
        {label} <span className="mono" style={{ fontSize: 9, color: "var(--ink-4)" }}>est.</span>
      </span>
    );
  }
  if (row.impact_source === "transaction_amount_partial") {
    return (
      <span title="Sum of the occurrences with a captured transaction amount only — some occurrences in this group had no amount and are not included in this figure.">
        {label} <span className="mono" style={{ fontSize: 9, color: "var(--ink-4)" }}>partial</span>
      </span>
    );
  }
  if (row.impact_source === "not_computed") {
    return (
      <span title="This report estimates impact for the highest-occurrence groups first and stops once its time budget is spent — this group's FAIR estimate wasn't reached this run.">
        <span className="mono" style={{ fontSize: 9, color: "var(--ink-4)" }}>not computed</span>
      </span>
    );
  }
  return <span>{label}</span>;
}

function SummaryTile({ label, children }) {
  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "12px 14px" }}>
      <div className="kicker" style={{ marginBottom: 6 }}>{label}</div>
      {children}
    </div>
  );
}

function SummaryCards({ summary }) {
  const riskEntries = Object.entries(summary.by_risk_rating || {})
    .sort((a, b) => (_RISK_ORDER[a[0]] ?? 9) - (_RISK_ORDER[b[0]] ?? 9));
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12, marginBottom: 18 }}>
      <SummaryTile label="Total occurrences">
        <span className="mono" style={{ fontSize: 20, fontWeight: 700 }}>{summary.total_occurrences.toLocaleString()}</span>
      </SummaryTile>
      <SummaryTile label="Estimated impact">
        <span className="mono" style={{ fontSize: 20, fontWeight: 700 }} title={summary.impact_basis}>{fmt$M(summary.total_impact_usd)}</span>
      </SummaryTile>
      <SummaryTile label="Controls affected">
        <span className="mono" style={{ fontSize: 20, fontWeight: 700 }}>{summary.controls_total.toLocaleString()}</span>
      </SummaryTile>
      <SummaryTile label="Risk mix">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {riskEntries.length === 0
            ? <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>—</span>
            : riskEntries.map(([rating, count]) => (
                <span key={rating} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <RiskRatingPill rating={rating === "unrated" ? undefined : rating} />
                  <span className="mono" style={{ fontSize: 11, fontWeight: 700 }}>{count}</span>
                </span>
              ))}
        </div>
      </SummaryTile>
    </div>
  );
}

function ByControlRow({ row, onDrill }) {
  return (
    <Clickable className="table-row" onClick={() => onDrill(row.control_id)}
      style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr 0.7fr 1fr 0.8fr", gap: 10, padding: "9px 12px", alignItems: "center", cursor: "pointer" }}>
      <div className="mono" style={{ fontSize: 11.5, fontWeight: 600 }}>{row.control_id}</div>
      <div style={{ fontSize: 11.5, color: "var(--ink-2)" }}>{row.system_source}</div>
      <div style={{ fontSize: 11.5, color: "var(--ink-2)" }}>{row.process}</div>
      <div className="mono" style={{ fontSize: 11.5, textAlign: "right" }}>{row.occurrence_count}</div>
      <div className="mono" style={{ fontSize: 11.5 }}><ImpactCell row={row} /></div>
      <div><RiskRatingPill rating={row.worst_risk_rating} /></div>
    </Clickable>
  );
}

function DrillDownEvent({ ev }) {
  const amount = ev.point_in_time_features?.amount;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1.1fr 0.9fr 1fr 0.7fr", gap: 10, padding: "7px 12px", borderBottom: "1px solid var(--line)", fontSize: 11 }}>
      <div className="mono" style={{ color: "var(--ink-3)" }}>{ev.event_timestamp ? new Date(ev.event_timestamp).toLocaleString() : "—"}</div>
      <div style={{ color: "var(--ink-2)" }}>{ev.actor || "—"}</div>
      <div style={{ color: "var(--ink-2)" }}>{ev.action || ev.event_type || "—"}</div>
      <div className="mono" style={{ textAlign: "right" }}>{amount != null ? fmt$M(amount) : "—"}</div>
    </div>
  );
}

function DrillDownModal({ controlId, dateFrom, dateTo, onClose }) {
  const [events, setEvents] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);

  React.useEffect(() => {
    if (!controlId) return;
    let cancelled = false;
    setLoading(true); setError(null);
    window.MCP.exceptionsReportDetail(dateFrom, dateTo, controlId)
      .then(data => { if (!cancelled) setEvents(data.events || []); })
      .catch(e => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [controlId, dateFrom, dateTo]);

  return (
    <Modal open={!!controlId} onClose={onClose} title={controlId} titleSub={`${dateFrom} → ${dateTo}`} width={640}>
      {loading ? (
        <Empty>Loading occurrences…</Empty>
      ) : error ? (
        <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)" }}>{error}</div>
      ) : events.length === 0 ? (
        <Empty>No occurrences found for this control in the selected period.</Empty>
      ) : (
        <div>
          <div style={{ display: "grid", gridTemplateColumns: "1.1fr 0.9fr 1fr 0.7fr", gap: 10, padding: "0 12px 6px", fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", textTransform: "uppercase" }}>
            <div>When</div><div>Actor</div><div>Action</div><div style={{ textAlign: "right" }}>Amount</div>
          </div>
          <div style={{ maxHeight: 360, overflowY: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
            {events.map(ev => <DrillDownEvent key={ev.event_id} ev={ev} />)}
          </div>
        </div>
      )}
    </Modal>
  );
}

// Same six personas ai_endpoints.py's persona_brief already uses for a
// risk-loop run (rail.jsx's PersonaTab) — reusing the exact vocabulary
// rather than inventing a parallel one for exceptions.
const _EXCEPTION_PERSONAS = ["CAE", "CFO", "COO", "TECH_EXEC", "NONTECH_EXEC", "BOARD"];

// AI-generated, role-tailored narrative over the CURRENT report (same
// mechanism as the risk loop's persona briefs — see rail.jsx's PersonaTab —
// fed this period's exception data instead of a risk register). Briefs are
// cached server-side per (persona, date_from, date_to), so the Board
// Intelligence consolidated report can request the same BOARD brief and
// get back whatever was already generated here instead of paying for a
// second Claude call.
// lockPersona: pins the brief to one persona and hides the by-function
// picker — used by the Board Intelligence consolidated report, which shows
// only the BOARD brief (never lets a viewer wander into the CFO/COO/etc.
// briefs from that screen). Omitted here on the Exception Report screen
// itself, where all six personas stay selectable.
// autoGenerate: fires the AI call once on mount rather than waiting for a
// "Generate with AI" click — same reasoning as PersonaTab's identical flag
// (rail.jsx): a board packet should show up pre-assembled, not require a
// click-through. Defaults off, so this screen's own manual behavior is
// unchanged.
// compact: charts-first rendering of the brief — headline + callouts stay
// visible, the long prose sections collapse behind a toggle. See
// components.jsx's PersonaBriefBody. Also default off.
function ExceptionPersonaBriefs({ report, dateFrom, dateTo, lockPersona = null, autoGenerate = false, compact = false }) {
  const [selected, setSelected] = React.useState(lockPersona || "BOARD");
  const [briefs, setBriefs] = React.useState({});
  const [state, setState] = React.useState({ loading: false, error: null });
  const brief = briefs[selected];

  async function generate() {
    setState({ loading: true, error: null });
    try {
      const res = await window.MCP.aiExceptionBrief(selected, dateFrom, dateTo, report.summary, report.by_control);
      setBriefs(prev => ({ ...prev, [selected]: res }));
      setState({ loading: false, error: null });
    } catch (e) {
      setState({ loading: false, error: e.message || "AI unavailable" });
    }
  }

  React.useEffect(() => {
    if (autoGenerate && !brief && !state.loading) generate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoGenerate, selected, dateFrom, dateTo]);

  return (
    <div className="stage-detail" style={{ marginTop: 20 }}>
      <SectionLabel right={
        <button className="btn btn-sm" onClick={generate} disabled={state.loading}
          title="Generate a role-tailored brief with Claude">
          <Icon name="spark" size={10} /> {state.loading ? "Generating…" : brief ? "Regenerate" : "Generate with AI"}
        </button>
      }>Persona Brief</SectionLabel>
      {!lockPersona && (
        <div className="persona-pick-group">
          <div className="persona-pick-label">By function</div>
          <div className="persona-pick">
            {_EXCEPTION_PERSONAS.map(p => (
              <button key={p} className={"pp" + (selected === p ? " active" : "")} onClick={() => setSelected(p)}>{p}</button>
            ))}
          </div>
        </div>
      )}

      {state.error && (
        <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", margin: "4px 0" }}>
          AI brief unavailable: {state.error}
        </div>
      )}

      {brief ? (
        <PersonaBriefBody brief={brief} compact={compact} />
      ) : (
        <Empty>
          {state.loading
            ? `Generating the ${selected} brief for this period's exception report…`
            : `Click "Generate with AI" to produce the ${selected} brief for this period's exception report.`}
        </Empty>
      )}
    </div>
  );
}

function ExceptionsReportScreen() {
  const [dateFrom, setDateFrom] = React.useState(_daysAgoISO(30));
  const [dateTo, setDateTo] = React.useState(_todayISO());
  const [report, setReport] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [drillControl, setDrillControl] = React.useState(null);
  const hasLoadedRef = React.useRef(false);

  const load = React.useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const data = await window.MCP.exceptionsReport(dateFrom, dateTo);
      setReport(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
      hasLoadedRef.current = true;
    }
  }, [dateFrom, dateTo]);

  React.useEffect(() => { load(); }, [load]);

  const sortedRows = React.useMemo(() => {
    if (!report) return [];
    // impact_usd is null for "not_computed" rows (the FAIR time budget ran
    // out before reaching them) — sort those last rather than have a
    // null/number comparison scatter them unpredictably through the list.
    return [...report.by_control].sort((a, b) => (b.impact_usd ?? -Infinity) - (a.impact_usd ?? -Infinity));
  }, [report]);

  return (
    <div className="scope-screen" data-screen-label="Exception Report">
      <div className="panel-head">
        <div>
          <div className="kicker">Exception Management · Board Reporting</div>
          <div className="panel-title mt-8">Exception Report</div>
          <div className="panel-sub">
            Every control exception between two dates, grouped by control with an estimated dollar impact and risk
            rating — click a row to see the individual occurrences behind it.
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "flex-end", marginBottom: 18, flexWrap: "wrap" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 10.5, color: "var(--ink-3)" }}>
          From
          <input type="date" className="fi-input" value={dateFrom} max={dateTo}
            onChange={e => setDateFrom(e.target.value)} />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 10.5, color: "var(--ink-3)" }}>
          To
          <input type="date" className="fi-input" value={dateTo} min={dateFrom} max={_todayISO()}
            onChange={e => setDateTo(e.target.value)} />
        </label>
        <button className="btn btn-sm btn-acc" onClick={load} disabled={loading}>
          <Icon name="check" size={11} /> {loading ? "Running…" : "Run report"}
        </button>
      </div>

      {error ? (
        <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", background: "var(--red-soft)", padding: "6px 10px", borderRadius: 4 }}>{error}</div>
      ) : loading && !hasLoadedRef.current ? (
        <Empty>Running report…</Empty>
      ) : !report || report.summary.total_occurrences === 0 ? (
        <Empty icon="✓">No exceptions occurred in this period.</Empty>
      ) : (
        <>
          <SummaryCards summary={report.summary} />
          {report.summary.controls_shown < report.summary.controls_total && (
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginBottom: 10 }}>
              Showing the top {report.summary.controls_shown.toLocaleString()} of {report.summary.controls_total.toLocaleString()} control
              groups by occurrence count — the totals above cover every exception in the period; this table doesn't.
            </div>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr 0.7fr 1fr 0.8fr", gap: 10, padding: "0 12px 8px", fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", textTransform: "uppercase" }}>
            <div>Control</div><div>System</div><div>Process</div><div style={{ textAlign: "right" }}># Occ.</div><div>Impact</div><div>Risk</div>
          </div>
          <div style={{ border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
            {sortedRows.map(row => (
              <ByControlRow key={`${row.control_id}-${row.system_source}-${row.process}`} row={row} onDrill={setDrillControl} />
            ))}
          </div>
          <ExceptionPersonaBriefs report={report} dateFrom={dateFrom} dateTo={dateTo} />
        </>
      )}

      <DrillDownModal controlId={drillControl} dateFrom={dateFrom} dateTo={dateTo} onClose={() => setDrillControl(null)} />
    </div>
  );
}

Object.assign(window, { ExceptionsReportScreen, ExceptionPersonaBriefs });
