/* ============================================================
   Scenarios panel — Bear / Base / Bull stress test
   ============================================================ */

function ScenariosPanel({ scenarios, greySwan, reverseStress, historicalAnalogs, governanceScenario }) {
  if (!scenarios) return <Empty>Run the loop to populate scenarios. Once the base ensemble is anchored, Bear / Bull cases auto-generate from FRED correlates.</Empty>;
  return (
    <div data-screen-label="Scenarios">
      <div className="panel-head">
        <div>
          <div className="kicker">Scenario stress test</div>
          <div className="panel-title mt-8">Bear · Base · Bull · Grey Swan · Reverse Stress · Historical Analogs · AI Governance</div>
          <div className="panel-sub">Three explicitly-modelled futures with quantified macro shifts, risk re-scoring, and audit-objective re-prioritization. Grey Swan tracks a single foreseeable cascade where a low-band risk escalates through one quarter; the sections below add a backward-solved breakpoint analysis, real-world macro replays, and a cross-cutting AI-agent governance cascade.</div>
        </div>
      </div>

      <div className="scen-grid">
        {scenarios.map(sc => (
          <div key={sc.id} className={`scen-card ${sc.id}`}>
            <div className="head">
              <span className="tag">{sc.id.toUpperCase()}</span>
              <span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>
                {sc.probability} probability
              </span>
            </div>
            <div className="scen-name">{sc.name}</div>
            <div className="scen-desc">{sc.description}</div>

            <div className="scen-metrics">
              <ScenM l="Revenue Δ"     v={`${sc.revenue_impact_pct > 0 ? "+" : ""}${sc.revenue_impact_pct}%`}/>
              <ScenM l="Margin Δ"      v={`${sc.gross_margin_impact_bps > 0 ? "+" : ""}${sc.gross_margin_impact_bps} bps`}/>
              <ScenM l="Revenue at risk" v={sc.revenue_at_risk_m == null ? "—" : sc.revenue_at_risk_m >= 0 ? `$${sc.revenue_at_risk_m}M` : `−$${Math.abs(sc.revenue_at_risk_m)}M`}/>
              <ScenM l="Runway"        v={`${sc.runway_days}d`}/>
            </div>

            <div className="scen-section">
              <div className="lbl">Scenario assumptions</div>
              <div className="scen-pills">
                {Object.entries(sc.assumptions || {}).map(([label, val]) => (
                  <span key={label} className="scen-pill">{label} {String(val)}</span>
                ))}
              </div>
            </div>

            <div className="scen-section">
              <div className="lbl">KRIs crossing RED</div>
              <div className="scen-pills">
                {sc.kris_red.length === 0
                  ? <span className="scen-pill">None</span>
                  : sc.kris_red.map(k => <span key={k} className="scen-pill red">{k}</span>)}
              </div>
            </div>

            <div className="scen-section">
              <div className="lbl">Top audit focus</div>
              <ul className="scen-list">
                {sc.audit_focus.map((a, i) => <li key={i}>{a}</li>)}
              </ul>
            </div>

            <div className="scen-section">
              <div className="lbl">Liquidity / recovery</div>
              <div style={{display:"flex", gap: 6, marginTop: 4}}>
                <Pill tone={sc.liquidity === "SUFFICIENT" ? "ok" : "warn"}>{sc.liquidity}</Pill>
                <span className="scen-pill">{sc.recovery.replace(/_/g, " ").toLowerCase()}</span>
              </div>
            </div>

            <div className="scen-section">
              <div className="lbl">Peer positioning</div>
              <div style={{fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.5}}>{sc.vs_peers}</div>
            </div>
          </div>
        ))}
      </div>

      {greySwan && <GreySwanCard data={greySwan}/>}
      {governanceScenario && <GreySwanCard data={governanceScenario}/>}
      {reverseStress && <ReverseStressCard data={reverseStress}/>}
      {historicalAnalogs && historicalAnalogs.length > 0 && <HistoricalAnalogsGrid data={historicalAnalogs}/>}
    </div>
  );
}

// ---------- REVERSE STRESS TEST — backward-solved breakpoint ----------
function ReverseStressCard({ data }) {
  return (
    <div className="scen-card reverse-stress" style={{marginTop: 14}}>
      <div className="head">
        <span className="tag" style={{background: "var(--surface-2)", color: "var(--ink-2)"}}>REVERSE STRESS TEST</span>
        <span className="mono" style={{fontSize: 10, color: data.already_breached ? "var(--red-ink)" : "var(--ink-3)"}}>
          {data.already_breached ? "ALREADY BREACHED" : `${data.headroom_pts}pts headroom`}
        </span>
      </div>
      <div className="scen-name">{data.breakpoint.label}</div>
      <div className="scen-desc">{data.breakpoint.definition}</div>

      <div className="scen-metrics">
        <ScenM l="Current FCF margin" v={`${data.current_fcf_margin_pct}%`}/>
        <ScenM l="Headroom" v={`${data.headroom_pts}pts`}/>
        <ScenM l="Rev decline to breach" v={`${data.required_shock.revenue_decline_pts}pt`}/>
        <ScenM l="Margin compression to breach" v={`${data.required_shock.margin_compression_bps}bps`}/>
      </div>

      <div className="scen-section">
        <div className="lbl">Backward-solved narrative</div>
        <div style={{fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.5}}>{data.narrative}</div>
      </div>

      <div className="scen-section">
        <div className="lbl">Contributing risks · {data.primary_vector} vector</div>
        <div className="scen-pills">
          {data.contributing_risks.map(r => (
            <span key={r.id} className={`scen-pill${r.rag === "R" ? " red" : ""}`}>{r.id} {r.name} · {r.share}%</span>
          ))}
        </div>
      </div>

      <div className="scen-section">
        <div className="lbl">Monitoring KRIs</div>
        <ul className="scen-list">
          {data.monitoring_kris.map((k, i) => <li key={i}>{k}</li>)}
        </ul>
      </div>

      <div className="scen-section">
        <div className="lbl">Audit focus</div>
        <ul className="scen-list">
          {data.audit_focus.map((a, i) => <li key={i}>{a}</li>)}
        </ul>
      </div>
    </div>
  );
}

// ---------- HISTORICAL ANALOGS — real macro shocks replayed against current ratios ----------
function HistoricalAnalogsGrid({ data }) {
  return (
    <div style={{marginTop: 14}}>
      <div className="kicker">Historical analog replay</div>
      <div className="panel-sub" style={{marginBottom: 10}}>Real macro shocks replayed against current ratios using this industry's FRED correlation sensitivities — not synthetic assumptions.</div>
      <div className="scen-grid">
        {data.map(h => (
          <div key={h.id} className="scen-card analog">
            <div className="head">
              <span className="tag" style={{background: "var(--surface-2)", color: "var(--ink-2)"}}>{h.period}</span>
              <span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>{h.probability}</span>
            </div>
            <div className="scen-name">{h.name}</div>
            <div className="scen-desc">{h.parallel}</div>

            <div className="scen-metrics">
              <ScenM l="Revenue Δ" v={`${h.revenue_impact_pct}%`}/>
              <ScenM l="Revenue at risk" v={`$${h.revenue_at_risk_m}M`}/>
            </div>

            <div className="scen-section">
              <div className="lbl">Realised macro deltas</div>
              <div className="scen-pills">
                {Object.entries(h.realized_deltas).map(([k, v]) => (
                  <span key={k} className="scen-pill">{k.replace(/_/g, " ")} {v > 0 ? "+" : ""}{v}</span>
                ))}
              </div>
            </div>

            <div style={{fontSize: 10.5, color: "var(--ink-3)", marginTop: 8}}>{h.sensitivity_basis}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- GREY SWAN / AI GOVERNANCE — 90-day cascade timeline ----------
function GreySwanCard({ data }) {
  const ragColor = { R: "var(--red)",       A: "var(--amber)",       G: "var(--green)" };
  const ragInk   = { R: "var(--red-ink)",   A: "var(--amber-ink)",   G: "var(--green-ink)" };
  const ragSoft  = { R: "var(--red-soft)",  A: "var(--amber-soft)",  G: "var(--green-soft)" };

  const ts = data.timeline;
  const maxScore = 25; // risk scores are on a 0–25 scale
  const maxLik = Math.max(...ts.map(t => t.likelihood)) * 1.05;
  const maxImp = Math.max(...ts.map(t => t.impact_$m)) * 1.05 || 1;

  // SVG track
  const W = 900, H = 130, padL = 40, padR = 24, padT = 18, padB = 32;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const xAt = (i) => padL + (plotW) * (i / (ts.length - 1));
  const yScore = (s) => padT + plotH - (s / maxScore) * plotH;

  return (
    <div className="grey-swan">
      <div className="gs-head">
        <div className="gs-head-l">
          <div className="gs-tag">
            <span className="gs-dot" style={{background: ragColor[data.starting_rag]}}/>
            <span className="gs-arrow">→</span>
            <span className="gs-dot" style={{background: ragColor[data.ending_rag]}}/>
            <span className="mono" style={{fontSize: 10, color: "var(--ink-3)", marginLeft: 8, letterSpacing: ".05em"}}>{data.kind || "GREY SWAN"}</span>
          </div>
          <div className="gs-name">{data.name}</div>
          <div className="gs-sub">
            <span style={{color: "var(--ink-2)"}}>{data.risk_id} · {data.risk_name}</span>
            <span className="muted"> · </span>
            <span className="mono" style={{color: "var(--ink-3)"}}>{data.probability}</span>
          </div>
          <div className="gs-headline">{data.headline}</div>
          <div className="gs-desc">{data.description}</div>
        </div>
        <div className="gs-head-r">
          <div className="gs-arc">
            <div className="gs-arc-side">
              <span className="rag-chip" style={{background: ragSoft[data.starting_rag], color: ragInk[data.starting_rag]}}>
                {data.starting_rag === 'R' ? 'HIGH' : data.starting_rag === 'A' ? 'MEDIUM' : 'LOW'}
              </span>
              <div className="gs-arc-val mono">{data.starting_score.toFixed(1)}</div>
              <div className="gs-arc-lab">T0 · today</div>
            </div>
            <Icon name="chev-r" size={20} className="muted"/>
            <div className="gs-arc-side">
              <span className="rag-chip" style={{background: ragSoft[data.ending_rag], color: ragInk[data.ending_rag]}}>
                {data.ending_rag === 'R' ? 'HIGH' : data.ending_rag === 'A' ? 'MEDIUM' : 'LOW'}
              </span>
              <div className="gs-arc-val mono" style={{color: ragInk[data.ending_rag]}}>{data.ending_score.toFixed(1)}</div>
              <div className="gs-arc-lab">T+90 · projected</div>
            </div>
          </div>
          {data.peak_impact_m > 0 && (
            <div style={{marginTop: 12, textAlign: "center", padding: "8px 12px", background: "var(--red-soft)", borderRadius: 6}}>
              <div style={{fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: ".06em", marginBottom: 2}}>Revenue at risk · T+90</div>
              <div className="mono" style={{fontSize: 18, fontWeight: 600, color: "var(--red-ink)"}}>~${data.peak_impact_m}M</div>
              <div style={{fontSize: 10, color: "var(--ink-3)"}}>≈{Math.abs(data.revenue_impact_pct)}% of annual revenue</div>
            </div>
          )}
        </div>
      </div>

      {/* Score-over-time track */}
      <div className="gs-track-wrap">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{width: "100%", height: 130, display: "block"}}>
          <defs>
            <linearGradient id="gs-band" x1="0" y1="1" x2="0" y2="0">
              <stop offset="0%"    stopColor="var(--green)" stopOpacity="0.12"/>
              <stop offset="50%"   stopColor="var(--amber)" stopOpacity="0.10"/>
              <stop offset="100%"  stopColor="var(--red)"   stopOpacity="0.14"/>
            </linearGradient>
          </defs>
          <rect x={padL} y={padT} width={plotW} height={plotH} fill="url(#gs-band)" rx="4"/>
          {/* RAG bands — thresholds match ragOf(): RED ≥ 15, AMBER ≥ 9 */}
          <line x1={padL} y1={yScore(15)} x2={W - padR} y2={yScore(15)} stroke="var(--red)" strokeWidth="0.6" strokeDasharray="3 3" opacity="0.55"/>
          <line x1={padL} y1={yScore(9)}  x2={W - padR} y2={yScore(9)}  stroke="var(--amber)" strokeWidth="0.6" strokeDasharray="3 3" opacity="0.55"/>
          {/* Y axis labels */}
          <text x={padL - 6} y={yScore(15) + 3} textAnchor="end" fontSize="9" fill="var(--red-ink)" fontFamily="Geist Mono, monospace">15</text>
          <text x={padL - 6} y={yScore(9)  + 3} textAnchor="end" fontSize="9" fill="var(--amber-ink)" fontFamily="Geist Mono, monospace">9</text>
          <text x={padL - 6} y={yScore(0)  + 3} textAnchor="end" fontSize="9" fill="var(--ink-3)" fontFamily="Geist Mono, monospace">0</text>
          {/* Score line */}
          <polyline
            points={ts.map((t, i) => `${xAt(i)},${yScore(t.score)}`).join(" ")}
            fill="none" stroke="var(--ink)" strokeWidth="1.6" strokeLinejoin="round"/>
          {/* Step markers */}
          {ts.map((t, i) => (
            <g key={i}>
              <circle cx={xAt(i)} cy={yScore(t.score)} r="6" fill={ragColor[t.rag]} stroke="var(--surface)" strokeWidth="2"/>
              <text x={xAt(i)} y={yScore(t.score) - 11} textAnchor="middle" fontSize="10" fill={ragInk[t.rag]}
                    fontFamily="Geist Mono, monospace" fontWeight="500">{t.score.toFixed(1)}</text>
              <text x={xAt(i)} y={H - 12} textAnchor="middle" fontSize="10" fill="var(--ink-3)"
                    fontFamily="Geist Mono, monospace">{t.label}</text>
            </g>
          ))}
        </svg>
      </div>

      {/* Timeline detail row */}
      <div className="gs-timeline">
        {ts.map((t, i) => (
          <div key={i} className="gs-step" style={{borderTopColor: ragColor[t.rag]}}>
            <div className="gs-step-head">
              <div>
                <span className="gs-step-t mono">{t.t}</span>
                <span className="gs-step-lbl"> · {t.label}</span>
              </div>
              <span className="rag-chip" style={{background: ragSoft[t.rag], color: ragInk[t.rag]}}>{t.rag === "R" ? "HIGH" : t.rag === "A" ? "MED" : "LOW"}</span>
            </div>
            <div className="gs-step-metrics">
              <div className="scen-m">
                <div className="l">Score</div>
                <div className="v" style={{color: ragInk[t.rag]}}>{t.score.toFixed(1)}</div>
              </div>
              <div className="scen-m">
                <div className="l">Likelihood</div>
                <div className="v">{(t.likelihood * 100).toFixed(0)}%</div>
              </div>
              <div className="scen-m" style={{gridColumn: "span 2"}}>
                <div className="l">Impact</div>
                <div className="v">{t.impact}{t.impact_$m > 0 ? <span className="muted mono" style={{fontWeight: 400, marginLeft: 6, fontSize: 11}}>· ${t.impact_$m}M</span> : null}</div>
              </div>
            </div>
            <div className="gs-step-sig">
              {t.signals.map((s, j) => <div key={j} className="gs-sig-row"><span className="mono">·</span><span>{s}</span></div>)}
            </div>
            <div className="gs-step-action">
              <div className="gs-step-action-lbl">Audit response</div>
              <div className="gs-step-action-v">{t.action}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Side-by-side: early warnings + mitigations */}
      <div className="gs-foot">
        <div className="gs-foot-col">
          <div className="lbl"><Icon name="alert" size={11}/> Early-warning signals — what to monitor between T0 and T30</div>
          <ul className="scen-list">
            {data.early_warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
        <div className="gs-foot-col">
          <div className="lbl"><Icon name="check" size={11}/> Pre-staged mitigations — trigger-based, not calendar-based</div>
          <ul className="scen-list">
            {data.mitigations.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </div>
        <div className="gs-foot-col">
          <div className="lbl"><Icon name="doc" size={11}/> Catalysts &amp; impacts at T+90</div>
          <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.5}}>
            <div>
              <div className="gs-foot-sublbl">Catalysts (joint)</div>
              <ul className="scen-list" style={{fontSize: 11}}>
                {data.catalysts.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            </div>
            <div>
              <div className="gs-foot-sublbl">Impacts at peak</div>
              <ul className="scen-list" style={{fontSize: 11}}>
                {data.impacts_at_max.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ScenM({ l, v }) {
  return (
    <div className="scen-m">
      <div className="l">{l}</div>
      <div className="v">{v}</div>
    </div>
  );
}

window.ScenariosPanel = ScenariosPanel;
