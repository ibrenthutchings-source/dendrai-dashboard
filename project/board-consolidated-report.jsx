/* ============================================================
   Board Intelligence — Consolidated Report.
   A single board-ready packet combining three artifacts that already
   exist elsewhere in the app, rather than computing anything new:
     1. Assess Risk Evidence Pack   (evidence-pack.jsx's EvidencePackModal)
     2. Audit Plan (Gantt)          (audit-scope.jsx's ScopeGantt)
     3. Exception Management        (exceptions-report.jsx's
                                      ExceptionPersonaBriefs, locked to the
                                      BOARD persona — "filtered for Board
                                      Level" means: only the Board-audience
                                      brief, not a risk-rating filter, per
                                      the scope decision behind this file)
   Scoped to the currently-loaded ticker/run, same as every other Board
   Intelligence screen.
   ============================================================ */

import { lazyGlobal } from './src/lazy-screen.js';

// app.jsx's lazyGlobal consts are local to app.jsx's own module scope and
// aren't reachable from here — this file is its own chunk, so it makes its
// own lazy references to the two chunks it borrows components from.
const ScopeGanttLazy = lazyGlobal(() => import('./audit-scope.jsx'), 'ScopeGantt');
const ExceptionPersonaBriefsLazy = lazyGlobal(() => import('./exceptions-report.jsx'), 'ExceptionPersonaBriefs');

function _todayISO() { return new Date().toISOString().slice(0, 10); }
function _daysAgoISO(n) { const d = new Date(); d.setDate(d.getDate() - n); return d.toISOString().slice(0, 10); }

// ---- Section 1: Evidence Pack launcher — the modal itself is a single
// app-wide instance owned by app.jsx (same one Assess Risk opens), so this
// is just a card + button that calls back up to it rather than a second
// modal instance.
function EvidencePackCard({ ticker, runId, hasRun, onOpen }) {
  return (
    <div className="stage-detail">
      <SectionLabel>Assess Risk Evidence Pack</SectionLabel>
      <div className="persona-card" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div className="persona-headline" style={{ fontSize: 13 }}>
            {hasRun ? `Full evidence trail for ${ticker || "this entity"}` : "No completed run yet"}
          </div>
          <div className="persona-summary">
            {hasRun
              ? "Every data source, calculation, and AI-assisted judgment behind the current risk assessment, packaged for audit and board review."
              : "Run Assess Enterprise Risk from the Pipeline screen to generate an evidence pack for this entity."}
          </div>
        </div>
        <button className="btn btn-sm btn-acc" onClick={onOpen} disabled={!hasRun} style={{ flexShrink: 0 }}>
          <Icon name="doc" size={11} /> Open Evidence Pack
        </button>
      </div>
    </div>
  );
}

// ---- Section 2: Audit Plan Gantt — reuses ScopeGantt exactly as
// audit-scope.jsx renders it, just without the Kanban/sprint-filter toolbar
// (a board packet wants the whole plan at a glance, not a working view).
function AuditPlanSection({ objectives, maps }) {
  const objs = objectives || [];
  return (
    <div className="stage-detail" style={{ marginTop: 20 }}>
      <SectionLabel>Audit Plan</SectionLabel>
      {objs.length === 0 ? (
        <Empty>Audit objectives populate after Stage 3 of the loop. Run the loop from the Pipeline screen.</Empty>
      ) : (
        <React.Suspense fallback={<Empty>Loading audit plan…</Empty>}>
          <ScopeGanttLazy objectives={objs} maps={maps} sprintFilter="all" />
        </React.Suspense>
      )}
    </div>
  );
}

// ---- Section 3: Exception Management, Board Level — fetches this
// period's exception report itself (same trailing-30-days default as the
// Exception Report screen), then hands it to ExceptionPersonaBriefs locked
// to BOARD. Whether a Board brief actually exists yet is exactly what that
// component already shows (a filled persona-card if one's cached, or an
// "click Generate" prompt if not) — no separate "has a brief" check needed.
function ExceptionBoardSection() {
  const [dateFrom] = React.useState(_daysAgoISO(30));
  const [dateTo] = React.useState(_todayISO());
  const [report, setReport] = React.useState(null);
  const [state, setState] = React.useState({ loading: true, error: null });

  React.useEffect(() => {
    let live = true;
    setState({ loading: true, error: null });
    window.MCP.exceptionsReport(dateFrom, dateTo)
      .then(data => { if (live) { setReport(data); setState({ loading: false, error: null }); } })
      .catch(e => { if (live) setState({ loading: false, error: e.message || "Exception report unavailable" }); });
    return () => { live = false; };
  }, [dateFrom, dateTo]);

  return (
    <div className="stage-detail" style={{ marginTop: 20 }}>
      <SectionLabel>Exception Management · Board Level</SectionLabel>
      <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)", marginBottom: 8 }}>
        {dateFrom} → {dateTo}
      </div>
      {state.error ? (
        <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", background: "var(--red-soft)", padding: "6px 10px", borderRadius: 4 }}>{state.error}</div>
      ) : state.loading ? (
        <Empty>Loading this period's exception report…</Empty>
      ) : !report || report.summary.total_occurrences === 0 ? (
        <Empty icon="✓">No exceptions occurred in this period.</Empty>
      ) : (
        <React.Suspense fallback={<Empty>Loading persona brief…</Empty>}>
          <ExceptionPersonaBriefsLazy report={report} dateFrom={dateFrom} dateTo={dateTo} lockPersona="BOARD" />
        </React.Suspense>
      )}
    </div>
  );
}

function BoardConsolidatedReportScreen({ ticker, runId, hasRun, objectives, maps, onOpenEvidencePack }) {
  return (
    <div className="scope-screen" data-screen-label="Consolidated Report">
      <div className="panel-head">
        <div>
          <div className="kicker">Board Intelligence · Consolidated Report</div>
          <div className="panel-title mt-8">Board Report</div>
          <div className="panel-sub">
            One packet for the board and audit committee: the current risk-assessment evidence trail, the audit
            plan, and any exception brief already prepared at Board level — scoped to {ticker || "the current entity"}.
          </div>
        </div>
      </div>

      <EvidencePackCard ticker={ticker} runId={runId} hasRun={hasRun} onOpen={onOpenEvidencePack} />
      <AuditPlanSection objectives={objectives} maps={maps} />
      <ExceptionBoardSection />
    </div>
  );
}

Object.assign(window, { BoardConsolidatedReportScreen });
