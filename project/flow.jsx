/* ============================================================
   Risk Flow panel — main canvas
   Sankey: Risk → Impact Area → Audit / MAP
   + velocity-driven 90-day oversight cadence below
   + selected-risk detail card with controls + MAPs
   ============================================================ */

function FlowPanel({ risks, maps, flowMeta, selectedId, setSelectedId, liveMode, rssSignals, fredData, appetiteThreshold = 7.0 }) {
  const [hoverId, setHoverId] = React.useState(null);
  const activeId = hoverId || selectedId;

  if (!risks?.length || !flowMeta) {
    return <Empty>Risk Flow populates after the loop completes. The chart maps each key risk to the business areas it impacts, the controls protecting against it, and the audits / MAPs addressing it.</Empty>;
  }

  const topRisks = [...risks].sort((a, b) => b.score - a.score).slice(0, 6);
  const active = activeId ? risks.find(r => r.id === activeId) : null;
  const activeMeta = activeId ? flowMeta[activeId] : null;
  const activeMaps = activeId ? (maps || []).filter(m => m.linked_risk === activeId) : [];

  return (
    <div data-screen-label="Risk Flow">
      <div className="panel-head">
        <div>
          <div className="kicker">Audit closed-loop traceability</div>
          <div className="panel-title mt-8">Risk → Impact → Audit Flow</div>
          <div className="panel-sub">Each key risk fans out to the business areas it impacts and to the audit work addressing it. Hover or click a risk to highlight its branches; details below show controls, MAPs, and the velocity-driven oversight cadence for the next 90 days.</div>
        </div>
      </div>

      {/* Chart */}
      <div className="flow-chart-card">
        <RiskFlowSankey
          risks={risks}
          maps={maps}
          flowMeta={flowMeta}
          selectedId={selectedId}
          onSelect={setSelectedId}
          hoverId={hoverId}
          onHover={setHoverId}
          rssSignals={rssSignals}
          fredData={fredData}
          appetiteThreshold={appetiteThreshold}
        />
        <div className="flow-legend">
          <div className="flow-legend-grp">
            <span className="flow-leg-lbl">Risk RAG</span>
            <span className="lg"><span className="rag-dot R"/> High</span>
            <span className="lg"><span className="rag-dot A"/> Medium</span>
            <span className="lg"><span className="rag-dot G"/> Low</span>
          </div>
          <div className="flow-legend-grp">
            <span className="flow-leg-lbl">Audit · MAP</span>
            <span className="lg"><span className="rag-dot" style={{background: "var(--amber)"}}/> MAP in flight</span>
            <span className="lg"><span className="rag-dot" style={{background: "var(--acc)"}}/> On plan</span>
            <span className="lg"><span className="rag-dot G"/> Closed</span>
          </div>
        </div>
      </div>

      {/* Velocity-driven cadence strip (Gantt) */}
      <div className="flow-cadence">
        <div className="sec-lbl flow-cadence-lbl">
          90-day audit oversight cadence
          <span className="muted" style={{marginLeft: 8, fontWeight: 400, textTransform: "none", letterSpacing: 0}}>
            checkpoint density scales with each risk's velocity
          </span>
        </div>
        <CadenceStrip risks={topRisks} flowMeta={flowMeta} activeId={activeId} onSelect={setSelectedId} onHover={setHoverId}/>
      </div>

      {/* Selected-risk detail */}
      <div className="flow-detail">
        {active && activeMeta ? (
          <RiskDetail risk={active} meta={activeMeta} maps={activeMaps}/>
        ) : (
          <FlowEmptyDetail topRisks={topRisks} onSelect={setSelectedId}/>
        )}
      </div>
    </div>
  );
}

// ---------- Velocity-driven cadence Gantt ----------
// Plots each risk as a horizontal row over a 0–90d axis. Cadence
// checkpoints come from the risk's flow metadata; the more
// velocity the risk has, the tighter the spacing of checkpoints.
function CadenceStrip({ risks, flowMeta, activeId, onSelect, onHover }) {
  const W = 980, rowH = 28, padL = 220, padR = 24, padT = 30, padB = 18;
  const H = padT + risks.length * rowH + padB;
  const plotW = W - padL - padR;
  const ragColor = { R: "var(--red)", A: "var(--amber)", G: "var(--green)" };

  const ticks = [0, 30, 60, 90];

  function parseT(s) {
    const m = /T\+(\d+)d/.exec(s);
    return m ? parseInt(m[1], 10) : 0;
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width: "100%", display: "block"}}
      xmlns="http://www.w3.org/2000/svg"
      onMouseLeave={() => onHover && onHover(null)}>
      {/* X axis ticks */}
      {ticks.map(t => {
        const x = padL + (t / 90) * plotW;
        return (
          <g key={t}>
            <line x1={x} y1={padT - 6} x2={x} y2={H - padB + 4} stroke="var(--line)" strokeWidth="0.5" strokeDasharray={t === 0 ? "" : "2 4"}/>
            <text x={x} y={padT - 12} textAnchor="middle" fontSize="10" fontFamily="Geist Mono, monospace" fill="var(--ink-3)">
              {t === 0 ? "T0" : `T+${t}d`}
            </text>
          </g>
        );
      })}
      {/* Rows */}
      {risks.map((r, i) => {
        const yMid = padT + i * rowH + rowH / 2;
        const meta = flowMeta[r.id];
        const isActive = activeId === r.id;
        const dimmed = activeId && !isActive;
        const cadence = (meta?.cadence || []).map(parseT);
        const vel = r.velocity || 0;
        // Velocity multiplier — higher velocity = more checkpoints
        const intensity = Math.max(0.2, Math.min(1, (vel + 1) / 4));
        return (
          <g key={r.id} style={{cursor: "pointer"}}
            onClick={() => onSelect && onSelect(r.id === activeId ? null : r.id)}
            onMouseEnter={() => onHover && onHover(r.id)}>
            {/* Row hover backdrop */}
            <rect x={0} y={padT + i * rowH} width={W} height={rowH - 2} rx={4}
              fill={isActive ? "var(--acc-soft)" : "transparent"}
              opacity={dimmed ? 0.3 : 1}/>
            {/* Label */}
            <text x={padL - 14} y={yMid - 2} textAnchor="end"
              fontSize="11.5" fontWeight={isActive ? 600 : 500}
              fill={dimmed ? "var(--ink-4)" : "var(--ink)"}>
              {truncate(r.name, 28)}
            </text>
            <text x={padL - 14} y={yMid + 10} textAnchor="end"
              fontSize="9.5" fontFamily="Geist Mono, monospace"
              fill={dimmed ? "var(--ink-4)" : "var(--ink-3)"}>
              {r.id} · v{vel >= 0 ? "+" : ""}{vel}
            </text>
            {/* Track */}
            <line x1={padL} y1={yMid} x2={padL + plotW} y2={yMid}
              stroke="var(--line)" strokeWidth="1" strokeLinecap="round"
              opacity={dimmed ? 0.3 : 1}/>
            {/* Cadence checkpoints */}
            {cadence.map((d, j) => {
              const x = padL + (d / 90) * plotW;
              return (
                <g key={j}>
                  <circle cx={x} cy={yMid} r={5 + intensity * 2}
                    fill={ragColor[r.rag]}
                    opacity={dimmed ? 0.3 : 0.92}/>
                  <circle cx={x} cy={yMid} r={5 + intensity * 2}
                    fill="none" stroke={ragColor[r.rag]} strokeWidth="1"
                    opacity={dimmed ? 0.3 : 0.5}/>
                </g>
              );
            })}
            {/* Velocity arrow band — width scales by velocity */}
            {vel > 0 && !dimmed && (
              <rect x={padL} y={yMid - 1} width={plotW * (vel / 4)}
                height={2} fill={ragColor[r.rag]} opacity="0.25" rx={1}/>
            )}
            {/* Title for tooltip */}
            <title>{r.name} · {meta?.cadence?.length || 0} checkpoint{meta?.cadence?.length === 1 ? "" : "s"} · velocity {vel}</title>
          </g>
        );
      })}
    </svg>
  );
}

// ---------- Selected risk detail ----------
function RiskDetail({ risk, meta, maps }) {
  const ragColor = { R: "var(--red)", A: "var(--amber)", G: "var(--green)" };
  const ragInk   = { R: "var(--red-ink)", A: "var(--amber-ink)", G: "var(--green-ink)" };
  const ragSoft  = { R: "var(--red-soft)", A: "var(--amber-soft)", G: "var(--green-soft)" };

  return (
    <div className="risk-detail">
      <div className="risk-detail-head">
        <div className="risk-detail-l">
          <div className="risk-detail-title-row">
            <span className="rag-chip" style={{background: ragSoft[risk.rag], color: ragInk[risk.rag]}}>{risk.rag === "R" ? "HIGH" : risk.rag === "A" ? "MED" : "LOW"}</span>
            <div className="risk-detail-id mono">{risk.id}</div>
            <div className="risk-detail-name">{risk.name}</div>
          </div>
          <div className="risk-detail-narrative">{meta.summary}</div>
        </div>
        <div className="risk-detail-r">
          <div className="risk-detail-metric">
            <div className="l">Score</div>
            <div className="v" style={{color: ragInk[risk.rag]}}>{(risk.score ?? 0).toFixed(1)}</div>
          </div>
          <div className="risk-detail-metric">
            <div className="l">Velocity</div>
            <div className="v">{risk.velocity >= 0 ? "+" : ""}{risk.velocity}</div>
          </div>
          <div className="risk-detail-metric">
            <div className="l">Control</div>
            <div className="v mono">{risk.ce}</div>
          </div>
        </div>
      </div>

      <div className="risk-detail-grid">
        <div className="risk-detail-col">
          <div className="risk-detail-lbl">Impacts these business areas <span className="muted mono" style={{marginLeft: 6}}>{meta.impacts.length}</span></div>
          <div className="risk-detail-chips">
            {(meta.impacts || []).map(im => <span key={im} className="risk-chip">{im}</span>)}
          </div>
        </div>

        <div className="risk-detail-col">
          <div className="risk-detail-lbl">Controls in place <span className="muted mono" style={{marginLeft: 6}}>{(meta.controls || []).length}</span></div>
          {(meta.controls || []).map((c, i) => (
            <div key={i} className="risk-ctrl-row">
              <span className="risk-ctrl-name">{c.name}</span>
              <span className={`risk-ctrl-pill ce-${c.ce}`}>{c.ce}</span>
            </div>
          ))}
        </div>

        <div className="risk-detail-col">
          <div className="risk-detail-lbl">Audit / MAP coverage <span className="muted mono" style={{marginLeft: 6}}>{(meta.audits || []).length}</span></div>
          {(meta.audits || []).map((a, i) => {
            const linked = maps.find(m => m.id === a || a.includes(m.id));
            if (linked) {
              const p = linked.completion_pct || 0;
              return (
                <div key={i} className="risk-aud-row map">
                  <div className="t">
                    <span className="mono" style={{color: "var(--ink-3)", marginRight: 6}}>{linked.id}</span>
                    {linked.finding}
                  </div>
                  <div className="m">
                    <span className="muted">Owner: {linked.owner} · Due {linked.due_date}</span>
                    <span className="mono" style={{color: p === 100 ? "var(--green-ink)" : "var(--amber-ink)"}}>{p}%</span>
                  </div>
                  <div className="pbar"><div className={p < 60 ? "amber" : ""} style={{width: `${p}%`}}/></div>
                </div>
              );
            }
            return (
              <div key={i} className="risk-aud-row plan">
                <div className="t">
                  <span className="risk-aud-tag mono">PLAN</span>
                  {a}
                </div>
                <div className="m muted">Scheduled · no MAP yet</div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ---------- Empty detail (no selection) ----------
function FlowEmptyDetail({ topRisks, onSelect }) {
  return (
    <div className="risk-detail risk-detail-empty">
      <div className="risk-detail-empty-msg">
        <Icon name="alert" size={14} className="muted"/>
        <span>Hover or click any risk in the chart above to see its impact fan-out, controls, and audit coverage. Selecting a risk also highlights its checkpoint cadence in the strip.</span>
      </div>
      <div className="risk-detail-grid risk-detail-quickpicks">
        {topRisks.slice(0, 6).map(r => {
          const ragSoft = { R: "var(--red-soft)", A: "var(--amber-soft)", G: "var(--green-soft)" };
          const ragInk = { R: "var(--red-ink)", A: "var(--amber-ink)", G: "var(--green-ink)" };
          return (
            <button key={r.id} className="risk-quickpick"
              onClick={() => onSelect && onSelect(r.id)}>
              <span className="rag-chip" style={{background: ragSoft[r.rag], color: ragInk[r.rag]}}>{r.rag}</span>
              <span className="mono" style={{color: "var(--ink-3)", fontSize: 10.5}}>{r.id}</span>
              <span className="risk-quickpick-name">{r.name}</span>
              <span className="muted mono" style={{fontSize: 10.5}}>{(r.score ?? 0).toFixed(1)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

window.FlowPanel = FlowPanel;
