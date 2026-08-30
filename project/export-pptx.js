/* ============================================================
   Board Intelligence — Consolidated Report -> PowerPoint export.

   Builds a real .pptx client-side with pptxgenjs: native editable charts,
   tables, and text — not a screenshot of the on-screen page. A board
   member (or their staff) can open this in PowerPoint and reflow/annotate
   it like any other deck.

   Data discipline matches the on-screen report: everything charted here
   comes from data the Consolidated Report screen already had in memory
   (risks, objectives, maps, the one exception-report fetch, and whichever
   AI briefs had already been generated on screen) — this module makes no
   network calls of its own and computes nothing the screen doesn't also
   show.

   Colors are fixed hex, not the app's CSS custom properties — a static
   document can't carry the light/dark theme tokens the screen switches on.
   Values below are this app's LIGHT-theme tokens (styles.css :root),
   converted OKLCH -> sRGB once, frozen here rather than recomputed per
   export. The RAG trio carries the same colorblind-accessibility caveat
   documented in board-consolidated-report.jsx's header comment (amber/green
   fall below the CVD-safe delta on this exact palette) — the mitigation
   there (never color alone) is mirrored here: every rating-colored chart
   ships a text legend, and the flagged-risks/exception tables always spell
   the rating out in a column, never relying on a swatch alone.
   ============================================================ */
import pptxgen from 'pptxgenjs';

const FONT = 'Calibri'; // ships with PowerPoint on every platform — Geist Mono
                         // (the on-screen chart font) is a webfont with no
                         // guarantee it's installed on whatever machine opens
                         // this file, so a chart-safe system font is used here.

const COLOR = {
  bg: 'FFFFFF', panel: 'F7F6F4',
  ink: '1E1A14', ink2: '4B4742', ink3: '77746F', ink4: 'A6A4A0', line: 'E5E2DE',
  acc: '008B5C', red: 'CF4040', amber: 'D98A2E', green: '2C965D',
};
const RATING_HEX = { R: COLOR.red, A: COLOR.amber, G: COLOR.green, unrated: COLOR.ink4 };
const RATING_LABEL = { R: 'Red', A: 'Amber', G: 'Green', unrated: 'Unrated' };
const RATING_ORDER = ['R', 'A', 'G', 'unrated'];

// Same escalation cutoff board-consolidated-report.jsx uses (VELOCITY_THRESHOLD) —
// kept in sync by comment, not by import, since that module isn't set up to
// export plain constants (it registers a screen component on `window`).
const VELOCITY_THRESHOLD = 2.5;

function fmt2(v) { return Number.isFinite(v) ? v.toFixed(1) : '—'; }
function fmtInt(v) { return Number.isFinite(v) ? v.toLocaleString() : '—'; }
function fmtUSD(v) { return Number.isFinite(v) ? `$${Math.round(v).toLocaleString()}` : '—'; }
function truncate(s, n) { if (!s) return ''; return s.length > n ? `${s.slice(0, n - 1)}…` : s; }
function ratingOf(rag) { return RATING_HEX[rag] ? rag : 'unrated'; }

// ---------- slide primitives ----------

function newSlide(pptx) {
  const slide = pptx.addSlide();
  slide.background = { color: COLOR.bg };
  return slide;
}

function addHeader(slide, kicker, title, sub) {
  if (kicker) {
    slide.addText(kicker.toUpperCase(), {
      x: 0.5, y: 0.28, w: 9, h: 0.3, fontFace: FONT, fontSize: 10, color: COLOR.acc, charSpacing: 1, bold: true,
    });
  }
  slide.addText(title, {
    x: 0.5, y: kicker ? 0.55 : 0.35, w: 9, h: 0.5, fontFace: FONT, fontSize: 22, bold: true, color: COLOR.ink,
  });
  if (sub) {
    slide.addText(sub, {
      x: 0.5, y: kicker ? 1.02 : 0.85, w: 9, h: 0.5, fontFace: FONT, fontSize: 11, color: COLOR.ink3,
    });
  }
}

// counts: { R, A, G, unrated } -> a one-line text legend. Used everywhere a
// chart encodes the RAG rating by color, so the rating is never carried by
// color alone in the exported deck either.
function ratingLegendText(counts) {
  const total = RATING_ORDER.reduce((s, k) => s + (counts[k] || 0), 0);
  if (!total) return '';
  return RATING_ORDER.filter(k => counts[k] > 0)
    .map(k => `${RATING_LABEL[k]}: ${counts[k]} (${Math.round((counts[k] / total) * 100)}%)`)
    .join('   ·   ');
}

function addStatRow(slide, y, stats) {
  // stats: [{ label, value, sub?, color? }] — 2 to 4 tiles across the slide width.
  const n = stats.length;
  const gap = 0.22;
  const totalW = 9;
  const w = (totalW - gap * (n - 1)) / n;
  stats.forEach((s, i) => {
    const x = 0.5 + i * (w + gap);
    slide.addShape('roundRect', {
      x, y, w, h: 1.55, fill: { color: COLOR.panel }, line: { color: COLOR.line, width: 0.75 },
    });
    slide.addText(String(s.label).toUpperCase(), {
      x: x + 0.14, y: y + 0.12, w: w - 0.28, h: 0.3, fontFace: FONT, fontSize: 8.5, color: COLOR.ink3, charSpacing: 0.5,
    });
    slide.addText(String(s.value), {
      x: x + 0.14, y: y + 0.4, w: w - 0.28, h: 0.5, fontFace: FONT, fontSize: 22, bold: true, color: s.color || COLOR.ink,
    });
    if (s.sub) {
      slide.addText(s.sub, {
        x: x + 0.14, y: y + 0.92, w: w - 0.28, h: 0.55, fontFace: FONT, fontSize: 8, color: COLOR.ink4,
      });
    }
  });
}

// Horizontal ranked bar chart — the one chart form this export uses for
// every magnitude comparison, mirroring the on-screen BoardBarChart: single
// hue by default, a status color only when `rows[].rating` is present, and
// always paired with a text legend below (never color-alone).
// rows: [{ label, value, rating? }]
function addBarChart(pptx, slide, rows, { y, h, valueFormat = '#,##0' }) {
  if (!rows.length) return;
  const ordered = [...rows]; // caller passes already-sorted, top-N rows
  const colors = ordered.map(r => (r.rating ? RATING_HEX[ratingOf(r.rating)] : COLOR.acc));
  slide.addChart(pptx.ChartType.bar, [{
    name: 'value',
    labels: ordered.map(r => r.label),
    values: ordered.map(r => r.value),
  }], {
    x: 0.5, y, w: 9, h,
    barDir: 'bar',
    chartColors: colors,
    showLegend: false,
    showValue: true,
    dataLabelFormatCode: valueFormat,
    dataLabelFontFace: FONT, dataLabelFontSize: 9, dataLabelColor: COLOR.ink2,
    catAxisLabelFontFace: FONT, catAxisLabelFontSize: 9, catAxisLabelColor: COLOR.ink2,
    catAxisLineColor: COLOR.line,
    valAxisHidden: true, valGridLine: { style: 'none' },
    barGapWidthPct: 35,
  });
}

function addTitleSlide(pptx, { ticker, hasRun, generatedAt }) {
  const slide = newSlide(pptx);
  slide.addText('BOARD INTELLIGENCE', {
    x: 0.6, y: 1.9, w: 8.8, h: 0.4, fontFace: FONT, fontSize: 14, color: COLOR.ink3, charSpacing: 2, bold: true,
  });
  slide.addText('Consolidated Report', {
    x: 0.6, y: 2.3, w: 8.8, h: 0.9, fontFace: FONT, fontSize: 36, bold: true, color: COLOR.ink,
  });
  slide.addText(ticker ? ticker.toUpperCase() : 'ENTITY NOT SET', {
    x: 0.6, y: 3.25, w: 8.8, h: 0.5, fontFace: FONT, fontSize: 18, bold: true, color: COLOR.acc,
  });
  slide.addText(
    `Generated ${generatedAt}` + (hasRun ? '' : ' — no completed risk-assessment run; some sections are limited.'),
    { x: 0.6, y: 3.85, w: 8.8, h: 0.4, fontFace: FONT, fontSize: 11, color: COLOR.ink3 },
  );
  return slide;
}

// A generated AI brief (headline/sections/callouts, from ai_endpoints.py's
// persona/exception-brief schema) as a compact slide: headline + callouts
// only, same "compact" editorial choice the on-screen report makes — the
// full prose `sections` are deliberately left off a board slide deck.
function addBriefSlide(pptx, kicker, title, brief) {
  const slide = newSlide(pptx);
  addHeader(slide, kicker, title);
  if (!brief) {
    slide.addText('Not generated for this session — open the Consolidated Report screen to produce it, then re-export.', {
      x: 0.5, y: 1.6, w: 9, h: 0.6, fontFace: FONT, fontSize: 12, color: COLOR.ink3, italic: true,
    });
    return slide;
  }
  slide.addShape('roundRect', {
    x: 0.5, y: 1.5, w: 9, h: 1.1, fill: { color: COLOR.panel }, line: { color: COLOR.acc, width: 1 },
  });
  slide.addText('AI-GENERATED · HUMAN-REVIEWED', {
    x: 0.7, y: 1.6, w: 8.6, h: 0.25, fontFace: FONT, fontSize: 8.5, color: COLOR.acc, bold: true, charSpacing: 0.5,
  });
  slide.addText(brief.headline || '', {
    x: 0.7, y: 1.85, w: 8.6, h: 0.7, fontFace: FONT, fontSize: 14, bold: true, color: COLOR.ink,
  });
  const callouts = brief.callouts || [];
  if (callouts.length) {
    slide.addText('Callouts', { x: 0.5, y: 2.85, w: 9, h: 0.35, fontFace: FONT, fontSize: 12, bold: true, color: COLOR.ink2 });
    slide.addText(
      callouts.map(c => ({ text: c, options: { bullet: { code: '2022' }, breakLine: true } })),
      { x: 0.5, y: 3.2, w: 9, h: 2.3, fontFace: FONT, fontSize: 12, color: COLOR.ink2, lineSpacingMultiple: 1.3 },
    );
  }
  return slide;
}

// ---------- section builders ----------

function buildEvidencePackSlide(pptx, { ticker, hasRun, risks, objectives, maps }) {
  const slide = newSlide(pptx);
  addHeader(slide, 'Assess Risk', 'Evidence Pack', `Register and remediation state for ${ticker ? ticker.toUpperCase() : 'this entity'}.`);
  if (!hasRun) {
    slide.addText('No completed run — run Assess Enterprise Risk from the Pipeline screen to populate this section.', {
      x: 0.5, y: 1.6, w: 9, h: 0.6, fontFace: FONT, fontSize: 12, color: COLOR.ink3, italic: true,
    });
    return slide;
  }
  const riskList = risks || [], objList = objectives || [], mapList = maps || [];
  const avgCompletion = mapList.length
    ? Math.round(mapList.reduce((s, m) => s + (m.completion_pct || 0), 0) / mapList.length)
    : 0;
  const closedMaps = mapList.filter(m => (m.completion_pct || 0) >= 100).length;
  addStatRow(slide, 1.6, [
    { label: 'Risks assessed', value: fmtInt(riskList.length), sub: 'Active register entries' },
    { label: 'Audit objectives', value: fmtInt(objList.length), sub: 'In the current plan' },
    { label: 'Management actions', value: fmtInt(mapList.length), sub: mapList.length ? `${closedMaps} closed` : 'None raised' },
    { label: 'Remediation progress', value: `${avgCompletion}%`, sub: 'Mean MAP completion', color: COLOR.acc },
  ]);
  slide.addText('The platform’s Evidence Pack holds every data source, calculation, and AI-assisted judgment behind these figures.', {
    x: 0.5, y: 3.5, w: 9, h: 0.5, fontFace: FONT, fontSize: 10.5, color: COLOR.ink3, italic: true,
  });
  return slide;
}

function buildAuditPlanSlide(pptx, { objectives, maps }) {
  const slide = newSlide(pptx);
  addHeader(slide, 'Execution', 'Audit Plan', 'Risk-linked audit objectives, by fiscal quarter.');
  const objs = objectives || [];
  if (!objs.length) {
    slide.addText('Audit objectives populate after Stage 3 of the loop.', {
      x: 0.5, y: 1.6, w: 9, h: 0.5, fontFace: FONT, fontSize: 12, color: COLOR.ink3, italic: true,
    });
    return slide;
  }
  const mapsList = maps || [];
  const avgFor = obj => {
    const ids = obj.linked_risks || (obj.linked_risk ? [obj.linked_risk] : []);
    const linked = mapsList.filter(m => ids.includes(m.linked_risk));
    if (!linked.length) return null;
    return Math.round(linked.reduce((s, m) => s + (m.completion_pct || 0), 0) / linked.length);
  };
  const rows = [
    [
      { text: 'Quarter', options: { bold: true, color: COLOR.bg, fill: { color: COLOR.ink2 } } },
      { text: 'ID', options: { bold: true, color: COLOR.bg, fill: { color: COLOR.ink2 } } },
      { text: 'Objective', options: { bold: true, color: COLOR.bg, fill: { color: COLOR.ink2 } } },
      { text: 'Priority', options: { bold: true, color: COLOR.bg, fill: { color: COLOR.ink2 } } },
      { text: 'Hours', options: { bold: true, color: COLOR.bg, fill: { color: COLOR.ink2 } } },
      { text: 'Progress', options: { bold: true, color: COLOR.bg, fill: { color: COLOR.ink2 } } },
    ],
    ...[...objs]
      .sort((a, b) => (a.sprint || 1) - (b.sprint || 1))
      .slice(0, 14)
      .map(o => {
        const pct = avgFor(o);
        return [
          { text: `Q${o.sprint || 1}` },
          { text: o.id || '—' },
          { text: truncate(o.objective || '', 60) },
          { text: o.priority || '—' },
          { text: fmtInt(o.hours || 0) },
          { text: pct == null ? '—' : `${pct}%` },
        ];
      }),
  ];
  slide.addTable(rows, {
    x: 0.5, y: 1.5, w: 9, colW: [0.7, 0.7, 4.4, 0.9, 0.8, 1.5],
    fontFace: FONT, fontSize: 9.5, color: COLOR.ink2,
    border: { type: 'solid', color: COLOR.line, pt: 0.5 },
    autoPage: true, autoPageRepeatHeader: true,
  });
  if (objs.length > 14) {
    slide.addText(`+ ${objs.length - 14} more objective${objs.length - 14 === 1 ? '' : 's'} — see the Audit Scope screen for the full plan.`, {
      x: 0.5, y: 6.9, w: 9, h: 0.3, fontFace: FONT, fontSize: 9, color: COLOR.ink4, italic: true,
    });
  }
  return slide;
}

function buildRiskProfileSlides(pptx, { risks, riskAppetite, appetiteThreshold }) {
  const riskList = risks || [];
  const slides = [];
  const s1 = newSlide(pptx);
  addHeader(s1, 'Board Risk Profile', 'Register Overview');
  if (!riskList.length) {
    s1.addText('The risk register populates after Stage 2 of the loop.', {
      x: 0.5, y: 1.6, w: 9, h: 0.5, fontFace: FONT, fontSize: 12, color: COLOR.ink3, italic: true,
    });
    slides.push(s1);
    return { slides, topRisks: [] };
  }

  const threshold = riskAppetite?.threshold ?? appetiteThreshold ?? null;
  const breachingIds = new Set(
    riskAppetite?.breaching?.length
      ? riskAppetite.breaching
      : (threshold != null ? riskList.filter(r => r.score >= threshold).map(r => r.id) : []),
  );
  const accelerating = riskList.filter(r => (r.velocity || 0) >= VELOCITY_THRESHOLD);
  const ratingCounts = riskList.reduce((acc, r) => {
    const k = ratingOf(r.rag); acc[k] = (acc[k] || 0) + 1; return acc;
  }, {});

  addStatRow(s1, 1.6, [
    { label: 'Risks assessed', value: fmtInt(riskList.length), sub: 'Active register entries' },
    {
      label: 'Above appetite', value: fmtInt(breachingIds.size), color: breachingIds.size ? COLOR.red : COLOR.ink,
      sub: threshold != null ? `Score ≥ ${fmt2(threshold)}` : 'No threshold set',
    },
    {
      label: 'Accelerating', value: fmtInt(accelerating.length), color: accelerating.length ? COLOR.amber : COLOR.ink,
      sub: `Velocity ≥ +${VELOCITY_THRESHOLD}`,
    },
    { label: 'Red-rated', value: fmtInt(ratingCounts.R || 0), color: (ratingCounts.R || 0) ? COLOR.red : COLOR.ink, sub: `of ${riskList.length} total` },
  ]);
  s1.addText(ratingLegendText(ratingCounts), { x: 0.5, y: 3.4, w: 9, h: 0.4, fontFace: FONT, fontSize: 10, color: COLOR.ink3 });
  slides.push(s1);

  // Named, ranked top risks — same population/ordering as the on-screen chart.
  const topRisks = [...riskList].sort((a, b) => b.score - a.score).slice(0, 10).map(r => ({
    id: r.id, name: r.name, label: truncate((r.name || '').split('—')[0].trim(), 26),
    value: r.score, rating: ratingOf(r.rag), velocity: r.velocity || 0,
    breaching: breachingIds.has(r.id), narrative: (r.narrative || '').trim(),
  }));

  const s2 = newSlide(pptx);
  addHeader(s2, 'Board Risk Profile', 'Highest-Scoring Risks',
    threshold != null ? `Residual score out of 25. Appetite threshold: ${fmt2(threshold)}.` : 'Residual score out of 25.');
  addBarChart(pptx, s2, topRisks.map(r => ({ label: r.label, value: r.value, rating: r.rating })), {
    y: 1.5, h: 4.3, valueFormat: '0.0',
  });
  s2.addText(
    ratingLegendText(topRisks.reduce((a, r) => { a[r.rating] = (a[r.rating] || 0) + 1; return a; }, {})),
    { x: 0.5, y: 5.85, w: 9, h: 0.35, fontFace: FONT, fontSize: 10, color: COLOR.ink3 },
  );
  slides.push(s2);

  // Flagged risks — name, factors, velocity, in a table (no hover in a
  // slide deck, so what was a tooltip on screen becomes a printed row).
  const flagged = topRisks.filter(r => r.breaching || r.velocity >= VELOCITY_THRESHOLD);
  if (flagged.length) {
    const s3 = newSlide(pptx);
    addHeader(s3, 'Board Risk Profile', 'Flagged Risks', 'Above the risk-appetite threshold, accelerating, or both.');
    const rows = [
      [
        { text: 'Risk', options: { bold: true, color: COLOR.bg, fill: { color: COLOR.ink2 } } },
        { text: 'Score', options: { bold: true, color: COLOR.bg, fill: { color: COLOR.ink2 } } },
        { text: 'Rating', options: { bold: true, color: COLOR.bg, fill: { color: COLOR.ink2 } } },
        { text: 'Velocity', options: { bold: true, color: COLOR.bg, fill: { color: COLOR.ink2 } } },
        { text: 'Driving factors', options: { bold: true, color: COLOR.bg, fill: { color: COLOR.ink2 } } },
      ],
      ...flagged.slice(0, 10).map(r => [
        { text: `${r.id} · ${truncate(r.name, 34)}` },
        { text: fmt2(r.value) },
        { text: RATING_LABEL[r.rating], options: { color: RATING_HEX[r.rating], bold: true } },
        { text: `${r.velocity > 0 ? '+' : ''}${r.velocity}`, options: { color: r.velocity >= VELOCITY_THRESHOLD ? COLOR.amber : COLOR.ink2 } },
        { text: truncate(r.narrative, 140) || '—', options: { fontSize: 8.5, color: COLOR.ink3 } },
      ]),
    ];
    s3.addTable(rows, {
      x: 0.5, y: 1.4, w: 9, colW: [2.1, 0.7, 0.9, 0.9, 4.4],
      fontFace: FONT, fontSize: 9.5, color: COLOR.ink2, valign: 'top',
      border: { type: 'solid', color: COLOR.line, pt: 0.5 },
      autoPage: true, autoPageRepeatHeader: true,
    });
    slides.push(s3);
  }

  return { slides, topRisks };
}

function buildCoverageGapSlide(pptx, { risks, objectives }) {
  const slide = newSlide(pptx);
  addHeader(slide, 'Quality Assurance', 'Coverage Gap Analysis', 'Active risk register cross-referenced against the audit scope.');
  const riskList = risks || [], objList = objectives || [];
  if (!riskList.length) {
    slide.addText('Coverage analysis populates once the risk register exists.', {
      x: 0.5, y: 1.6, w: 9, h: 0.5, fontFace: FONT, fontSize: 12, color: COLOR.ink3, italic: true,
    });
    return slide;
  }
  const covered = riskList.filter(r => objList.some(o => o.linked_risk === r.id));
  const orphaned = riskList.length - covered.length;
  const verdict = orphaned === 0 ? 'COMPLETE' : 'GAPS FOUND';
  addStatRow(slide, 1.6, [
    { label: 'Register → Scope', value: `${covered.length}/${riskList.length}`, sub: 'Risks mapped to an audit objective' },
    { label: 'Orphaned risks', value: fmtInt(orphaned), color: orphaned ? COLOR.amber : COLOR.green, sub: 'No linked audit objective' },
    { label: 'Verdict', value: verdict, color: orphaned ? COLOR.amber : COLOR.green, sub: 'Register-to-scope alignment only' },
  ]);
  slide.addText(
    'This slide summarizes register→scope alignment only. The platform’s Coverage Gap Analysis screen also checks quant-model coverage, 8-K signal coverage, and RAG-calibration divergence — open it there for the full detail.',
    { x: 0.5, y: 3.4, w: 9, h: 0.8, fontFace: FONT, fontSize: 10, color: COLOR.ink3, italic: true },
  );
  return slide;
}

function buildExceptionSlides(pptx, { report, dateFrom, dateTo }) {
  const slides = [];
  const s1 = newSlide(pptx);
  addHeader(s1, 'Exception Management · Board Level', 'Period Summary', `${dateFrom} → ${dateTo}`);
  if (!report || !report.summary || report.summary.total_occurrences === 0) {
    s1.addText('No exceptions occurred in this period.', {
      x: 0.5, y: 1.6, w: 9, h: 0.5, fontFace: FONT, fontSize: 12, color: COLOR.ink3, italic: true,
    });
    slides.push(s1);
    return slides;
  }
  const summary = report.summary;
  const byControl = report.by_control || [];
  addStatRow(s1, 1.6, [
    { label: 'Total occurrences', value: fmtInt(summary.total_occurrences), sub: 'Control exceptions in this period' },
    { label: 'Estimated impact', value: fmtUSD(summary.total_impact_usd), sub: summary.impact_basis || '', color: COLOR.acc },
    { label: 'Controls affected', value: fmtInt(summary.controls_total), sub: summary.controls_shown < summary.controls_total ? `Top ${summary.controls_shown} charted` : 'All charted' },
    { label: 'Systems affected', value: fmtInt(Object.keys(summary.by_system || {}).length), sub: `${Object.keys(summary.by_process || {}).length} process(es)` },
  ]);
  const ratingCounts = Object.entries(summary.by_risk_rating || {}).reduce((acc, [k, v]) => {
    const key = RATING_HEX[k] ? k : 'unrated'; acc[key] = (acc[key] || 0) + v; return acc;
  }, {});
  if (Object.keys(ratingCounts).length) {
    s1.addText('Occurrences by risk rating', { x: 0.5, y: 3.55, w: 9, h: 0.3, fontFace: FONT, fontSize: 11, bold: true, color: COLOR.ink2 });
    s1.addText(ratingLegendText(ratingCounts), { x: 0.5, y: 3.9, w: 9, h: 0.3, fontFace: FONT, fontSize: 10, color: COLOR.ink3 });
  }
  slides.push(s1);

  const bySystem = Object.entries(summary.by_system || {}).sort((a, b) => b[1] - a[1]).slice(0, 8)
    .map(([name, count]) => ({ label: truncate(name, 22), value: count }));
  if (bySystem.length) {
    const s2 = newSlide(pptx);
    addHeader(s2, 'Exception Management · Board Level', 'Exceptions by Source System', 'Occurrence count, top 8.');
    addBarChart(pptx, s2, bySystem, { y: 1.5, h: 4.8, valueFormat: '#,##0' });
    slides.push(s2);
  }

  const impactRows = byControl
    .filter(r => Number.isFinite(r.impact_usd) && r.impact_usd > 0)
    .sort((a, b) => b.impact_usd - a.impact_usd)
    .slice(0, 8)
    .map(r => ({ label: truncate(r.control_id, 20), value: r.impact_usd, rating: r.worst_risk_rating }));
  if (impactRows.length) {
    const s3 = newSlide(pptx);
    addHeader(s3, 'Exception Management · Board Level', 'Costliest Control Groups', 'Estimated dollar impact per control, top 8.');
    addBarChart(pptx, s3, impactRows, { y: 1.5, h: 4.3, valueFormat: '"$"#,##0' });
    s3.addText(
      ratingLegendText(impactRows.reduce((a, r) => { const k = ratingOf(r.rating); a[k] = (a[k] || 0) + 1; return a; }, {})),
      { x: 0.5, y: 5.85, w: 9, h: 0.35, fontFace: FONT, fontSize: 10, color: COLOR.ink3 },
    );
    slides.push(s3);
  }

  return slides;
}

/**
 * Build and download the Consolidated Report as a .pptx.
 *
 * @param {object} data
 * @param {string} data.ticker
 * @param {boolean} data.hasRun
 * @param {Array} data.risks
 * @param {Array} data.objectives
 * @param {Array} data.maps
 * @param {object|null} data.riskAppetite   - { threshold, level, breaching, status } or null
 * @param {number|null} data.appetiteThreshold
 * @param {object|null} data.exceptionReport - the same shape ExceptionBoardSection fetches
 * @param {string} data.exceptionDateFrom
 * @param {string} data.exceptionDateTo
 * @param {object|null} data.boardPersonaBrief   - { headline, sections, callouts } or null if not yet generated
 * @param {object|null} data.boardExceptionBrief - same shape, or null
 */
export async function exportConsolidatedReportPptx(data) {
  const pptx = new pptxgen();
  pptx.defineLayout({ name: 'BOARD_16x9', width: 10, height: 5.63 });
  pptx.layout = 'BOARD_16x9';
  pptx.author = 'Dendrai Intelligenza';
  pptx.title = `${data.ticker ? data.ticker.toUpperCase() + ' — ' : ''}Board Consolidated Report`;

  const generatedAt = new Date().toLocaleString();
  addTitleSlide(pptx, { ticker: data.ticker, hasRun: data.hasRun, generatedAt });

  buildEvidencePackSlide(pptx, data);
  buildAuditPlanSlide(pptx, data);

  const { slides: riskSlides } = buildRiskProfileSlides(pptx, data);
  void riskSlides; // already added to pptx by reference; kept for readability
  addBriefSlide(pptx, 'Board Risk Profile', 'Persona Report — Board', data.boardPersonaBrief);

  buildCoverageGapSlide(pptx, data);

  buildExceptionSlides(pptx, {
    report: data.exceptionReport, dateFrom: data.exceptionDateFrom, dateTo: data.exceptionDateTo,
  });
  addBriefSlide(pptx, 'Exception Management · Board Level', 'Exception Brief — Board', data.boardExceptionBrief);

  const tickerPart = data.ticker ? `${data.ticker.toUpperCase()}-` : '';
  const fileName = `${tickerPart}Board-Consolidated-Report-${new Date().toISOString().slice(0, 10)}.pptx`;
  await pptx.writeFile({ fileName });
  return fileName;
}
