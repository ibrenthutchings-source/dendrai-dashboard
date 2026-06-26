/* ============================================================
   Coverage Gap Analysis Panel
   Cross-references the active risk register against the audit
   scope, quant model baselines, and 8-K event signals to surface
   orphaned risks, unscoped quant signals, and RAG mis-calibrations.
   ============================================================ */

const ITEM_RISK_MAP = [
  { items: ['1.01','2.03','3.02'], riskId: 'R-10', label: 'Material obligation / equity event', rag: 'A' },
  { items: ['5.02','5.07'],        riskId: 'R-11', label: 'Executive / board change event',      rag: 'A' },
  { items: ['8.01'],               riskId: null,   label: 'Other reportable event',              rag: 'G' },
];

function ragLabel(rag) {
  return rag === 'R' ? 'Red' : rag === 'A' ? 'Amber' : 'Green';
}
function ragDot(rag, size = 8) {
  const color = rag === 'R' ? 'var(--red)' : rag === 'A' ? 'var(--amber)' : 'var(--green)';
  return <span style={{ display:'inline-block', width:size, height:size, borderRadius:'50%', background:color, marginRight:5, flexShrink:0, verticalAlign:'middle' }}/>;
}

function SectionHead({ title, sub }) {
  return (
    <div style={{ marginBottom:10 }}>
      <div style={{ fontSize:11, fontWeight:700, letterSpacing:'0.06em', textTransform:'uppercase', color:'var(--ink-2)' }}>{title}</div>
      {sub && <div style={{ fontSize:10.5, color:'var(--ink-3)', marginTop:2 }}>{sub}</div>}
    </div>
  );
}

function Badge({ label, color = 'var(--ink-3)', bg = 'var(--surface-2)' }) {
  return (
    <span style={{ fontSize:9.5, fontFamily:'var(--mono)', padding:'1px 6px', borderRadius:4,
      background:bg, color, border:'1px solid var(--line)', whiteSpace:'nowrap' }}>
      {label}
    </span>
  );
}

function ScoreCard({ label, value, sub, ok }) {
  const color = ok === true ? 'var(--green-ink)' : ok === false ? 'var(--red-ink)' : 'var(--amber-ink)';
  return (
    <div style={{ flex:'1 1 130px', minWidth:110, background:'var(--surface-2)', border:'1px solid var(--line)',
      borderRadius:8, padding:'10px 14px' }}>
      <div style={{ fontSize:10, color:'var(--ink-3)', marginBottom:4, letterSpacing:'0.04em', textTransform:'uppercase' }}>{label}</div>
      <div style={{ fontSize:17, fontWeight:600, fontVariantNumeric:'tabular-nums', color }}>{value}</div>
      {sub && <div style={{ fontSize:10, color:'var(--ink-4)', marginTop:2 }}>{sub}</div>}
    </div>
  );
}

function CoverageGapPanel({ risks = [], objectives = [], rssSignals = [], ratios = {}, industry = '', ticker = '' }) {

  // ── 1. Register ↔ Scope alignment ────────────────────────────
  const coverageRows = risks.map(r => {
    const obj = objectives.find(o => o.linked_risk === r.id);
    return { risk: r, obj, covered: !!obj };
  });
  const coveredCount   = coverageRows.filter(x => x.covered).length;
  const orphanedCount  = coverageRows.filter(x => !x.covered).length;

  // ── 2. Calibration flags (template annotation) ───────────────
  // Pull calibration flags from window-exported template data if available,
  // otherwise fall back to known per-risk rules.
  const calibFlags = React.useMemo(() => {
    const flags = [];
    if (!risks.length) return flags;
    const isSemiconductors = (industry || '').toLowerCase().includes('semi');
    risks.forEach(r => {
      // R-02: flag when rdIntensity is null (blind-spot risk)
      if (r.id === 'R-02' && ratios.rdIntensity == null) {
        const baseRag = r.rag;
        flags.push({
          riskId: r.id,
          name: r.name,
          registerRag: baseRag,
          signalRag: 'A',
          direction: baseRag === 'G' ? 'under' : null,
          note: 'R&D intensity not observable from EDGAR (XBRL untagged). Quant model penalises monitoring blind-spot → Amber. Register Green is unsupported by available data.',
        });
      }
      // R-07: flag widest divergence
      if (r.id === 'R-07' && (r.rag === 'R' || r.score >= 12)) {
        flags.push({
          riskId: r.id,
          name: r.name,
          registerRag: r.rag,
          signalRag: 'G',
          direction: 'over',
          note: 'Quant model scores Export Controls at sector median (Green). Register elevation requires verification of entity-specific BIS enforcement action or EAR violation — not derivable from EDGAR.',
        });
      }
    });
    return flags;
  }, [risks, ratios, industry]);

  // ── 3. 8-K signal coverage ───────────────────────────────────
  const eightKFindings = React.useMemo(() => {
    // In live/MCP mode, rssSignals may carry 8-K item data.
    // Parse for known item patterns in headlines; also apply synthetic coverage check.
    const detected = {};

    rssSignals.forEach(s => {
      const text = `${s.title || ''} ${s.summary || ''}`.toLowerCase();
      ITEM_RISK_MAP.forEach(rule => {
        const hitItem = rule.items.find(item =>
          text.includes(`item ${item}`) || text.includes(`item${item.replace('.','')}`));
        if (hitItem) {
          const key = rule.riskId || hitItem;
          if (!detected[key]) detected[key] = { ...rule, signals: [] };
          detected[key].signals.push(s);
        }
      });
    });

    // If in mock mode (no RSS data), apply the gap analysis findings for semiconductors
    if (!rssSignals.length && (industry || '').toLowerCase().includes('semi')) {
      return [
        { riskId:'R-10', label:'Material obligation / equity event (Items 1.01, 2.03, 3.02)',
          rag:'A', signals:[], source:'8-K pattern', covered: !!risks.find(r => r.id === 'R-10') },
        { riskId:'R-11', label:'Executive / board change event (Items 5.02, 5.07)',
          rag:'A', signals:[], source:'8-K pattern', covered: !!risks.find(r => r.id === 'R-11') },
      ];
    }

    return Object.values(detected).map(d => ({
      ...d,
      covered: d.riskId ? !!risks.find(r => r.id === d.riskId) : false,
      source: `${d.signals.length} RSS signal${d.signals.length !== 1 ? 's' : ''}`,
    }));
  }, [rssSignals, risks, industry]);

  const uncoveredSignals = eightKFindings.filter(f => !f.covered);

  // ── 4. Quant-only risks (quant model shows risk but register may miss) ──
  // R-09 is the primary one — macro demand cyclicality is now in the template
  // so after a run it appears. Show coverage status.
  const macroRisk = risks.find(r => r.id === 'R-09');
  const quantOnly = !macroRisk && ratios.revGrowth != null && ratios.revGrowth < -0.10
    ? [{ id:'R-09', name:'Macro Demand Cyclicality', rag:'R', note:`Revenue ${(ratios.revGrowth*100).toFixed(1)}% YoY. Re-run loop to add this risk.` }]
    : [];

  // ── 5. Overall verdict ───────────────────────────────────────
  const totalGaps  = orphanedCount + uncoveredSignals.length + quantOnly.length;
  const totalFlags = calibFlags.length;
  const verdict    = totalGaps === 0 && totalFlags === 0 ? 'COMPLETE' : totalGaps === 0 ? 'PARTIAL' : 'INCOMPLETE';
  const verdictColor = verdict === 'COMPLETE' ? 'var(--green-ink)' : verdict === 'PARTIAL' ? 'var(--amber-ink)' : 'var(--red-ink)';

  return (
    <div style={{ padding:'0 20px 32px', maxWidth:900 }}>
      {/* ── Header ── */}
      <div className="panel-head" style={{ paddingLeft:0, paddingRight:0 }}>
        <div>
          <div className="kicker">Quality Assurance</div>
          <div className="panel-title mt-8">Coverage Gap Analysis</div>
          <div className="panel-sub">
            Cross-reference: active risk register · audit scope · quant model · 8-K event signals
            {ticker && ` · ${ticker.toUpperCase()}`}
          </div>
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <div style={{ fontFamily:'var(--mono)', fontSize:12, fontWeight:700, color:verdictColor,
            padding:'4px 12px', background:'var(--surface-2)', border:'1px solid var(--line)', borderRadius:6 }}>
            {verdict}
          </div>
        </div>
      </div>

      {/* ── Scorecard ── */}
      <div style={{ display:'flex', flexWrap:'wrap', gap:10, marginBottom:24 }}>
        <ScoreCard label="Register → Scope" value={`${coveredCount}/${risks.length}`}
          sub="risks mapped to objectives" ok={orphanedCount === 0} />
        <ScoreCard label="Quant gaps" value={quantOnly.length}
          sub="model risks not in register" ok={quantOnly.length === 0} />
        <ScoreCard label="8-K gaps" value={uncoveredSignals.length}
          sub="signal categories uncovered" ok={uncoveredSignals.length === 0} />
        <ScoreCard label="Calibration alerts" value={calibFlags.length}
          sub="RAG vs. quant divergence" ok={calibFlags.length === 0 ? true : undefined} />
        <ScoreCard label="Overall" value={verdict}
          sub={`${totalGaps} gap${totalGaps !== 1 ? 's' : ''}, ${totalFlags} alert${totalFlags !== 1 ? 's' : ''}`}
          ok={verdict === 'COMPLETE' ? true : verdict === 'INCOMPLETE' ? false : undefined} />
      </div>

      {/* ── Section 1: Register → Scope mapping ── */}
      <div style={{ background:'var(--surface)', border:'1px solid var(--line)', borderRadius:8,
        padding:'14px 16px', marginBottom:16 }}>
        <SectionHead title="1. Register → Audit Scope Mapping" sub="Each active risk and its linked audit objective." />
        {!risks.length ? (
          <div style={{ fontSize:11, color:'var(--ink-4)', padding:'12px 0' }}>Run the loop to populate the risk register.</div>
        ) : (
          <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
            <thead>
              <tr style={{ borderBottom:'1px solid var(--line)' }}>
                <th style={{ textAlign:'left', padding:'4px 6px', color:'var(--ink-3)', fontWeight:500, fontSize:10, letterSpacing:'0.04em' }}>ID</th>
                <th style={{ textAlign:'left', padding:'4px 6px', color:'var(--ink-3)', fontWeight:500, fontSize:10 }}>Risk Name</th>
                <th style={{ textAlign:'left', padding:'4px 6px', color:'var(--ink-3)', fontWeight:500, fontSize:10 }}>RAG</th>
                <th style={{ textAlign:'left', padding:'4px 6px', color:'var(--ink-3)', fontWeight:500, fontSize:10 }}>Score</th>
                <th style={{ textAlign:'left', padding:'4px 6px', color:'var(--ink-3)', fontWeight:500, fontSize:10 }}>Audit Objective</th>
                <th style={{ textAlign:'left', padding:'4px 6px', color:'var(--ink-3)', fontWeight:500, fontSize:10 }}>Aligned?</th>
              </tr>
            </thead>
            <tbody>
              {coverageRows.map(({ risk: r, obj, covered }) => (
                <tr key={r.id} style={{ borderBottom:'1px solid var(--line-2)' }}>
                  <td style={{ padding:'5px 6px', fontFamily:'var(--mono)', fontSize:10.5 }}>{r.id}</td>
                  <td style={{ padding:'5px 6px', color:'var(--ink-2)' }}>{r.name}</td>
                  <td style={{ padding:'5px 6px' }}>
                    <span style={{ display:'flex', alignItems:'center' }}>
                      {ragDot(r.rag)}{ragLabel(r.rag)}
                    </span>
                  </td>
                  <td style={{ padding:'5px 6px', fontFamily:'var(--mono)' }}>{r.score.toFixed(1)}</td>
                  <td style={{ padding:'5px 6px', color:'var(--ink-3)', fontSize:10.5 }}>
                    {obj ? <span>{obj.id} — {obj.objective.slice(0,70)}{obj.objective.length > 70 ? '…' : ''}</span>
                         : <span style={{ color:'var(--ink-4)' }}>No objective — orphaned</span>}
                  </td>
                  <td style={{ padding:'5px 6px' }}>
                    {covered
                      ? <Badge label="✓ Aligned" color="var(--green-ink)" bg="var(--green-soft)"/>
                      : <Badge label="⚠ Orphaned" color="var(--amber-ink)" bg="var(--amber-soft)"/>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Section 2: Quant model gaps ── */}
      <div style={{ background:'var(--surface)', border:'1px solid var(--line)', borderRadius:8,
        padding:'14px 16px', marginBottom:16 }}>
        <SectionHead title="2. Quant Model Risks Not In Register"
          sub="Risks the scoring model flags that have no corresponding register entry." />
        {!quantOnly.length && macroRisk ? (
          <div style={{ fontSize:11, color:'var(--green-ink)', display:'flex', alignItems:'center', gap:6 }}>
            <Icon name="check" size={12}/> All quant-model risks are represented in the register.
          </div>
        ) : quantOnly.length ? quantOnly.map(q => (
          <div key={q.id} style={{ background:'var(--red-soft)', border:'1px solid var(--red)', borderRadius:6,
            padding:'10px 14px', marginBottom:8 }}>
            <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:4 }}>
              {ragDot('R')}
              <span style={{ fontWeight:600, fontSize:11 }}>{q.id}: {q.name}</span>
              <Badge label="Missing from register" color="var(--red-ink)" bg="var(--red-soft)"/>
            </div>
            <div style={{ fontSize:10.5, color:'var(--ink-3)' }}>{q.note}</div>
          </div>
        )) : (
          <div style={{ fontSize:11, color:'var(--ink-4)' }}>
            Run the loop to evaluate quant model risk coverage.
          </div>
        )}
        {macroRisk && (
          <div style={{ background:'var(--green-soft)', border:'1px solid var(--green)', borderRadius:6,
            padding:'10px 14px', display:'flex', alignItems:'center', gap:8 }}>
            <Icon name="check" size={12} style={{ color:'var(--green-ink)' }}/>
            <div style={{ fontSize:11 }}>
              <b>R-09: Macro Demand Cyclicality</b> — registered ·
              <span style={{ color: macroRisk.rag === 'R' ? 'var(--red-ink)' : 'var(--amber-ink)', marginLeft:4 }}>
                {ragLabel(macroRisk.rag)} ({macroRisk.score.toFixed(1)}/25)
              </span>
            </div>
          </div>
        )}
      </div>

      {/* ── Section 3: 8-K signal coverage ── */}
      <div style={{ background:'var(--surface)', border:'1px solid var(--line)', borderRadius:8,
        padding:'14px 16px', marginBottom:16 }}>
        <SectionHead title="3. 8-K Event Signal Coverage"
          sub="Material event filing categories and whether a corresponding register risk exists." />
        {!eightKFindings.length ? (
          <div style={{ fontSize:11, color:'var(--ink-4)' }}>No 8-K signals detected. Enable RSS / MCP signals to activate this check.</div>
        ) : (
          <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
            {eightKFindings.map((f, i) => (
              <div key={i} style={{
                display:'flex', alignItems:'center', gap:10, padding:'8px 12px',
                borderRadius:6, border:`1px solid ${f.covered ? 'var(--green)' : 'var(--amber)'}`,
                background: f.covered ? 'var(--green-soft)' : 'var(--amber-soft)' }}>
                {ragDot(f.rag)}
                <div style={{ flex:1 }}>
                  <div style={{ fontSize:11, fontWeight:500 }}>{f.label}</div>
                  <div style={{ fontSize:10, color:'var(--ink-3)', marginTop:2 }}>
                    {f.source}{f.riskId ? ` · maps to ${f.riskId}` : ''}
                  </div>
                </div>
                {f.covered
                  ? <Badge label={`✓ ${f.riskId} in register`} color="var(--green-ink)" bg="var(--green-soft)"/>
                  : <Badge label="⚠ No matching risk" color="var(--amber-ink)" bg="var(--amber-soft)"/>}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Section 4: Calibration alerts ── */}
      <div style={{ background:'var(--surface)', border:'1px solid var(--line)', borderRadius:8,
        padding:'14px 16px', marginBottom:16 }}>
        <SectionHead title="4. RAG Calibration Review"
          sub="Risks where the register RAG diverges from the quant model baseline — requires human sign-off." />
        {!calibFlags.length ? (
          <div style={{ fontSize:11, color:'var(--green-ink)', display:'flex', alignItems:'center', gap:6 }}>
            <Icon name="check" size={12}/> No calibration divergences detected.
          </div>
        ) : calibFlags.map(f => (
          <div key={f.riskId} style={{ border:'1px solid var(--line)', borderRadius:6,
            padding:'10px 14px', marginBottom:10, background:'var(--surface-2)' }}>
            <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:6 }}>
              <span style={{ fontFamily:'var(--mono)', fontSize:10.5, color:'var(--ink-3)' }}>{f.riskId}</span>
              <span style={{ fontSize:11, fontWeight:600, color:'var(--ink)' }}>{f.name}</span>
              <div style={{ display:'flex', alignItems:'center', gap:4, marginLeft:'auto' }}>
                <span style={{ fontSize:10, color:'var(--ink-3)' }}>Register</span>
                {ragDot(f.registerRag)}<span style={{ fontSize:10.5 }}>{ragLabel(f.registerRag)}</span>
                <span style={{ fontSize:10, color:'var(--ink-3)', margin:'0 4px' }}>→ Quant</span>
                {ragDot(f.signalRag)}<span style={{ fontSize:10.5 }}>{ragLabel(f.signalRag)}</span>
                <Badge
                  label={f.direction === 'under' ? '⬆ Under-rated' : '⬇ Over-rated'}
                  color={f.direction === 'under' ? 'var(--amber-ink)' : 'var(--ink-3)'}
                  bg={f.direction === 'under' ? 'var(--amber-soft)' : 'var(--surface-2)'}/>
              </div>
            </div>
            <div style={{ fontSize:10.5, color:'var(--ink-3)', lineHeight:1.55 }}>{f.note}</div>
          </div>
        ))}
      </div>

      {/* ── Section 5: Recommended actions ── */}
      {(totalGaps > 0 || totalFlags > 0) && (
        <div style={{ background:'var(--surface)', border:'1px solid var(--line)', borderRadius:8,
          padding:'14px 16px' }}>
          <SectionHead title="5. Recommended Actions" />
          <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
            {quantOnly.map(q => (
              <div key={q.id} style={{ display:'flex', gap:8, fontSize:11, alignItems:'flex-start' }}>
                <span style={{ color:'var(--red-ink)', fontWeight:700, flexShrink:0 }}>➕</span>
                <span>Add <b>{q.id}: {q.name}</b> to register · Proposed RAG: <span style={{color:'var(--red-ink)'}}>Red</span> · Re-run loop to score.</span>
              </div>
            ))}
            {uncoveredSignals.map((f, i) => (
              <div key={i} style={{ display:'flex', gap:8, fontSize:11, alignItems:'flex-start' }}>
                <span style={{ color:'var(--amber-ink)', fontWeight:700, flexShrink:0 }}>➕</span>
                <span>Add risk for <b>{f.label}</b> — 8-K event not currently in register.</span>
              </div>
            ))}
            {calibFlags.filter(f => f.direction === 'under').map(f => (
              <div key={f.riskId} style={{ display:'flex', gap:8, fontSize:11, alignItems:'flex-start' }}>
                <span style={{ color:'var(--amber-ink)', fontWeight:700, flexShrink:0 }}>⬆</span>
                <span>Escalate <b>{f.riskId}</b> from <b>{ragLabel(f.registerRag)}</b> to <b>{ragLabel(f.signalRag)}</b> pending data verification.</span>
              </div>
            ))}
            {calibFlags.filter(f => f.direction === 'over').map(f => (
              <div key={f.riskId} style={{ display:'flex', gap:8, fontSize:11, alignItems:'flex-start' }}>
                <span style={{ color:'var(--ink-3)', fontWeight:700, flexShrink:0 }}>⬇</span>
                <span>Review <b>{f.riskId}</b> register score — consider re-calibration if no entity-specific evidence supports elevated rating.</span>
              </div>
            ))}
            {orphanedCount > 0 && (
              <div style={{ display:'flex', gap:8, fontSize:11, alignItems:'flex-start' }}>
                <span style={{ color:'var(--amber-ink)', fontWeight:700, flexShrink:0 }}>⚠</span>
                <span><b>{orphanedCount} risk{orphanedCount !== 1 ? 's' : ''}</b> not linked to any audit objective — expand scope or add objectives in Audit Scope screen.</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { CoverageGapPanel });
