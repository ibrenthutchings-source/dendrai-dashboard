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

// ---------- RISK FLOW SANKEY (4-column interactive) ----------
// Risk Domain → Severity → Control Maturity → Mitigation Status

const _SK_DOMAIN_COLORS = {
  "Financial Reporting": "#6366f1",
  "Trade Compliance":    "#a855f7",
  "Operational":         "#f97316",
  "Cybersecurity":       "#ef4444",
  "ESG":                 "#22c55e",
  "Legal":               "#eab308",
  "Macro":               "#3b82f6",
  "Supply":              "#14b8a6",
};
const _SK_DOMAIN_ABBR = {
  "Financial Reporting": "Fin.Rpt",
  "Trade Compliance":    "Trade",
  "Operational":         "Ops",
  "Cybersecurity":       "Cyber",
  "ESG":                 "ESG",
  "Legal":               "Legal",
  "Macro":               "Macro",
  "Supply":              "Supply",
};
const _SK_SEV_COLORS = { High: "#ef4444", Medium: "#f59e0b", Low: "#22c55e" };
const _SK_CE_COLORS  = { NONE: "#ef4444", WEAK: "#f59e0b", ADEQUATE: "#3b82f6", STRONG: "#22c55e" };
const _SK_MIT_COLORS = { Open: "#ef4444", "In Progress": "#f59e0b", Closed: "#22c55e", "No MAP": "#94a3b8" };

const _SK_COLS = [
  {
    key: "domain", label: "Risk Domain",
    order: Object.keys(_SK_DOMAIN_COLORS), colors: _SK_DOMAIN_COLORS,
    abbr: k => _SK_DOMAIN_ABBR[k] || k,
  },
  {
    key: "severity", label: "Severity",
    order: ["High", "Medium", "Low"], colors: _SK_SEV_COLORS,
    abbr: k => k,
  },
  {
    key: "ce", label: "Control",
    order: ["NONE", "WEAK", "ADEQUATE", "STRONG"], colors: _SK_CE_COLORS,
    abbr: k => ({ NONE: "None", WEAK: "Weak", ADEQUATE: "Adq.", STRONG: "Str." })[k] || k,
  },
  {
    key: "mitigation", label: "Mitigation",
    order: ["Open", "In Progress", "Closed", "No MAP"], colors: _SK_MIT_COLORS,
    abbr: k => ({ Open: "Open", "In Progress": "In Prg.", Closed: "Closed", "No MAP": "No MAP" })[k] || k,
  },
];

function RiskFlowSankey({ risks, maps = [] }) {
  const [hovered, setHovered] = React.useState(null);
  if (!risks?.length) return null;

  // Best MAP per risk (highest completion_pct wins)
  const mapByRisk = {};
  (maps || []).forEach(m => {
    if (!m.linked_risk) return;
    const cur = mapByRisk[m.linked_risk];
    if (!cur || (m.completion_pct || 0) > (cur.completion_pct || 0)) mapByRisk[m.linked_risk] = m;
  });

  const riskData = risks.map(r => {
    const m = mapByRisk[r.id];
    let mit;
    if (!m)                                mit = "No MAP";
    else if ((m.completion_pct || 0) >= 100) mit = "Closed";
    else if ((m.completion_pct || 0) > 0)   mit = "In Progress";
    else                                     mit = "Open";
    return {
      r,
      domain:     r.category || "Unknown",
      severity:   r.rag === "R" ? "High" : r.rag === "A" ? "Medium" : "Low",
      ce:         r.ce || "NONE",
      mitigation: mit,
      weight:     r.score || 1,
    };
  });

  const W = 680, H = 420;
  const padT = 36, padB = 12, padL = 14, padR = 14;
  const plotH = H - padT - padB;
  const plotW = W - padL - padR;
  const nodeW = 12, nodeGap = 6;

  const colX = [0, 1, 2, 3].map(i => padL + i * (plotW / 3));
  const total = riskData.reduce((s, d) => s + d.weight, 0);

  // Node weights per column
  const nodeWeights = _SK_COLS.map(col => {
    const w = {};
    riskData.forEach(d => { const k = d[col.key]; w[k] = (w[k] || 0) + d.weight; });
    return w;
  });

  // Present keys per column
  const presentKeys = _SK_COLS.map((col, ci) => col.order.filter(k => nodeWeights[ci][k] > 0));

  // Single usableH across all columns for consistent link-to-node scaling
  const maxNodes = Math.max(...presentKeys.map(pk => pk.length));
  const usableH = plotH - (maxNodes - 1) * nodeGap;

  // Layout nodes
  const layoutNodes = _SK_COLS.map((col, ci) => {
    let y = padT;
    return presentKeys[ci].map(key => {
      const w = nodeWeights[ci][key];
      const h = (w / total) * usableH;
      const node = { key, w, h, y, color: col.colors[key] || "#94a3b8" };
      y += h + nodeGap;
      return node;
    });
  });

  const nodeMap = layoutNodes.map(nodes => Object.fromEntries(nodes.map(n => [n.key, n])));

  // Offset trackers for stacking links within nodes
  const fromOff = layoutNodes.map(nodes => Object.fromEntries(nodes.map(n => [n.key, 0])));
  const toOff   = layoutNodes.map(nodes => Object.fromEntries(nodes.map(n => [n.key, 0])));

  const renderedLinks = [];
  for (let c = 0; c < 3; c++) {
    const fromKey = _SK_COLS[c].key;
    const toKey   = _SK_COLS[c + 1].key;
    const lm = {};
    riskData.forEach(d => {
      const fk = d[fromKey], tk = d[toKey];
      const lk = `${fk}||${tk}`;
      if (!lm[lk]) lm[lk] = { from: fk, to: tk, weight: 0, risks: [] };
      lm[lk].weight += d.weight;
      lm[lk].risks.push(d.r);
    });
    Object.values(lm)
      .sort((a, b) => {
        const fo = _SK_COLS[c].order, to2 = _SK_COLS[c + 1].order;
        const fd = fo.indexOf(a.from) - fo.indexOf(b.from);
        return fd !== 0 ? fd : to2.indexOf(a.to) - to2.indexOf(b.to);
      })
      .forEach(link => {
        const fn = nodeMap[c][link.from];
        const tn = nodeMap[c + 1][link.to];
        if (!fn || !tn) return;
        const h  = (link.weight / total) * usableH;
        const x1 = colX[c]     + nodeW / 2;
        const x2 = colX[c + 1] - nodeW / 2;
        const y1 = fn.y + fromOff[c][link.from];
        const y2 = tn.y + toOff[c + 1][link.to];
        const cx = (x1 + x2) / 2;
        fromOff[c][link.from]     += h;
        toOff[c + 1][link.to]     += h;
        const d =
          `M${x1},${y1} C${cx},${y1} ${cx},${y2} ${x2},${y2}` +
          ` L${x2},${y2 + h} C${cx},${y2 + h} ${cx},${y1 + h} ${x1},${y1 + h} Z`;
        renderedLinks.push({
          d, col: c, from: link.from, to: link.to,
          weight: link.weight, risks: link.risks,
          color: fn.color,
          id: `lnk-${c}-${link.from}-${link.to}`,
        });
      });
  }

  const isActive = lnk => {
    if (!hovered) return true;
    if (hovered.col === lnk.col     && hovered.key === lnk.from) return true;
    if (hovered.col === lnk.col + 1 && hovered.key === lnk.to)   return true;
    return false;
  };

  const hoveredRisks = hovered
    ? riskData.filter(d => d[_SK_COLS[hovered.col].key] === hovered.key)
    : [];

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", display: "block" }}>
        {/* Column headers */}
        {_SK_COLS.map((col, ci) => (
          <text key={ci} x={colX[ci]} y={22}
                textAnchor={ci === 0 ? "start" : ci === 3 ? "end" : "middle"}
                fontSize="10" fontFamily="Geist Mono, monospace"
                fontWeight="600" fill="var(--ink-2)">{col.label}</text>
        ))}

        {/* Links */}
        {renderedLinks.map(lnk => (
          <path
            key={lnk.id} d={lnk.d} fill={lnk.color}
            opacity={isActive(lnk) ? 0.42 : 0.07}
            style={{ transition: "opacity 0.12s" }}
          />
        ))}

        {/* Nodes */}
        {layoutNodes.map((nodes, ci) => nodes.map(node => {
          const isHov = hovered?.col === ci && hovered?.key === node.key;
          const isDim = !!hovered && !isHov;
          const goRight = ci < 3;
          const lx = goRight ? colX[ci] + nodeW / 2 + 4 : colX[ci] - nodeW / 2 - 4;
          return (
            <g key={`nd-${ci}-${node.key}`}
               onMouseEnter={() => setHovered({ col: ci, key: node.key })}
               onMouseLeave={() => setHovered(null)}
               style={{ cursor: "pointer" }}>
              <rect
                x={colX[ci] - nodeW / 2} y={node.y}
                width={nodeW} height={Math.max(node.h, 2)}
                fill={node.color} rx={3}
                opacity={isDim ? 0.25 : 1}
                style={{ transition: "opacity 0.12s" }}
              />
              {node.h >= 12 && (
                <text
                  x={lx} y={node.y + node.h / 2 + 3.5}
                  textAnchor={goRight ? "start" : "end"}
                  fontSize="9" fontFamily="Geist Mono, monospace"
                  fill={isDim ? "var(--ink-3)" : "var(--ink-2)"}
                  pointerEvents="none"
                  style={{ transition: "fill 0.12s" }}
                >
                  {_SK_COLS[ci].abbr(node.key)}
                </text>
              )}
            </g>
          );
        }))}
      </svg>

      {/* Hover tooltip */}
      {hovered && hoveredRisks.length > 0 && (
        <div style={{
          marginTop: 8, background: "var(--surface-2)",
          border: "1px solid var(--line)", borderRadius: 8,
          padding: "10px 12px", fontSize: 11,
        }}>
          <div style={{ fontWeight: 600, color: "var(--ink)", marginBottom: 6 }}>
            {_SK_COLS[hovered.col].label}: {hovered.key}
            <span className="mono" style={{ fontWeight: 400, color: "var(--ink-3)", marginLeft: 8 }}>
              {hoveredRisks.length} risk{hoveredRisks.length !== 1 ? "s" : ""}
            </span>
          </div>
          {hoveredRisks.map(d => (
            <div key={d.r.id} style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "3px 0", borderTop: "1px solid var(--line)",
            }}>
              <span className={`rag-dot ${d.r.rag}`}/>
              <span style={{ color: "var(--ink-2)", flex: 1 }}>{d.r.name}</span>
              <span className="mono" style={{ color: "var(--ink-3)", fontSize: 10 }}>
                {(d.r.score || 0).toFixed(1)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { Heatmap, ForecastChart, MScoreGauge, RiskFlowSankey });
