/* ============================================================
   Board Intelligence — Consolidated Report.
   A single board-ready packet combining artifacts that already exist
   elsewhere in the app, rather than computing anything new:
     1. Assess Risk Evidence Pack   (evidence-pack.jsx's EvidencePackModal)
     2. Audit Plan (Gantt)          (audit-scope.jsx's ScopeGantt)
     3. Board Risk Profile          (charts.jsx's SeverityMatrix + a ranked
                                      top-risks chart, over the same `risks`
                                      register the Live Register rail reads,
                                      plus rail.jsx's PersonaTab locked to the
                                      BOARD audience layer)
     4. Coverage Gap Analysis       (coverage-gap.jsx's CoverageGapPanel —
                                      the register→scope / quant / 8-K /
                                      RAG-calibration report, reused as-is)
     5. Exception Management        (exceptions-report.jsx's
                                      ExceptionPersonaBriefs, locked to the
                                      BOARD persona — "filtered for Board
                                      Level" means: only the Board-audience
                                      brief, not a risk-rating filter, per
                                      the scope decision behind this file)

   CHARTS FIRST. A board member should get the whole picture from the marks
   and the numbers in about thirty seconds; the AI briefs (3 and 5) are real
   value — human-reviewed and server-cached — but they are a supporting
   layer here, not the page. Each brief keeps its headline and callouts on
   screen and collapses its long prose sections behind a "Read the full
   narrative" toggle (components.jsx's PersonaBriefBody, `compact` mode).
   Both still auto-generate on load rather than waiting for a "Generate with
   AI" click — a report meant to be handed to the board should show up
   pre-assembled. Scoped to the currently-loaded ticker/run, same as every
   other Board Intelligence screen.

   Data discipline: every chart on this screen is drawn from something the
   screen already had — the `risks` register, `objectives`/`maps`, and the
   one exception-report fetch this screen makes (passed down to
   ExceptionBoardSection as props). No new endpoints, no second network call.

   "Export to PowerPoint" (top-right of the header) builds a real .pptx
   client-side — see export-pptx.js — from this same in-memory data, loaded
   on demand via dynamic import so the pptxgenjs dependency never ships in
   this screen's own bundle for the far more common case of just reading
   the report on screen.

   ---- Chart design notes (why these forms, these colors) ----
   • Headline numbers are STAT TILES, not one-bar charts. A handful of
     scalars is a KPI row; forcing them into a chart would be dishonest.
   • Magnitude comparisons (exceptions per system/process, control groups by
     dollar impact, risks by score) are HORIZONTAL BARS — horizontal because
     the categories are long-named (control IDs, system names, risk titles)
     and a vertical column chart would have to rotate or truncate them.
     Every bar is direct-labeled at its tip, so the value axis is dropped
     entirely rather than duplicating what the labels already say.
   • Part-to-whole over an ordered severity scale (occurrences by risk
     rating) is a single STACKED SHARE BAR with a fully-valued legend.
   • MAP progress is a METER (one ratio against a limit), not a chart.
   • Color: the single-measure bars carry ONE hue (var(--acc)) for every bar
     — colouring nominal bars by their own value would spend the identity
     channel re-encoding what bar length already shows. Where colour appears
     it encodes a genuinely separate variable: the R/A/G risk rating, which
     is a reserved STATUS scale in this app, not a series palette.
   • The R/A/G trio was run through the palette validator against this app's
     real surfaces (light #ffffff, dark #0c140e — the OKLCH tokens in
     styles.css converted to sRGB). Amber↔Green measures ΔE 6.2 under
     protanopia on light and 5.3 on dark (OKLab ×100), i.e. inside and below
     the colour-vision-deficiency floor: red-green colourblind readers cannot
     separate those two by hue. Light-mode amber also sits at 2.75:1 on
     white, under the 3:1 mark floor. Both findings have the same fix, and it
     is applied everywhere a rating is shown here: the rating NEVER travels
     as colour alone — every rating mark is paired with its letter and count
     in a legend, and named in the tooltip. That pairing is also what the
     status-colour rule requires in the first place.
     var(--acc) itself measures 4.33:1 light / 6.47:1 dark, clear of 3:1.
   ============================================================ */

import { lazyGlobal } from './src/lazy-screen.js';
import {
  BarChart, Bar, XAxis, YAxis, Cell, Tooltip, ResponsiveContainer,
  LabelList, ReferenceLine,
} from 'recharts';

// app.jsx's lazyGlobal consts are local to app.jsx's own module scope and
// aren't reachable from here — this file is its own chunk, so it makes its
// own lazy references to the code-split chunks it borrows components from.
// rail.jsx (PersonaTab), charts.jsx (SeverityMatrix, truncate),
// coverage-gap.jsx (CoverageGapPanel) and components.jsx (Icon/Empty/
// SectionLabel/scoreColor/fmt$M/…) are NOT code-split — src/main.jsx imports
// them eagerly into the main bundle — so those are referenced as bare
// globals below, no lazyGlobal/Suspense needed.
const ScopeGanttLazy = lazyGlobal(() => import('./audit-scope.jsx'), 'ScopeGantt');
const ExceptionPersonaBriefsLazy = lazyGlobal(() => import('./exceptions-report.jsx'), 'ExceptionPersonaBriefs');

function _todayISO() { return new Date().toISOString().slice(0, 10); }
function _daysAgoISO(n) { const d = new Date(); d.setDate(d.getDate() - n); return d.toISOString().slice(0, 10); }

// Velocity at or above this is "accelerating" — risk-engine.js's velOf()
// emits integers in {-1,0,1,2,3}, so 2.5 selects exactly the top band and is
// the same population pipeline.jsx already calls high-velocity with its
// `risks.filter(r => r.velocity >= 3)`.
const VELOCITY_THRESHOLD = 2.5;

// Reserved status scale — NOT a categorical series palette. See the header
// note: every use of these pairs the colour with the letter and a count.
const RATING_META = {
  R: { label: "Red", fill: "var(--red)", ink: "var(--red-ink)" },
  A: { label: "Amber", fill: "var(--amber)", ink: "var(--amber-ink)" },
  G: { label: "Green", fill: "var(--green)", ink: "var(--green-ink)" },
  unrated: { label: "Unrated", fill: "var(--ink-4)", ink: "var(--ink-3)" },
};
const RATING_ORDER = ["R", "A", "G", "unrated"];

function _ratingMeta(rating) { return RATING_META[rating] || RATING_META.unrated; }

// ---------- shared board primitives ----------

function BoardStat({ label, value, sub }) {
  return (
    <div className="board-stat">
      <div className="board-stat-label">{label}</div>
      <div className="board-stat-value">{value}</div>
      {sub && <div className="board-stat-sub">{sub}</div>}
    </div>
  );
}

function BoardChartCard({ title, sub, right, children }) {
  return (
    <div className="board-chart-card">
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <div className="board-chart-title">{title}</div>
          {sub && <div className="board-chart-sub">{sub}</div>}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

// Legend for the R/A/G status scale. Always rendered wherever a rating is
// encoded as colour, and always carrying the letter + the count, so the
// rating is never conveyed by hue alone (see the header note on the
// amber↔green CVD collapse).
function RatingLegend({ counts, total }) {
  const present = RATING_ORDER.filter(k => (counts[k] || 0) > 0);
  if (!present.length) return null;
  return (
    <div className="board-legend">
      {present.map(k => {
        const meta = _ratingMeta(k);
        const n = counts[k];
        return (
          <span className="board-legend-item" key={k}>
            <span className="board-legend-swatch" style={{ background: meta.fill }} />
            <span><b>{k === "unrated" ? "—" : k}</b> {meta.label} · {n}{total ? ` (${Math.round((n / total) * 100)}%)` : ""}</span>
          </span>
        );
      })}
    </div>
  );
}

// Part-to-whole across the ordered severity scale, on one row. Segments are
// separated by a 2px gap in the surface colour rather than a stroke, and are
// deliberately unlabelled inside — at 20px tall with four possible segments a
// text label cannot be guaranteed to fit, and a clipped label is worse than
// none. The legend below carries every count and share, so no value is
// reachable only by hovering.
function RatingShareBar({ counts }) {
  const total = RATING_ORDER.reduce((s, k) => s + (counts[k] || 0), 0);
  if (!total) return null;
  return (
    <>
      <div className="board-share" role="img"
        aria-label={RATING_ORDER.filter(k => counts[k]).map(k => `${_ratingMeta(k).label}: ${counts[k]}`).join(", ")}>
        {RATING_ORDER.filter(k => counts[k] > 0).map(k => (
          <div className="board-share-seg" key={k}
            style={{ flex: `${counts[k]} 0 0`, background: _ratingMeta(k).fill }}
            title={`${_ratingMeta(k).label} — ${counts[k]} of ${total}`} />
        ))}
      </div>
      <RatingLegend counts={counts} total={total} />
    </>
  );
}

function BoardMeter({ pct }) {
  return (
    <div className="board-meter">
      <div className="board-meter-fill" style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
    </div>
  );
}

// ---------- the one chart form this screen uses ----------
// Horizontal ranked bars. `data` rows are
//   { key, label, value, full?, fill?, rating?, note? }
// label  — the (already truncated) axis text
// full   — the untruncated name, shown in the tooltip
// fill   — omit for the single-hue default (var(--acc)); pass a status colour
//          only where colour encodes a second variable
// rating — draws the status dot beside the axis label, so the rating reads
//          from a mark next to the text rather than from coloured text
// note   — the "why", shown in the tooltip (never the only home for a value)

function BoardBarTooltip({ active, payload, formatValue }) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  return (
    <div className="board-tip">
      <div className="board-tip-cat">
        {d.full || d.label}{d.rating ? ` · ${_ratingMeta(d.rating).label}` : ""}
      </div>
      <div className="board-tip-val">{formatValue ? formatValue(d.value) : d.value}</div>
      {d.note && <div className="board-tip-note">{d.note}</div>}
    </div>
  );
}

// Custom category tick: a status dot (a coloured MARK) beside muted-ink text,
// rather than colouring the label text itself.
// Looks the row up by tick INDEX, not by label text — two truncated labels can
// legitimately collide (two risk titles sharing a 22-char prefix), and a
// label-keyed lookup would then paint both dots the same rating.
function BoardCatTick({ x, y, payload, index, rows }) {
  const i = Number.isFinite(payload?.index) ? payload.index : index;
  const rating = rows?.[i]?.rating || null;
  const textX = rating ? -15 : -6;
  return (
    <g transform={`translate(${x},${y})`}>
      {rating && <circle cx={-7} cy={0} r={3.5} fill={_ratingMeta(rating).fill} />}
      <text x={textX} y={0} dy={3.5} textAnchor="end"
        fontSize={10} fontFamily="Geist Mono, monospace" fill="var(--ink-2)">
        {payload.value}
      </text>
    </g>
  );
}

function BoardBarChart({ data, formatValue, axisWidth = 124, referenceValue, referenceLabel }) {
  if (!data?.length) return null;
  // Grow the container with the data (plus room for the reference-line label)
  // instead of fixing a height — a fixed height is what crops axis bands and
  // produces a tiny nested scrollbar inside a card.
  const height = data.length * 26 + (referenceValue != null ? 26 : 10);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: referenceValue != null ? 16 : 2, right: 62, bottom: 2, left: 0 }}>
        <XAxis type="number" hide domain={[0, "dataMax"]} />
        <YAxis type="category" dataKey="label" width={axisWidth} interval={0}
          tickLine={false} axisLine={{ stroke: "var(--line)" }}
          tick={<BoardCatTick rows={data} />} />
        <Tooltip content={<BoardBarTooltip formatValue={formatValue} />}
          cursor={{ fill: "var(--hover)", fillOpacity: 0.6 }} />
        {referenceValue != null && (
          <ReferenceLine x={referenceValue} stroke="var(--ink-3)" strokeWidth={1}
            label={{ value: referenceLabel, position: "top", fontSize: 8.5,
              fontFamily: "Geist Mono, monospace", fill: "var(--ink-3)" }} />
        )}
        {/* <=24px thick, 4px rounded data-end, square at the baseline. */}
        <Bar dataKey="value" barSize={13} radius={[0, 4, 4, 0]}
          isAnimationActive={false} activeBar={{ fillOpacity: 0.75 }}>
          {data.map(d => <Cell key={d.key} fill={d.fill || "var(--acc)"} />)}
          <LabelList dataKey="value" position="right" formatter={formatValue}
            fontSize={10} fontFamily="Geist Mono, monospace" fill="var(--ink-2)" />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// ---- Section 1: Evidence Pack — was two sentences of copy and a button.
// Now leads with what the pack actually contains (register size, scope size,
// remediation progress), all of it already on this screen's props, and keeps
// the button for the full drill-down.
function EvidencePackCard({ ticker, hasRun, risks, objectives, maps, onOpen }) {
  const riskList = risks || [], objList = objectives || [], mapList = maps || [];
  const avgCompletion = mapList.length
    ? Math.round(mapList.reduce((s, m) => s + (m.completion_pct || 0), 0) / mapList.length)
    : 0;
  const closedMaps = mapList.filter(m => (m.completion_pct || 0) >= 100).length;

  return (
    <div className="stage-detail">
      <SectionLabel right={
        <button className="btn btn-sm btn-acc" onClick={onOpen} disabled={!hasRun}>
          <Icon name="doc" size={11} /> Open Evidence Pack
        </button>
      }>Assess Risk Evidence Pack</SectionLabel>
      {!hasRun ? (
        <Empty>Run Assess Enterprise Risk from the Pipeline screen to generate an evidence pack for this entity.</Empty>
      ) : (
        <>
          <div className="board-stat-row" style={{ marginBottom: 0 }}>
            <BoardStat label="Risks assessed" value={riskList.length}
              sub={`Register for ${ticker ? ticker.toUpperCase() : "this entity"}`} />
            <BoardStat label="Audit objectives" value={objList.length} sub="In the current plan" />
            <BoardStat label="Management actions" value={mapList.length}
              sub={mapList.length ? `${closedMaps} closed · ${avgCompletion}% average completion` : "None raised"} />
            <div className="board-stat">
              <div className="board-stat-label">Remediation progress</div>
              <div className="board-stat-value">{avgCompletion}%</div>
              <BoardMeter pct={avgCompletion} />
              <div className="board-stat-sub">Mean completion across all MAPs</div>
            </div>
          </div>
          <div className="board-chart-sub" style={{ margin: "10px 0 0" }}>
            The pack itself holds every data source, calculation, and AI-assisted judgment behind these figures.
          </div>
        </>
      )}
    </div>
  );
}

// ---- Section 2: Audit Plan Gantt — reuses ScopeGantt exactly as
// audit-scope.jsx renders it, just without the Kanban/sprint-filter toolbar
// (a board packet wants the whole plan at a glance, not a working view).
// Already a chart; untouched by the charts-first rework.
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

// ---- Section 3: Board Risk Profile.
// The register's shape first (severity matrix + ranked top risks, both named
// and both showing why they rank where they do), then the Board persona
// brief as a compact companion beneath rather than the section's headline
// act. PersonaTab owns its own `selected` state here — deliberately
// independent of the main app's tweaks.persona/selectedPersona (the modal's
// own state), so opening this report never changes what the Persona Report
// modal shows elsewhere, and vice versa.
//
// "Above threshold" is the register's own risk-appetite result, not a cutoff
// invented here: riskAppetite.breaching is the authoritative list of risk IDs
// with score >= riskAppetite.threshold (app.jsx Stage 2), and appetiteThreshold
// is the same APPETITE_THRESHOLDS value the Pipeline and Sankey screens use.
// When a run predates that result, the same score >= threshold filter is
// recomputed locally rather than guessed at.
function RiskPersonaBoardSection({ ticker, runId, personas, risks, loopStats, riskAppetite, appetiteThreshold }) {
  const [selected, setSelected] = React.useState("BOARD");
  const riskList = risks || [];

  const threshold = riskAppetite?.threshold ?? appetiteThreshold ?? null;
  const breachingIds = React.useMemo(() => new Set(
    riskAppetite?.breaching?.length
      ? riskAppetite.breaching
      : (threshold != null ? riskList.filter(r => r.score >= threshold).map(r => r.id) : [])
  ), [riskAppetite, threshold, riskList]);

  const accelerating = riskList.filter(r => (r.velocity || 0) >= VELOCITY_THRESHOLD);
  const ratingCounts = riskList.reduce((acc, r) => {
    const k = RATING_META[r.rag] ? r.rag : "unrated";
    acc[k] = (acc[k] || 0) + 1; return acc;
  }, {});

  // Ranked top risks. Colour encodes the RAG rating (a genuinely separate
  // variable from bar length, which is the score); the appetite threshold is
  // drawn as a reference line so "above threshold" is legible from the chart
  // itself, not just from a badge. The tooltip carries the risk's own
  // narrative — the factors driving the score — plus the explicit reason it
  // is flagged.
  const topRisks = React.useMemo(() => (
    [...riskList].sort((a, b) => b.score - a.score).slice(0, 8).map(r => {
      const why = [];
      if (breachingIds.has(r.id)) {
        why.push(threshold != null
          ? `Above appetite (score ${fmt2(r.score)} ≥ ${fmt2(threshold)})`
          : "Above risk appetite");
      }
      if ((r.velocity || 0) >= VELOCITY_THRESHOLD) why.push(`Accelerating (velocity +${r.velocity})`);
      const factors = (r.narrative || "").trim();
      return {
        key: r.id,
        // Truncation width is paired with the axisWidth this chart is given
        // below — a label the axis band cannot hold gets clipped at the SVG
        // edge, which eats the FIRST characters and is worse than truncating.
        label: truncate(r.name.split("—")[0].trim(), 20),
        full: `${r.id} · ${r.name}`,
        value: r.score,
        rating: RATING_META[r.rag] ? r.rag : "unrated",
        fill: scoreColor(r.score),
        note: [
          why.length ? why.join(" · ") : null,
          factors ? (factors.length > 190 ? factors.slice(0, 190) + "…" : factors) : null,
        ].filter(Boolean).join(" — "),
      };
    })
  ), [riskList, breachingIds, threshold]);

  return (
    <div className="stage-detail" style={{ marginTop: 20 }}>
      <SectionLabel>Board Risk Profile</SectionLabel>
      {riskList.length === 0 ? (
        <Empty>The risk register populates after Stage 2 of the loop. Run the loop from the Pipeline screen.</Empty>
      ) : (
        <>
          <div className="board-stat-row">
            <BoardStat label="Risks assessed" value={riskList.length} sub="Active register entries" />
            <BoardStat label="Above appetite" value={breachingIds.size}
              sub={threshold != null ? `Score ≥ ${fmt2(threshold)}${riskAppetite?.level ? ` · ${riskAppetite.level} appetite` : ""}` : "No appetite threshold set"} />
            <BoardStat label="Accelerating" value={accelerating.length}
              sub={`Velocity ≥ +${VELOCITY_THRESHOLD} — worsening fastest`} />
            <BoardStat label="Red-rated" value={ratingCounts.R || 0}
              sub={`of ${riskList.length} · ${ratingCounts.A || 0} amber, ${ratingCounts.G || 0} green`} />
          </div>

          <div className="board-chart-grid">
            <BoardChartCard
              title="Impact × likelihood"
              sub="Each dot is one register risk, placed by its impact and likelihood scores. Cells shade by the register's own rating bands.">
              <div style={{ maxWidth: 340, margin: "4px auto 0" }}>
                <SeverityMatrix risks={riskList} activeQ="Now" />
              </div>
              <RatingLegend counts={ratingCounts} total={riskList.length} />
            </BoardChartCard>

            <BoardChartCard
              title="Highest-scoring risks"
              sub={threshold != null
                ? `Named, ranked by residual score out of 25. Anything right of the appetite line is above threshold — hover a bar for the factors behind its score.`
                : "Named, ranked by residual score out of 25 — hover a bar for the factors behind its score."}>
              <BoardBarChart data={topRisks} formatValue={v => fmt2(v)} axisWidth={162}
                referenceValue={threshold} referenceLabel={threshold != null ? `APPETITE ${fmt2(threshold)}` : undefined} />
              <RatingLegend
                counts={topRisks.reduce((a, d) => { a[d.rating] = (a[d.rating] || 0) + 1; return a; }, {})}
                total={topRisks.length} />
            </BoardChartCard>
          </div>

          <PersonaTab personas={personas} selected={selected} setSelected={setSelected}
            ticker={ticker} risks={riskList} loopStats={loopStats} runId={runId}
            lockPersona="BOARD" autoGenerate={true} compact={true} />
        </>
      )}
    </div>
  );
}

// ---- Section 4: Coverage Gap Analysis — the existing QA report
// (coverage-gap.jsx), embedded as-is rather than reimplemented. It is
// already the tabular, board-facing answer to "which named risks are
// exposed, and why": a verdict badge, five scorecards, the register→scope
// table (now carrying each risk's velocity alongside its RAG and score), the
// quant-model and 8-K coverage checks — each finding with a `note` stating
// the driving factor — and a recommended-actions list.
function CoverageGapSection({ risks, objectives, rssSignals, events, ratios, industry, ticker }) {
  const hasRegister = (risks || []).length > 0;
  return (
    <div className="stage-detail" style={{ marginTop: 20 }}>
      <SectionLabel>Coverage Gap Analysis</SectionLabel>
      {!hasRegister ? (
        <Empty>Coverage analysis populates once the risk register exists. Run the loop from the Pipeline screen.</Empty>
      ) : (
        <div className="board-embed-report">
          <CoverageGapPanel
            risks={risks}
            objectives={objectives}
            rssSignals={rssSignals}
            events={events}
            ratios={ratios}
            industry={industry}
            ticker={ticker} />
        </div>
      )}
    </div>
  );
}

// ---- Section 5: Exception Management, Board Level — fetches this
// period's exception report itself (same trailing-30-days default as the
// Exception Report screen), charts it, and only then hands it to
// ExceptionPersonaBriefs locked to BOARD and set to auto-generate.
//
// This section is where the charts-first rework earns the most: every number
// plotted below was already in this one fetch and, before this, surfaced only
// as an AI paragraph.
const _EXC_DIMENSIONS = [
  { id: "by_system", label: "By system" },
  { id: "by_process", label: "By process" },
];

function ExceptionCharts({ report }) {
  const [dim, setDim] = React.useState("by_system");
  const summary = report.summary || {};
  const byControl = report.by_control || [];

  const dimData = React.useMemo(() => {
    const src = summary[dim] || {};
    return Object.entries(src)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([name, count]) => ({ key: name, label: truncate(name, 18), full: name, value: count }));
  }, [summary, dim]);

  // impact_usd is null for "not_computed" rows (the report's FAIR time budget
  // ran out before reaching them) — those are excluded rather than plotted as
  // zero, which would understate them as "no impact".
  const impactData = React.useMemo(() => (
    byControl
      .filter(r => Number.isFinite(r.impact_usd) && r.impact_usd > 0)
      .sort((a, b) => b.impact_usd - a.impact_usd)
      .slice(0, 8)
      .map(r => ({
        key: `${r.control_id}-${r.system_source}-${r.process}`,
        label: truncate(r.control_id, 16),
        full: `${r.control_id} · ${r.system_source} · ${r.process}`,
        value: r.impact_usd,
        rating: RATING_META[r.worst_risk_rating] ? r.worst_risk_rating : "unrated",
        fill: _ratingMeta(r.worst_risk_rating).fill,
        note: `${Number.isFinite(r.worst_risk_score) ? `Risk score ${fmt2(r.worst_risk_score)}/25 — ` : ""}${r.occurrence_count} occurrence${r.occurrence_count === 1 ? "" : "s"} in this period${
          r.impact_source === "fair_estimate" ? " · impact estimated via FAIR Monte Carlo, no transaction amount captured"
          : r.impact_source === "transaction_amount_partial" ? " · partial: sum of the occurrences that carried a transaction amount"
          : " · sum of the captured transaction amounts"}`,
      }))
  ), [byControl]);

  const ratingCounts = React.useMemo(() => {
    const src = summary.by_risk_rating || {};
    return Object.entries(src).reduce((acc, [k, v]) => {
      acc[RATING_META[k] ? k : "unrated"] = (acc[RATING_META[k] ? k : "unrated"] || 0) + v;
      return acc;
    }, {});
  }, [summary]);

  const systemCount = Object.keys(summary.by_system || {}).length;

  return (
    <>
      <div className="board-stat-row">
        <BoardStat label="Total occurrences" value={(summary.total_occurrences || 0).toLocaleString()}
          sub="Control exceptions in this period" />
        <BoardStat label="Estimated impact" value={fmt$M(summary.total_impact_usd)}
          sub={summary.impact_basis || "Transaction amounts where captured, FAIR estimate otherwise"} />
        <BoardStat label="Controls affected" value={(summary.controls_total || 0).toLocaleString()}
          sub={summary.controls_shown < summary.controls_total
            ? `Top ${summary.controls_shown} by occurrence count are charted below`
            : "All charted below"} />
        <BoardStat label="Systems affected" value={systemCount}
          sub={`Across ${Object.keys(summary.by_process || {}).length} business process${Object.keys(summary.by_process || {}).length === 1 ? "" : "es"}`} />
      </div>

      {Object.keys(ratingCounts).length > 0 && (
        <div className="board-chart-card" style={{ marginBottom: 12 }}>
          <div className="board-chart-title">Occurrences by risk rating</div>
          <div className="board-chart-sub">
            The severity mix of everything that happened this period, as a share of all occurrences.
          </div>
          <RatingShareBar counts={ratingCounts} />
        </div>
      )}

      <div className="board-chart-grid">
        <BoardChartCard
          title={dim === "by_system" ? "Exceptions by source system" : "Exceptions by business process"}
          sub="Occurrence count. Top 8 shown."
          right={
            <div className="gov-picker" style={{ flexShrink: 0 }}>
              {_EXC_DIMENSIONS.map(d => (
                <button key={d.id} className={"gov-pick-btn" + (dim === d.id ? " active" : "")}
                  onClick={() => setDim(d.id)}>{d.label}</button>
              ))}
            </div>
          }>
          {dimData.length
            ? <BoardBarChart data={dimData} formatValue={v => v.toLocaleString()} axisWidth={124} />
            : <Empty>No breakdown available for this period.</Empty>}
        </BoardChartCard>

        <BoardChartCard
          title="Costliest control groups"
          sub="Estimated dollar impact per control. Bar colour is that group's worst risk rating — see the legend.">
          {impactData.length ? (
            <>
              <BoardBarChart data={impactData} formatValue={v => fmt$M(v)} axisWidth={130} />
              <RatingLegend
                counts={impactData.reduce((a, d) => { a[d.rating] = (a[d.rating] || 0) + 1; return a; }, {})}
                total={impactData.length} />
            </>
          ) : (
            <Empty>No priced control groups in this period.</Empty>
          )}
        </BoardChartCard>
      </div>
    </>
  );
}

// dateFrom/dateTo/report/state are now owned by BoardConsolidatedReportScreen
// (lifted up from this component) rather than fetched here — the PowerPoint
// export button needs this exact period's exception data too, and lifting
// the single fetch up is simpler and safer than a second independent fetch
// or a callback threaded back down just to hand data upward.
function ExceptionBoardSection({ dateFrom, dateTo, report, state }) {
  return (
    <div className="stage-detail" style={{ marginTop: 20 }}>
      <SectionLabel>Exception Management · Board Level</SectionLabel>
      <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)", marginBottom: 10 }}>
        {dateFrom} → {dateTo}
      </div>
      {state.error ? (
        <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", background: "var(--red-soft)", padding: "6px 10px", borderRadius: 4 }}>{state.error}</div>
      ) : state.loading ? (
        <Empty>Loading this period's exception report…</Empty>
      ) : !report || report.summary.total_occurrences === 0 ? (
        <Empty icon="✓">No exceptions occurred in this period.</Empty>
      ) : (
        <>
          <ExceptionCharts report={report} />
          <React.Suspense fallback={<Empty>Loading persona brief…</Empty>}>
            <ExceptionPersonaBriefsLazy report={report} dateFrom={dateFrom} dateTo={dateTo}
              lockPersona="BOARD" autoGenerate={true} compact={true} />
          </React.Suspense>
        </>
      )}
    </div>
  );
}

// PowerPoint export — see export-pptx.js. Loaded on demand (dynamic import)
// rather than a top-of-file import: pptxgenjs is a real chunk of weight for
// a feature most visits to this screen never use, so it's kept out of this
// screen's own bundle until the button is actually clicked.
//
// The two AI briefs are fetched here independently of RiskPersonaBoardSection/
// ExceptionBoardSection's own on-screen copies rather than threaded up via a
// callback — both endpoints are cached server-side by input hash (persona +
// risk data / persona + period), and this screen already computes the exact
// same inputs those components use, so this is a guaranteed cache hit (no
// second AI spend) whenever a brief has already rendered on screen, and a
// real (correct) generation on the rare case someone exports before either
// section's autoGenerate has resolved.
function usePptxExport({ ticker, hasRun, risks, objectives, maps, loopStats, runId, riskAppetite, appetiteThreshold, excReport, dateFrom, dateTo }) {
  const [state, setState] = React.useState({ busy: false, error: null });

  const run = React.useCallback(async () => {
    setState({ busy: true, error: null });
    try {
      const [boardPersonaBrief, boardExceptionBrief] = await Promise.all([
        (hasRun && window.MCP?.aiPersonaBrief)
          ? window.MCP.aiPersonaBrief(ticker || "", "BOARD", risks || [], loopStats || {}, runId || null).catch(() => null)
          : Promise.resolve(null),
        (excReport && window.MCP?.aiExceptionBrief)
          ? window.MCP.aiExceptionBrief("BOARD", dateFrom, dateTo, excReport.summary, excReport.by_control).catch(() => null)
          : Promise.resolve(null),
      ]);
      const { exportConsolidatedReportPptx } = await import('./export-pptx.js');
      await exportConsolidatedReportPptx({
        ticker, hasRun, risks, objectives, maps, riskAppetite, appetiteThreshold,
        exceptionReport: excReport, exceptionDateFrom: dateFrom, exceptionDateTo: dateTo,
        boardPersonaBrief, boardExceptionBrief,
      });
      setState({ busy: false, error: null });
    } catch (e) {
      setState({ busy: false, error: e.message || "PowerPoint export failed" });
    }
  }, [ticker, hasRun, risks, objectives, maps, loopStats, runId, riskAppetite, appetiteThreshold, excReport, dateFrom, dateTo]);

  return { ...state, run };
}

function BoardConsolidatedReportScreen({
  ticker, runId, hasRun, objectives, maps, personas, risks, loopStats,
  riskAppetite, appetiteThreshold, rssSignals, events, ratios, industry,
  onOpenEvidencePack,
}) {
  const [dateFrom] = React.useState(_daysAgoISO(30));
  const [dateTo] = React.useState(_todayISO());
  const [excReport, setExcReport] = React.useState(null);
  const [excState, setExcState] = React.useState({ loading: true, error: null });

  React.useEffect(() => {
    let live = true;
    setExcState({ loading: true, error: null });
    window.MCP.exceptionsReport(dateFrom, dateTo)
      .then(data => { if (live) { setExcReport(data); setExcState({ loading: false, error: null }); } })
      .catch(e => { if (live) setExcState({ loading: false, error: e.message || "Exception report unavailable" }); });
    return () => { live = false; };
  }, [dateFrom, dateTo]);

  const pptxExport = usePptxExport({
    ticker, hasRun, risks, objectives, maps, loopStats, runId, riskAppetite, appetiteThreshold,
    excReport, dateFrom, dateTo,
  });

  return (
    <div className="scope-screen" data-screen-label="Consolidated Report">
      <div className="panel-head">
        <div>
          <div className="kicker">Board Intelligence · Consolidated Report</div>
          <div className="panel-title mt-8">Board Report</div>
          <div className="panel-sub">
            One packet for the board and audit committee: the current risk-assessment evidence trail, the audit
            plan, the board risk profile and its coverage gaps, and this period's exception picture — charted
            first, with the AI-written briefs kept alongside and their long-form narrative one click away.
            Scoped to {ticker || "the current entity"}.
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6, flexShrink: 0 }}>
          <button className="btn btn-sm btn-acc" onClick={pptxExport.run} disabled={!hasRun || pptxExport.busy}
            title="Download this report as an editable PowerPoint deck — native charts and tables, not a screenshot.">
            <Icon name="download" size={11} /> {pptxExport.busy ? "Exporting…" : "Export to PowerPoint"}
          </button>
          {pptxExport.error && (
            <div className="mono" style={{ fontSize: 10, color: "var(--red-ink)", maxWidth: 260, textAlign: "right" }}>
              {pptxExport.error}
            </div>
          )}
        </div>
      </div>

      <EvidencePackCard ticker={ticker} hasRun={hasRun} risks={risks} objectives={objectives}
        maps={maps} onOpen={onOpenEvidencePack} />
      <AuditPlanSection objectives={objectives} maps={maps} />
      <RiskPersonaBoardSection ticker={ticker} runId={runId} personas={personas} risks={risks}
        loopStats={loopStats} riskAppetite={riskAppetite} appetiteThreshold={appetiteThreshold} />
      <CoverageGapSection risks={risks} objectives={objectives} rssSignals={rssSignals}
        events={events} ratios={ratios} industry={industry} ticker={ticker} />
      <ExceptionBoardSection dateFrom={dateFrom} dateTo={dateTo} report={excReport} state={excState} />
    </div>
  );
}

Object.assign(window, { BoardConsolidatedReportScreen });
