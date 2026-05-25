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

// ---------- RISK FLOW SANKEY ----------
// Projects each risk forward 0/30/60/90 days and draws a sankey
// of RAG-bucket transitions across the timeline. "Mapping risks through".
function RiskFlowSankey({ risks }) {
  if (!risks?.length) return null;

  const W = 360, H = 360;
  const padTop = 38, padBottom = 28, padL = 28, padR = 28;
  const plotW = W - padL - padR;
  const plotH = H - padTop - padBottom;

  const stages = [
    { key: 0, lab: "Now",  sub: "T0" },
    { key: 1, lab: "+1Q",  sub: "Q1" },
    { key: 2, lab: "+2Q",  sub: "Q2" },
    { key: 3, lab: "+3Q",  sub: "Q3" },
  ];
  const ragOrder = ["R", "A", "G"];
  const ragColor = { R: "var(--red)",   A: "var(--amber)",   G: "var(--green)" };
  const ragSoft  = { R: "var(--red-soft)", A: "var(--amber-soft)", G: "var(--green-soft)" };
  const ragInk   = { R: "var(--red-ink)", A: "var(--amber-ink)", G: "var(--green-ink)" };

  // Project a single risk's score Q quarters forward using velocity
  // dampened 15% per quarter, modulated by control effectiveness.
  function projectAt(r, quartersAhead) {
    if (quartersAhead === 0) return { score: r.score, rag: ragFromScore(r.score) };
    const ceMul = ({ NONE: 1.20, WEAK: 1.10, ADEQUATE: 0.98, STRONG: 0.80 })[r.ce] || 1;
    let s = r.score;
    for (let i = 0; i < quartersAhead; i++) {
      s = s + (r.velocity || 0) * Math.pow(0.85, i) * ceMul * 0.4;
    }
    s = Math.max(0.6, Math.min(10, s));
    return { score: s, rag: ragFromScore(s) };
  }

  const total = risks.length;
  const riskPaths = risks.map(r => ({ r, path: stages.map(s => projectAt(r, s.key)) }));

  // Counts per stage per RAG
  const stageCounts = stages.map((_, sI) => {
    const c = { R: 0, A: 0, G: 0 };
    riskPaths.forEach(({ path }) => c[path[sI].rag]++);
    return c;
  });

  // Layout nodes
  const stageX = stages.map((_, i) => padL + (plotW) * (i / (stages.length - 1)));
  const nodeWidth = 9;
  const ragGap = 6;
  const usableH = plotH - ragGap * 2;

  const nodes = stages.map((_, sI) => {
    let y = padTop;
    const out = {};
    ragOrder.forEach(rag => {
      const c = stageCounts[sI][rag];
      const h = (c / total) * usableH;
      out[rag] = { y, h, count: c };
      if (c > 0) y += h + ragGap;
    });
    return out;
  });

  // Build transition ribbons
  const fromOff = stages.map(() => ({ R: 0, A: 0, G: 0 }));
  const toOff   = stages.map(() => ({ R: 0, A: 0, G: 0 }));
  const ribbons = [];
  for (let s = 0; s < stages.length - 1; s++) {
    const trans = {};
    riskPaths.forEach(({ path }) => {
      const k = `${path[s].rag}>${path[s+1].rag}`;
      trans[k] = (trans[k] || 0) + 1;
    });
    // Iterate in fixed order so stacking is deterministic.
    ragOrder.forEach(fromRag => {
      ragOrder.forEach(toRag => {
        const k = `${fromRag}>${toRag}`;
        const c = trans[k] || 0;
        if (!c) return;
        const h = (c / total) * usableH;
        const fY1 = nodes[s][fromRag].y + fromOff[s][fromRag];
        const tY1 = nodes[s+1][toRag].y + toOff[s+1][toRag];
        fromOff[s][fromRag] += h;
        toOff[s+1][toRag]   += h;
        const x1 = stageX[s]   + nodeWidth / 2;
        const x2 = stageX[s+1] - nodeWidth / 2;
        const cx = (x1 + x2) / 2;
        const path =
          `M${x1},${fY1} C${cx},${fY1} ${cx},${tY1} ${x2},${tY1}` +
          ` L${x2},${tY1 + h} C${cx},${tY1 + h} ${cx},${fY1 + h} ${x1},${fY1 + h} Z`;
        // Highlight upward transitions (worsening) with destination color full,
        // downward / stable transitions stay soft.
        const worsening = ragWorse(fromRag, toRag);
        ribbons.push({
          path, count: c, fromRag, toRag,
          color: worsening ? ragColor[toRag] : ragColor[toRag],
          opacity: worsening ? 0.55 : 0.22,
        });
      });
    });
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", display: "block" }} xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="sk-bg" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%"   stopColor="var(--surface-2)" stopOpacity="0.6"/>
          <stop offset="100%" stopColor="var(--surface-2)" stopOpacity="0"/>
        </linearGradient>
      </defs>

      {/* Stage labels */}
      {stages.map((s, i) => (
        <g key={s.key}>
          <text x={stageX[i]} y={20} textAnchor="middle" fontSize="11" fontFamily="Geist Mono, monospace"
                fontWeight="500" fill="var(--ink)">{s.lab}</text>
        </g>
      ))}

      {/* Ribbons */}
      {ribbons.map((rb, i) => (
        <path key={i} d={rb.path} fill={rb.color} opacity={rb.opacity}>
          <title>{rb.fromRag} → {rb.toRag}: {rb.count} risk{rb.count === 1 ? "" : "s"}</title>
        </path>
      ))}

      {/* Nodes (RAG bars) */}
      {stages.map((s, sI) => (
        ragOrder.map(rag => {
          const n = nodes[sI][rag];
          if (n.count === 0) return null;
          const cx = stageX[sI];
          return (
            <g key={`n-${sI}-${rag}`}>
              <rect x={cx - nodeWidth / 2} y={n.y} width={nodeWidth} height={Math.max(n.h, 1)}
                    fill={ragColor[rag]} rx={2}/>
              {n.h > 11 && (
                <text x={cx} y={n.y + n.h / 2 + 3} textAnchor="middle"
                      fontSize="9.5" fontFamily="Geist Mono, monospace" fontWeight="500"
                      fill="white" pointerEvents="none">{n.count}</text>
              )}
            </g>
          );
        })
      ))}

      {/* Stage counters under each column */}
      {stages.map((s, sI) => {
        const c = stageCounts[sI];
        const red = c.R, amb = c.A, grn = c.G;
        return (
          <g key={`c-${sI}`} transform={`translate(${stageX[sI]}, ${H - 12})`}>
            <text textAnchor="middle" fontSize="9" fontFamily="Geist Mono, monospace" fill="var(--ink-3)">
              <tspan fill="var(--red-ink)">{red}R</tspan>
              <tspan dx="6" fill="var(--amber-ink)">{amb}A</tspan>
              <tspan dx="6" fill="var(--green-ink)">{grn}G</tspan>
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// Heuristic for whether RAG transition is "worsening"
function ragWorse(from, to) {
  const rank = { G: 0, A: 1, R: 2 };
  return rank[to] > rank[from];
}

Object.assign(window, { Heatmap, ForecastChart, MScoreGauge, RiskFlowSankey });
