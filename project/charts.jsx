/* ============================================================
   Charts — pure SVG, no chart lib
   - Heatmap (impact × likelihood with Q4 projection arrows)
   - Line/Area chart (revenue/margin history + forecast)
   - M-Score gauge
   ============================================================ */

// ---------- HEATMAP ----------
function Heatmap({ risks, activeQ = "Now", onSelect, selectedId }) {
  const W = 360, H = 320, PAD = 40;
  const plotW = W - PAD * 2, plotH = H - PAD * 2;

  const points = risks.map((r, ridx) => {
    const qs = projectQuarters(r);
    const qIdx = { "Now": -1, "Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3 }[activeQ];
    const curSc = qIdx === -1 ? r.score : qs[qIdx];
    const q4Sc = qs[3];
    const curImp = clamp(r.inherent || curSc + 1, 1, 10);
    const q4Imp = clamp(curImp + (r.velocity || 0) * 0.3, 1, 10);
    const curLik = likelihoodFromCE(r.ce);
    const q4Lik = clamp(curLik + (r.velocity || 0) * 0.2, 1, 10);
    const curX = PAD + ((curLik - 1) / 9) * plotW;
    const curY = H - PAD - ((curImp - 1) / 9) * plotH;
    const q4X = PAD + ((q4Lik - 1) / 9) * plotW;
    const q4Y = H - PAD - ((q4Imp - 1) / 9) * plotH;
    const size = Math.sqrt(curImp * curLik) * 4.6;
    const q4Size = Math.sqrt(q4Imp * q4Lik) * 3.6;
    return { r, ridx, curX, curY, q4X, q4Y, size, q4Size,
      curRag: ragFromScore(curSc), q4Rag: ragFromScore(q4Sc), curSc, vel: r.velocity || 0 };
  });

  const ragSoft = { R: "color-mix(in oklch, var(--red) 65%, white)",
                    A: "color-mix(in oklch, var(--amber) 65%, white)",
                    G: "color-mix(in oklch, var(--green) 65%, white)" };
  const ragBorder = { R: "var(--red)", A: "var(--amber)", G: "var(--green)" };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width: "100%", display: "block"}} xmlns="http://www.w3.org/2000/svg">
      <defs>
        <marker id="ah-r" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="var(--red)"/></marker>
        <marker id="ah-g" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="var(--green)"/></marker>
        <marker id="ah-a" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="var(--amber)"/></marker>
        <linearGradient id="hm-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="color-mix(in oklch, var(--green) 8%, transparent)"/>
          <stop offset="100%" stopColor="color-mix(in oklch, var(--red) 8%, transparent)"/>
        </linearGradient>
      </defs>

      {/* Plot background */}
      <rect x={PAD} y={PAD} width={plotW} height={plotH} fill="url(#hm-bg)" rx="6"/>

      {/* Grid */}
      {[1,2,3,4,5,6,7,8].map((i) => {
        const gx = PAD + (i / 9) * plotW;
        const gy = H - PAD - (i / 9) * plotH;
        return (
          <g key={i}>
            <line x1={gx} y1={PAD} x2={gx} y2={H - PAD} stroke="var(--line)" strokeWidth="0.5" strokeDasharray="2 3"/>
            <line x1={PAD} y1={gy} x2={W - PAD} y2={gy} stroke="var(--line)" strokeWidth="0.5" strokeDasharray="2 3"/>
          </g>
        );
      })}
      {/* Axes */}
      <line x1={PAD} y1={PAD} x2={PAD} y2={H - PAD} stroke="var(--line-strong)" strokeWidth="1"/>
      <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="var(--line-strong)" strokeWidth="1"/>
      <text x={W/2} y={H-8} textAnchor="middle" fontSize="10" fill="var(--ink-3)" fontFamily="Geist Mono, monospace">LIKELIHOOD</text>
      <text x={12} y={H/2} textAnchor="middle" fontSize="10" fill="var(--ink-3)" fontFamily="Geist Mono, monospace" transform={`rotate(-90 12,${H/2})`}>IMPACT</text>

      {/* Tick labels */}
      {[1,3,5,7,9].map(v => (
        <g key={v}>
          <text x={PAD + (v-1)/9 * plotW} y={H - PAD + 14} textAnchor="middle" fontSize="9" fill="var(--ink-3)" fontFamily="Geist Mono, monospace">{v}</text>
          <text x={PAD - 6} y={H - PAD - (v-1)/9 * plotH + 3} textAnchor="end" fontSize="9" fill="var(--ink-3)" fontFamily="Geist Mono, monospace">{v}</text>
        </g>
      ))}

      {/* Q4 projection outlines */}
      {points.map((p, i) => {
        const dist = Math.sqrt((p.q4X - p.curX) ** 2 + (p.q4Y - p.curY) ** 2);
        if (dist < 3) return null;
        return <circle key={"q4-" + i} cx={p.q4X} cy={p.q4Y} r={p.q4Size} fill="none"
          stroke={ragBorder[p.q4Rag]} strokeWidth="1" strokeDasharray="3 2" opacity="0.4"/>;
      })}
      {/* Velocity arrows */}
      {points.map((p, i) => {
        const dx = p.q4X - p.curX, dy = p.q4Y - p.curY;
        const dist = Math.sqrt(dx*dx + dy*dy);
        if (dist < 4) return null;
        const aid = p.vel > 0 ? "ah-r" : p.vel < 0 ? "ah-g" : "ah-a";
        const sc = p.vel > 0 ? "var(--red)" : p.vel < 0 ? "var(--green)" : "var(--amber)";
        const sw = Math.max(1, Math.abs(p.vel) * 0.7);
        const ratio = Math.max(0, (dist - p.size - 6) / dist);
        const ex = p.curX + dx * (1 - 0.3 * (1 - ratio));
        const ey = p.curY + dy * (1 - 0.3 * (1 - ratio));
        const sx = p.curX + dx * (p.size / dist);
        const sy = p.curY + dy * (p.size / dist);
        return <line key={"v-" + i} x1={sx} y1={sy} x2={ex} y2={ey} stroke={sc} strokeWidth={sw} opacity="0.75" markerEnd={`url(#${aid})`}/>;
      })}
      {/* Bubbles */}
      {points.map((p) => {
        const isSel = selectedId === p.r.id;
        return (
          <g key={"b-" + p.r.id} style={{cursor: "pointer"}} onClick={() => onSelect && onSelect(p.r.id)}>
            <circle cx={p.curX} cy={p.curY} r={p.size + 8} fill="transparent"/>
            <circle cx={p.curX} cy={p.curY} r={p.size}
              fill={ragSoft[p.curRag]}
              stroke={ragBorder[p.curRag]}
              strokeWidth={isSel ? 2.4 : 1.2}
              opacity={isSel ? 1 : 0.92}/>
            {p.size > 14 ? (
              <text x={p.curX} y={p.curY + 3} textAnchor="middle" fontSize="9" fontWeight="500" fill="var(--ink)" pointerEvents="none">
                {p.r.id}
              </text>
            ) : (
              <text x={p.curX} y={p.curY + 3} textAnchor="middle" fontSize="9" fontWeight="600" fill="var(--ink)" pointerEvents="none">
                {p.r.id.replace("R-", "")}
              </text>
            )}
          </g>
        );
      })}

      {/* Active quarter label */}
      <g transform={`translate(${W - PAD}, ${PAD - 12})`}>
        <text textAnchor="end" fontSize="11" fontFamily="Geist Mono, monospace" fontWeight="500" fill="var(--acc-ink)">{activeQ}</text>
      </g>
    </svg>
  );
}

// ---------- LINE + FORECAST CHART ----------
function ForecastChart({ history, forecast, unit = "$M", color = "var(--acc)" }) {
  const W = 540, H = 220, PADL = 44, PADR = 14, PADT = 16, PADB = 28;
  const plotW = W - PADL - PADR, plotH = H - PADT - PADB;
  const all = [
    ...history.map(d => d.v),
    ...forecast.flatMap(d => [d.base, d.lo, d.hi]),
  ];
  const min = Math.min(...all) * 0.96;
  const max = Math.max(...all) * 1.04;
  const range = max - min || 1;
  const total = history.length + forecast.length;
  const step = plotW / (total - 1);

  const xy = (i, v) => [PADL + i * step, PADT + plotH - ((v - min) / range) * plotH];
  const hist = history.map((d, i) => xy(i, d.v));
  const fc = forecast.map((d, j) => xy(history.length - 1 + j + 1, d.base));
  const fcLo = forecast.map((d, j) => xy(history.length - 1 + j + 1, d.lo));
  const fcHi = forecast.map((d, j) => xy(history.length - 1 + j + 1, d.hi));
  // Connect history end to forecast start
  const transitionLine = [hist[hist.length - 1], fc[0]];

  // Band path: hi forward then lo reversed
  const lastHist = hist[hist.length - 1];
  const bandPath = "M" + lastHist[0] + "," + lastHist[1]
    + " " + fcHi.map(([x,y]) => `L${x},${y}`).join(" ")
    + " " + [...fcLo].reverse().map(([x,y]) => `L${x},${y}`).join(" ")
    + " Z";

  // Y axis ticks (4)
  const ticks = [0, .25, .5, .75, 1].map(t => min + range * t);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width: "100%", display: "block"}} xmlns="http://www.w3.org/2000/svg">
      {/* Y gridlines */}
      {ticks.map((t, i) => {
        const y = PADT + plotH - ((t - min) / range) * plotH;
        return (
          <g key={i}>
            <line x1={PADL} y1={y} x2={W - PADR} y2={y} stroke="var(--line)" strokeWidth="0.5" strokeDasharray={i === 0 ? "" : "2 3"}/>
            <text x={PADL - 6} y={y + 3} textAnchor="end" fontSize="9" fill="var(--ink-3)" fontFamily="Geist Mono, monospace">
              {unit === "$M" ? `$${t.toFixed(0)}` : `${t.toFixed(1)}%`}
            </text>
          </g>
        );
      })}
      {/* Vertical divider between history and forecast */}
      {(() => {
        const x = PADL + (history.length - 1) * step;
        return (
          <g>
            <line x1={x} y1={PADT} x2={x} y2={PADT + plotH} stroke="var(--line-strong)" strokeWidth="0.5" strokeDasharray="3 3"/>
            <text x={x + 6} y={PADT + 10} fontSize="9" fontFamily="Geist Mono, monospace" fill="var(--ink-3)">FORECAST →</text>
          </g>
        );
      })()}

      {/* Confidence band */}
      <path d={bandPath} fill={color} opacity="0.10"/>

      {/* History line */}
      <polyline points={hist.map(p => p.join(",")).join(" ")} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"/>
      {/* Forecast (dashed) */}
      <line x1={transitionLine[0][0]} y1={transitionLine[0][1]} x2={transitionLine[1][0]} y2={transitionLine[1][1]}
        stroke={color} strokeWidth="2" strokeDasharray="5 4" opacity="0.85"/>
      <polyline points={fc.map(p => p.join(",")).join(" ")} fill="none" stroke={color} strokeWidth="2" strokeDasharray="5 4" strokeLinejoin="round" strokeLinecap="round" opacity="0.85"/>

      {/* Points */}
      {hist.map(([x, y], i) => <circle key={"h" + i} cx={x} cy={y} r="2.5" fill={color}/>)}
      {fc.map(([x, y], i) => <circle key={"f" + i} cx={x} cy={y} r="3" fill="white" stroke={color} strokeWidth="1.6"/>)}

      {/* X labels (every other tick) */}
      {[...history, ...forecast].map((d, i) => {
        if (i % 2 !== 0) return null;
        const x = PADL + i * step;
        return <text key={"x" + i} x={x} y={H - 8} textAnchor="middle" fontSize="9" fill="var(--ink-3)" fontFamily="Geist Mono, monospace">{d.q}</text>;
      })}
    </svg>
  );
}

// ---------- M-Score gauge ----------
function MScoreGauge({ m, redThreshold = -1.78, amberThreshold = -2.22 }) {
  // Scale: -4 (left/green) → 0 (right/red). Visual mapping: clamp.
  const min = -4, max = 0;
  const pct = clamp((m - min) / (max - min), 0, 1);
  const band = m > redThreshold ? "RED" : m > amberThreshold ? "AMBER" : "GREEN";
  const bandColor = band === "RED" ? "var(--red)" : band === "AMBER" ? "var(--amber)" : "var(--green)";
  const bandInk   = band === "RED" ? "var(--red-ink)" : band === "AMBER" ? "var(--amber-ink)" : "var(--green-ink)";
  return (
    <div>
      <div style={{position:"relative", height: 12, borderRadius: 6, overflow: "hidden",
        background: "linear-gradient(90deg, var(--green-soft), var(--amber-soft), var(--red-soft))",
        border: "1px solid var(--line)"}}>
        <div style={{position:"absolute", left: `${pct * 100}%`, top: -2, bottom: -2, width: 2, background: "var(--ink)"}}/>
      </div>
      <div className="mono" style={{display:"flex", justifyContent:"space-between", fontSize: 10, color: "var(--ink-3)", marginTop: 4}}>
        <span>-4.0</span><span>-2.22</span><span>-1.78</span><span>0.0</span>
      </div>
      <div style={{display:"flex", alignItems:"baseline", gap: 8, marginTop: 8}}>
        <span className="mono" style={{fontSize: 22, fontWeight: 500, letterSpacing: "-0.02em"}}>{m.toFixed(2)}</span>
        <span className="rag-chip" style={{background: `color-mix(in oklch, ${bandColor} 14%, transparent)`, color: bandInk}}>{band}</span>
      </div>
    </div>
  );
}

// ---------- RISK FLOW SANKEY (audit closed loop) ----------
// 3-column sankey: each KEY RISK fans out to the business areas
// it impacts and to the audit/control work addressing it. Hovering
// or clicking a risk highlights its full path. A velocity strip
// below the chart shows when oversight cadence ramps up over 90d.
function RiskFlowSankey({ risks, maps, flowMeta, selectedId, onSelect, onHover, hoverId, rssSignals, fredData, appetiteThreshold = 7.0 }) {
  if (!risks?.length || !flowMeta) return null;

  // Top risks by score
  const topRisks = [...risks].sort((a, b) => b.score - a.score).slice(0, 6);

  // Collect distinct impact areas across selected risks
  const impactSet = new Set();
  topRisks.forEach(r => (flowMeta[r.id]?.impacts || []).forEach(im => impactSet.add(im)));
  const impacts = [...impactSet];

  // Audit nodes: derive from linked MAPs + listed audits
  // We group into: "In-flight MAP", "Audit on plan", "Closed MAP"
  const auditGroups = [
    { id: "open",   label: "MAP · in flight",       color: "var(--amber)",  ink: "var(--amber-ink)" },
    { id: "plan",   label: "Audit on plan",         color: "var(--acc)",    ink: "var(--acc-ink)" },
    { id: "closed", label: "Closed / completed",    color: "var(--green)",  ink: "var(--green-ink)" },
  ];

  // For each risk, classify its audit count into the 3 buckets
  // based on the linked MAP completion %.
  function audCountsFor(rid) {
    const meta = flowMeta[rid];
    if (!meta) return { open: 0, plan: 0, closed: 0 };
    // Number of audit/MAP items
    const linkedMaps = (maps || []).filter(m => m.linked_risk === rid);
    const planned = Math.max(0, (meta.audits?.length || 0) - linkedMaps.length);
    const open = linkedMaps.filter(m => (m.completion_pct || 0) < 100).length;
    const closed = linkedMaps.filter(m => (m.completion_pct || 0) >= 100).length;
    return { open, plan: planned, closed };
  }

  // Residual risk computation
  function computeResidual(risk) {
    const velContrib = (risk.velocity || 0) * 0.35;
    const ceAdj = ({ STRONG: -0.7, ADEQUATE: -0.3, WEAK: 0.1, NONE: 0.4 })[risk.ce] || 0;

    // RSS contribution: sum velocity of signals linked to this risk
    const rssVel = (rssSignals || [])
      .filter(s => (s.affectedRisks || []).includes(risk.id) ||
        (s.domains || []).some(d => (flowMeta[risk.id]?.impacts || []).some(im => im.toLowerCase().includes(d.toLowerCase()))))
      .reduce((sum, s) => sum + (s.velocity || 0) * 0.08, 0);

    // Macro: average FRED velocity signal (contractionary = +0.2, neutral = 0, expansionary = -0.1)
    const macroAdj = (fredData || []).filter(f => f.dir === "CONTRACTIONARY").length * 0.08;

    const projected = risk.score + velContrib + ceAdj + rssVel + macroAdj;
    return Math.max(1, Math.min(10, parseFloat(projected.toFixed(1))));
  }

  // ---- Layout ----
  const W = 940, H = 380;
  const padT = 36, padB = 28;
  const colW = 8;                      // node bar width
  const labelGapL = 14;                // left col label gap
  const labelGapR = 14;
  // Column X positions for the node BARS
  const xRisk    = 168;
  const xImpact  = 360;
  const xAudit   = 560;
  const xResidual = 760;
  const plotH = H - padT - padB;

  // Compute risk node sizes by score weight (relative)
  const ragColor = { R: "var(--red)", A: "var(--amber)", G: "var(--green)" };
  const ragInk   = { R: "var(--red-ink)", A: "var(--amber-ink)", G: "var(--green-ink)" };

  const riskWeights = topRisks.map(r => Math.max(2, r.score));
  const totalRW = riskWeights.reduce((a, b) => a + b, 0);

  // Risk node heights
  const gapR = 8;
  const usableR = plotH - gapR * (topRisks.length - 1);
  const riskNodes = {};
  {
    let y = padT;
    topRisks.forEach((r, i) => {
      const h = (riskWeights[i] / totalRW) * usableR;
      riskNodes[r.id] = { y, h, r };
      y += h + gapR;
    });
  }

  // Impact nodes — height = sum of risk-weights flowing into it
  const impactWeights = {};
  impacts.forEach(im => { impactWeights[im] = 0; });
  topRisks.forEach((r, i) => {
    (flowMeta[r.id]?.impacts || []).forEach(im => {
      if (impactWeights[im] === undefined) return;
      // Each risk contributes equally to each of its impacts
      const n = (flowMeta[r.id].impacts || []).length || 1;
      impactWeights[im] += riskWeights[i] / n;
    });
  });
  const totalImW = Object.values(impactWeights).reduce((a, b) => a + b, 0);
  const gapI = 6;
  const usableI = plotH - gapI * (impacts.length - 1);
  const impactNodes = {};
  {
    // Sort impacts by descending weight for cleaner ribbons
    const sorted = [...impacts].sort((a, b) => impactWeights[b] - impactWeights[a]);
    let y = padT;
    sorted.forEach(im => {
      const h = (impactWeights[im] / totalImW) * usableI;
      impactNodes[im] = { y, h, im };
      y += h + gapI;
    });
  }

  // Audit nodes — width = sum of risk-weights contributing items in each bucket
  const audGroupWeights = { open: 0, plan: 0, closed: 0 };
  topRisks.forEach((r, i) => {
    const c = audCountsFor(r.id);
    const total = c.open + c.plan + c.closed;
    if (total === 0) return;
    audGroupWeights.open   += (riskWeights[i] * c.open)   / total;
    audGroupWeights.plan   += (riskWeights[i] * c.plan)   / total;
    audGroupWeights.closed += (riskWeights[i] * c.closed) / total;
  });
  const totalAW = Object.values(audGroupWeights).reduce((a, b) => a + b, 0) || 1;
  const gapA = 10;
  const usableA = plotH - gapA * (auditGroups.length - 1);
  const auditNodes = {};
  {
    let y = padT;
    auditGroups.forEach(g => {
      const h = (audGroupWeights[g.id] / totalAW) * usableA;
      auditNodes[g.id] = { y, h, ...g };
      y += h + gapA;
    });
  }

  // Build ribbons risk → impact
  const ribbonsRI = [];
  // Track per-source/per-dest offsets so stacking works
  const offR = {}; // risk side outflow
  const offI = {}; // impact side inflow
  topRisks.forEach((r, i) => offR[r.id] = 0);
  impacts.forEach(im => offI[im] = 0);
  topRisks.forEach((r, i) => {
    const meta = flowMeta[r.id];
    if (!meta) return;
    const list = meta.impacts || [];
    const n = list.length || 1;
    list.forEach(im => {
      if (!impactNodes[im]) return;
      const w = riskWeights[i] / n;
      const hSrc = (w / totalRW) * usableR;
      const hDst = (w / totalImW) * usableI;
      const ry = riskNodes[r.id].y + offR[r.id];
      const iy = impactNodes[im].y + offI[im];
      offR[r.id] += hSrc;
      offI[im]   += hDst;
      const x1 = xRisk + colW;
      const x2 = xImpact;
      const cx = (x1 + x2) / 2;
      const path =
        `M${x1},${ry} C${cx},${ry} ${cx},${iy} ${x2},${iy}` +
        ` L${x2},${iy + hDst} C${cx},${iy + hDst} ${cx},${ry + hSrc} ${x1},${ry + hSrc} Z`;
      ribbonsRI.push({ riskId: r.id, path, rag: r.rag });
    });
  });

  // Build ribbons impact → audit (route each impact's weight across audit
  // buckets weighted by the same per-risk audit-bucket split)
  const ribbonsIA = [];
  // Per impact, we know its inflow composition by risk. Re-derive that.
  const impactRiskShare = {}; // {impact: {riskId: weight}}
  impacts.forEach(im => impactRiskShare[im] = {});
  topRisks.forEach((r, i) => {
    const list = flowMeta[r.id]?.impacts || [];
    const n = list.length || 1;
    list.forEach(im => {
      if (impactRiskShare[im] === undefined) return;
      impactRiskShare[im][r.id] = (impactRiskShare[im][r.id] || 0) + riskWeights[i] / n;
    });
  });
  const offI_out = {}; impacts.forEach(im => offI_out[im] = 0);
  const offA_in = { open: 0, plan: 0, closed: 0 };
  // Iterate impacts in same sort order as nodes for stacking parity
  const sortedImpacts = [...impacts].sort((a, b) => impactNodes[a].y - impactNodes[b].y);
  sortedImpacts.forEach(im => {
    const riskShare = impactRiskShare[im];
    Object.entries(riskShare).forEach(([rid, w]) => {
      const ric = audCountsFor(rid);
      const tot = ric.open + ric.plan + ric.closed;
      if (tot === 0) return;
      ["open", "plan", "closed"].forEach(g => {
        const portion = (ric[g] / tot) * w;
        if (!portion) return;
        const hDst = (portion / totalAW) * usableA;
        // src side stacking uses impact column
        const hSrc = (portion / totalImW) * usableI;
        const iy = impactNodes[im].y + offI_out[im];
        const ay = auditNodes[g].y + offA_in[g];
        offI_out[im] += hSrc;
        offA_in[g]   += hDst;
        const x1 = xImpact + colW;
        const x2 = xAudit;
        const cx = (x1 + x2) / 2;
        const path =
          `M${x1},${iy} C${cx},${iy} ${cx},${ay} ${x2},${ay}` +
          ` L${x2},${ay + hDst} C${cx},${ay + hDst} ${cx},${iy + hSrc} ${x1},${iy + hSrc} Z`;
        ribbonsIA.push({ riskId: rid, path, rag: risks.find(rr => rr.id === rid)?.rag || "A", group: g });
      });
    });
  });

  // Pre-compute all projections so we can sort residual column by projected score
  const projectionsByRisk = {};
  topRisks.forEach(r => { projectionsByRisk[r.id] = computeResidual(r); });

  // Sort residual nodes by projected score descending (highest breach shown first)
  const sortedByResidual = [...topRisks].sort((a, b) => projectionsByRisk[b.id] - projectionsByRisk[a.id]);

  const residualNodes = {};
  {
    let y = padT;
    sortedByResidual.forEach(r => {
      const origIdx = topRisks.indexOf(r);
      const proj = projectionsByRisk[r.id];
      const projRag = proj >= 7.5 ? "R" : proj >= 5.0 ? "A" : "G";
      const h = (riskWeights[origIdx] / totalRW) * usableR;
      residualNodes[r.id] = {
        y, h, proj, projRag,
        delta: proj - r.score,
        breachesAppetite: proj >= appetiteThreshold,
      };
      y += h + gapR;
    });
  }

  // Build ribbons audit → residual (per risk, proportional to its audit weight)
  const ribbonsAR = [];
  const offA_out = { open: 0, plan: 0, closed: 0 };
  const offRes_in = {};
  topRisks.forEach(r => { offRes_in[r.id] = 0; });

  topRisks.forEach((r, i) => {
    const rac = audCountsFor(r.id);
    const tot = rac.open + rac.plan + rac.closed;
    if (tot === 0) return;
    const rn = residualNodes[r.id];
    ["open", "plan", "closed"].forEach(g => {
      const portion = (rac[g] / tot) * riskWeights[i];
      if (!portion) return;
      const hSrc = (portion / totalAW) * usableA;
      const hDst = (portion / totalRW) * usableR;
      const ay = auditNodes[g].y + offA_out[g];
      const ry2 = rn.y + offRes_in[r.id];
      offA_out[g]     += hSrc;
      offRes_in[r.id] += hDst;
      const x1 = xAudit + colW;
      const x2 = xResidual;
      const cx = (x1 + x2) / 2;
      const path =
        `M${x1},${ay} C${cx},${ay} ${cx},${ry2} ${x2},${ry2}` +
        ` L${x2},${ry2 + hDst} C${cx},${ry2 + hDst} ${cx},${ay + hSrc} ${x1},${ay + hSrc} Z`;
      ribbonsAR.push({ riskId: r.id, path, projRag: rn.projRag, group: g });
    });
  });

  const activeId = hoverId || selectedId;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", display: "block" }} xmlns="http://www.w3.org/2000/svg"
      onMouseLeave={() => onHover && onHover(null)}>
      <defs>
        <linearGradient id="rfs-band" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%"   stopColor="var(--surface-2)" stopOpacity="0.4"/>
          <stop offset="100%" stopColor="var(--surface-2)" stopOpacity="0"/>
        </linearGradient>
      </defs>

      {/* Column headers */}
      <text x={xRisk + colW / 2} y={18} textAnchor="middle" fontSize="10" fontFamily="Geist Mono, monospace"
            letterSpacing="0.06em" fill="var(--ink-3)">RISK</text>
      <text x={xImpact + colW / 2} y={18} textAnchor="middle" fontSize="10" fontFamily="Geist Mono, monospace"
            letterSpacing="0.06em" fill="var(--ink-3)">IMPACT AREA</text>
      <text x={xAudit + colW / 2} y={18} textAnchor="middle" fontSize="10" fontFamily="Geist Mono, monospace"
            letterSpacing="0.06em" fill="var(--ink-3)">AUDIT &amp; MAP</text>
      <text x={xResidual + colW / 2} y={14} textAnchor="middle" fontSize="10" fontFamily="Geist Mono, monospace"
            letterSpacing="0.06em" fill="var(--ink-3)">RESIDUAL RISK</text>
      <text x={xResidual + colW / 2} y={26} textAnchor="middle" fontSize="8" fontFamily="Geist Mono, monospace"
            fill="var(--red-ink)" opacity="0.75">appetite ≥ {appetiteThreshold}</text>

      {/* Ribbons risk → impact */}
      {ribbonsRI.map((rb, i) => {
        const isActive = activeId ? rb.riskId === activeId : false;
        const dimmed = activeId && !isActive;
        return (
          <path key={`ri-${i}`} d={rb.path} fill={ragColor[rb.rag]}
                opacity={dimmed ? 0.06 : isActive ? 0.55 : 0.22}/>
        );
      })}
      {/* Ribbons impact → audit */}
      {ribbonsIA.map((rb, i) => {
        const isActive = activeId ? rb.riskId === activeId : false;
        const dimmed = activeId && !isActive;
        return (
          <path key={`ia-${i}`} d={rb.path} fill={auditGroups.find(g => g.id === rb.group).color}
                opacity={dimmed ? 0.05 : isActive ? 0.5 : 0.18}/>
        );
      })}
      {/* Ribbons audit → residual */}
      {ribbonsAR.map((rb, i) => {
        const isActive = activeId ? rb.riskId === activeId : false;
        const dimmed = activeId && !isActive;
        return (
          <path key={`ar-${i}`} d={rb.path} fill={ragColor[rb.projRag]}
                opacity={dimmed ? 0.05 : isActive ? 0.45 : 0.16}/>
        );
      })}

      {/* Risk nodes + labels */}
      {topRisks.map(r => {
        const n = riskNodes[r.id];
        const isActive = activeId === r.id;
        const dimmed = activeId && !isActive;
        return (
          <g key={`rn-${r.id}`} style={{ cursor: "pointer" }}
             onMouseEnter={() => onHover && onHover(r.id)}
             onClick={() => onSelect && onSelect(r.id === selectedId ? null : r.id)}>
            <rect x={xRisk - 162} y={n.y - 2} width={162 + colW + 4} height={n.h + 4} fill="transparent"/>
            <rect x={xRisk} y={n.y} width={colW} height={Math.max(n.h, 2)} rx={2}
                  fill={ragColor[r.rag]} opacity={dimmed ? 0.4 : 1}/>
            <text x={xRisk - labelGapL} y={n.y + n.h / 2 - 2} textAnchor="end"
                  fontSize="11" fontWeight={isActive ? 600 : 500}
                  fill={dimmed ? "var(--ink-4)" : "var(--ink)"}>
              {truncate(r.name, 22)}
            </text>
            <text x={xRisk - labelGapL} y={n.y + n.h / 2 + 10} textAnchor="end"
                  fontSize="9.5" fontFamily="Geist Mono, monospace"
                  fill={dimmed ? "var(--ink-4)" : ragInk[r.rag]}>
              {r.id} · {r.score.toFixed(1)} · v{r.velocity >= 0 ? "+" : ""}{r.velocity}
            </text>
          </g>
        );
      })}

      {/* Impact nodes + labels */}
      {impacts.map(im => {
        const n = impactNodes[im];
        if (!n) return null;
        // Find which highlighted risks pass through
        const involved = activeId
          ? (flowMeta[activeId]?.impacts || []).includes(im)
          : false;
        const dimmed = activeId && !involved;
        return (
          <g key={`in-${im}`}>
            <rect x={xImpact} y={n.y} width={colW} height={Math.max(n.h, 2)} rx={2}
                  fill="var(--ink-2)" opacity={dimmed ? 0.25 : 0.8}/>
            <text x={xImpact + colW + 6} y={n.y + n.h / 2 + 4}
                  fontSize="10.5" fontWeight={involved ? 500 : 400}
                  fill={dimmed ? "var(--ink-4)" : "var(--ink-2)"}>
              {truncate(im, 18)}
            </text>
          </g>
        );
      })}

      {/* Audit nodes + labels */}
      {auditGroups.map(g => {
        const n = auditNodes[g.id];
        if (!n) return null;
        return (
          <g key={`an-${g.id}`}>
            <rect x={xAudit} y={n.y} width={colW} height={Math.max(n.h, 2)} rx={2}
                  fill={g.color}/>
            <text x={xAudit + colW + 6} y={n.y + n.h / 2 - 2}
                  fontSize="11" fontWeight="500" fill={g.ink}>
              {g.label}
            </text>
            <text x={xAudit + colW + 6} y={n.y + n.h / 2 + 11}
                  fontSize="9.5" fontFamily="Geist Mono, monospace" fill="var(--ink-3)">
              {/* compute count */}
              {(() => {
                let c = 0;
                topRisks.forEach(r => { c += audCountsFor(r.id)[g.id]; });
                return `${c} item${c === 1 ? "" : "s"}`;
              })()}
            </text>
          </g>
        );
      })}

      {/* Residual risk nodes + labels — sorted by projected score, breaches flagged */}
      {topRisks.map(r => {
        const rn = residualNodes[r.id];
        if (!rn) return null;
        const isActive = activeId === r.id;
        const dimmed = activeId && !isActive;
        const deltaSign = rn.delta >= 0 ? "+" : "";
        const labelColor = ragColor[rn.projRag];
        const labelInk = ragInk[rn.projRag];
        return (
          <g key={`res-${r.id}`}>
            {rn.breachesAppetite && (
              <rect x={xResidual - 3} y={rn.y - 3} width={colW + 6} height={Math.max(rn.h, 2) + 6} rx={3}
                    fill="none" stroke="var(--red)" strokeWidth={1.5} strokeDasharray="3 2"
                    opacity={dimmed ? 0.2 : 0.65}/>
            )}
            <rect x={xResidual} y={rn.y} width={colW} height={Math.max(rn.h, 2)} rx={2}
                  fill={labelColor} opacity={dimmed ? 0.3 : 1}/>
            <text x={xResidual + colW + 6} y={rn.y + rn.h / 2 - 2}
                  fontSize="11" fontWeight="500" fill={dimmed ? "var(--ink-4)" : labelInk}>
              {rn.proj.toFixed(1)}{rn.breachesAppetite && !dimmed ? " !" : ""}
            </text>
            <text x={xResidual + colW + 6} y={rn.y + rn.h / 2 + 11}
                  fontSize="9.5" fontFamily="Geist Mono, monospace"
                  fill={dimmed ? "var(--ink-4)" : "var(--ink-3)"}>
              {deltaSign}{rn.delta.toFixed(1)} projected
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function truncate(s, n) { return s.length > n ? s.slice(0, n - 1) + "…" : s; }

Object.assign(window, { Heatmap, ForecastChart, MScoreGauge, RiskFlowSankey });
