/* ============================================================
   Governance Intelligence
   • GovernancePane  — bottom navigation slideout (bar + nav strip)
   • GovernanceView  — main-pane content (all tabs live here)
   ============================================================ */
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from 'recharts';

const GOV_TABS = [
  { id: "overview",  l: "Overview" },
  { id: "board",     l: "Board & Audit Committee" },
  { id: "comp",      l: "Exec Compensation" },
  { id: "proposals", l: "Shareholder Proposals" },
  { id: "peers",     l: "Peer Benchmarking" },
];

// ── Section text renderer ────────────────────────────────────────────────────
function ProxySection({ text }) {
  if (!text) return <div className="gov-empty">No data extracted from filing.</div>;

  const bulletPat = /^([•·▪\-\*]|\d+[\.\)])\s+/;

  // Prefer paragraph-level splits (double newline) — each paragraph is a
  // coherent unit of legal text. Sentence-splitting breaks on abbreviations.
  let chunks = text.split(/\n{2,}/).map(c => c.replace(/\n/g, " ").trim()).filter(c => c.length > 30);

  // Fall back to single-newline lines if the text has no paragraph structure
  if (chunks.length <= 1) {
    chunks = text.split(/\n/).map(c => c.trim()).filter(c => c.length > 20);
  }

  // If lines already carry explicit bullet markers, merge continuation lines
  const hasBullets = chunks.some(c => bulletPat.test(c));
  let items;
  if (hasBullets) {
    items = [];
    let cur = null;
    for (const chunk of chunks) {
      if (bulletPat.test(chunk)) {
        if (cur !== null) items.push(cur.trim());
        cur = chunk.replace(bulletPat, "");
      } else if (cur !== null) {
        cur += " " + chunk;
      } else if (chunk.length > 30) {
        items.push(chunk);
      }
    }
    if (cur !== null) items.push(cur.trim());
  } else {
    items = chunks;
  }

  return (
    <ul className="gov-bullet-list">
      {items.slice(0, 10).map((item, i) => (
        <li key={i} className="gov-bullet-item">{item}</li>
      ))}
    </ul>
  );
}

// ── Peer benchmarking time series chart ─────────────────────────────────────
const _PEER_LINE_COLORS = ['var(--violet)', '#e8a838', '#4aad52', '#e05c5c', '#5bc4c4', '#9c6ade', '#3d8bd4', '#c77dff', '#57cc99', '#f4a261'];

const _PEER_METRICS = [
  { id: "gross_margin",   label: "Gross Margin" },
  { id: "rd_intensity",   label: "R&D Intensity" },
  { id: "revenue_growth", label: "Revenue Growth" },
];

function PeerTimeSeriesChart({ peers, subjectHistory, ticker }) {
  const [metric, setMetric] = useState("gross_margin");
  const [hidden, setHidden] = useState(() => new Set());

  const series = useMemo(() => {
    const list = [];
    if (subjectHistory?.length) {
      list.push({
        key: "__subject__", name: `${ticker?.toUpperCase() || "Company"} (You)`,
        color: "var(--acc)", strokeWidth: 2.4, history: subjectHistory,
      });
    }
    (peers || []).forEach((p, i) => {
      if (p.history?.length) {
        list.push({
          key: p.ticker || `peer-${i}`, name: p.company_name || p.ticker || `Peer ${i + 1}`,
          color: _PEER_LINE_COLORS[i % _PEER_LINE_COLORS.length], strokeWidth: 1.6, history: p.history,
        });
      }
    });
    return list;
  }, [peers, subjectHistory, ticker]);

  if (!series.length) return null;

  const allPeriods = Array.from(new Set(series.flatMap(s => s.history.map(h => h.period)))).sort();
  const data = allPeriods.map(period => {
    const row = { period };
    series.forEach(s => {
      const pt = s.history.find(h => h.period === period);
      row[s.key] = pt ? pt[metric] : null;
    });
    return row;
  });

  const fmtV = v => Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—";

  function ChartTooltip({ active, payload, label }) {
    if (!active || !payload?.length) return null;
    const vals = series
      .map(s => ({ name: s.name, color: s.color, value: payload.find(p => p.dataKey === s.key)?.value }))
      .filter(v => v.value != null && !hidden.has(v.name));
    if (!vals.length) return null;
    return (
      <div style={{
        background: 'var(--bg)', border: '1px solid var(--line-strong)', borderRadius: 6,
        padding: '6px 10px', fontSize: 11, fontFamily: 'Geist Mono, monospace',
        boxShadow: '0 2px 8px rgba(0,0,0,0.12)', pointerEvents: 'none', maxHeight: 240, overflowY: 'auto',
      }}>
        <div style={{ color: 'var(--ink-3)', fontSize: 9, marginBottom: 4 }}>{label}</div>
        {vals.map(v => (
          <div key={v.name} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: v.color, flexShrink: 0 }}/>
            <span style={{ color: 'var(--ink-2)', flex: 1, fontSize: 10, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v.name}</span>
            <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{fmtV(v.value)}</span>
          </div>
        ))}
      </div>
    );
  }

  function toggle(name) {
    setHidden(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  }

  return (
    <div className="gov-peer-chart">
      <div className="gov-picker">
        {_PEER_METRICS.map(m => (
          <button key={m.id}
            className={"gov-pick-btn" + (metric === m.id ? " active" : "")}
            onClick={() => setMetric(m.id)}>
            {m.label}
          </button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" strokeOpacity={0.6} vertical={false}/>
          <XAxis dataKey="period"
            tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}
            tickLine={false} axisLine={{ stroke: 'var(--line)' }}/>
          <YAxis tickFormatter={fmtV}
            tick={{ fontSize: 9, fill: 'var(--ink-3)', fontFamily: 'Geist Mono, monospace' }}
            tickLine={false} axisLine={false} width={48}/>
          <Tooltip content={<ChartTooltip/>} cursor={{ stroke: 'var(--line-strong)', strokeWidth: 1, strokeDasharray: '2 2' }}/>
          {series.map(s => (
            <Line key={s.key} type="monotone" dataKey={s.key}
              stroke={s.color} strokeWidth={s.strokeWidth}
              dot={{ r: 2, fill: s.color, strokeWidth: 0 }}
              activeDot={{ r: 4, fill: s.color, strokeWidth: 0 }}
              hide={hidden.has(s.name)}
              connectNulls
              isAnimationActive={false}
              legendType="none"/>
          ))}
        </LineChart>
      </ResponsiveContainer>
      <div className="gov-peer-legend">
        {series.map(s => (
          <button key={s.key}
            className={"gov-peer-legend-item" + (hidden.has(s.name) ? " off" : "")}
            onClick={() => toggle(s.name)}
            title={hidden.has(s.name) ? "Click to show" : "Click to hide"}>
            <span className="gov-peer-legend-swatch" style={{background: s.color}}/>
            {s.name}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Peer table ───────────────────────────────────────────────────────────────
function PeerTable({ peers, sic, sic_description, ticker, peerSource, namedCompetitors }) {
  if (!peers?.length) return <div className="gov-empty">No peer data — run in MCP mode to fetch peer intelligence.</div>;
  const fromTenK = peerSource && peerSource.startsWith("10-K");
  return (
    <div>
      <div className="gov-meta-row">
        <span className="gov-meta-label">Source</span>
        <span className="gov-meta-val">{peerSource || "SIC peers"}</span>
        <span className="gov-meta-label" style={{marginLeft: 16}}>SIC</span>
        <span className="gov-meta-val mono">{sic}</span>
        <span className="gov-meta-label" style={{marginLeft: 16}}>Industry</span>
        <span className="gov-meta-val">{sic_description || "—"}</span>
        <span className="gov-meta-label" style={{marginLeft: 16}}>{peers.length} with data</span>
      </div>
      {fromTenK && namedCompetitors?.length > 0 && (
        <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", margin: "0 0 10px", lineHeight: 1.5}}>
          Named in {ticker?.toUpperCase()}'s 10-K: {namedCompetitors.join(" · ")}
          {namedCompetitors.length > peers.length && (
            <span> — {namedCompetitors.length - peers.length} dropped (no EDGAR financial data)</span>
          )}
        </div>
      )}
      <table className="gov-table">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Company</th>
            <th style={{width: 72, textAlign:"right"}}>Gross Margin</th>
            <th style={{width: 72, textAlign:"right"}}>R&D %</th>
            <th style={{width: 72, textAlign:"right"}}>Rev Growth</th>
          </tr>
        </thead>
        <tbody>
          {peers.map((p, i) => {
            const isSelf = p.ticker && p.ticker.toUpperCase() === ticker?.toUpperCase();
            return (
              <tr key={i} style={isSelf ? {background: "var(--acc-soft)", fontWeight: 600} : null}>
                <td className="mono">{p.ticker || "—"}{isSelf ? " ★" : ""}</td>
                <td>{p.company_name}</td>
                <td className="mono" style={{textAlign:"right"}}>{p.gross_margin != null ? `${(p.gross_margin * 100).toFixed(1)}%` : "—"}</td>
                <td className="mono" style={{textAlign:"right"}}>{p.rd_intensity  != null ? `${(p.rd_intensity  * 100).toFixed(1)}%` : "—"}</td>
                <td className="mono" style={{textAlign:"right"}}>{p.revenue_growth != null ? `${(p.revenue_growth * 100).toFixed(1)}%` : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Filing selector ──────────────────────────────────────────────────────────
function FilingPicker({ filings, selected, onSelect }) {
  if (!filings?.length) return null;
  return (
    <div className="gov-picker">
      {filings.map((f, i) => (
        <button key={i}
          className={"gov-pick-btn" + (selected === i ? " active" : "")}
          onClick={() => onSelect(i)}>
          {f.filing_date}
        </button>
      ))}
    </div>
  );
}

// ── Bottom navigation slideout ────────────────────────────────────────────────
// Shows the persistent bar + a navigation strip when open.
// Clicking a nav item calls onSelectTab(id), which app.jsx wires to switch the
// main pane to the Governance tab with the right sub-section active.
function GovernancePane({ open, onToggle, data, peerData, ticker, loading, activeTab, onSelectTab }) {
  const proxy = data?.proxy_filings || [];

  return (
    <div className={"gov-shell" + (open ? " open" : "")}>

      {/* ── Persistent bar ── */}
      <div className="gov-bar" onClick={onToggle}>
        <div className="gov-bar-left">
          <div className="gov-bar-icon">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <rect x="1" y="1" width="5" height="5" rx="1.2" fill="currentColor" opacity=".7"/>
              <rect x="8" y="1" width="5" height="5" rx="1.2" fill="currentColor" opacity=".7"/>
              <rect x="1" y="8" width="5" height="5" rx="1.2" fill="currentColor"/>
              <rect x="8" y="8" width="5" height="5" rx="1.2" fill="currentColor" opacity=".45"/>
            </svg>
          </div>
          <span className="gov-bar-label">Board Intelligence</span>
          {data && (
            <span className="gov-bar-badge">
              {data.company_name || ticker}
            </span>
          )}
          {loading && <span className="gov-bar-status">Fetching…</span>}
          {!loading && !data && <span className="gov-bar-status muted">Run in MCP mode to load</span>}
        </div>
        <div className="gov-bar-right">
          {data && (
            <span className="gov-bar-meta mono">
              {proxy.length} proxy filing{proxy.length !== 1 ? "s" : ""}
              {peerData ? ` · ${peerData.peers?.length || 0} peers` : ""}
            </span>
          )}
          <svg className={"gov-chevron" + (open ? " up" : "")}
               width="12" height="12" viewBox="0 0 12 12">
            <path d="M2 8L6 4L10 8" stroke="currentColor" strokeWidth="1.6"
                  strokeLinecap="round" strokeLinejoin="round" fill="none"/>
          </svg>
        </div>
      </div>

      {/* ── Navigation strip ── */}
      <div className="gov-pane">
        <div className="gov-nav">
          <span className="gov-nav-hint">Jump to section:</span>
          {GOV_TABS.map(t => (
            <button key={t.id}
              className={"gov-nav-item" + (activeTab === t.id ? " active" : "")}
              onClick={(e) => { e.stopPropagation(); onSelectTab(t.id); }}>
              {t.l}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Main-pane Governance view ─────────────────────────────────────────────────
// Rendered inside a .panel div in app.jsx. Contains all five sub-tab views.
function GovernanceView({ data, peerData, ticker, loading, activeTab, onTabChange, govFetchError, peerFetchError, lastRefresh, onRefresh }) {
  const RefreshBadge = window.RefreshBadge;
  const [filingIdx, setFilingIdx] = useState(0);

  const proxy    = data?.proxy_filings || [];
  const filing   = proxy[filingIdx];
  const sections = filing?.sections || {};

  return (
    <div className="gov-view">
      {/* Header */}
      <div className="panel-head">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div>
            <div className="kicker">Proxy Data · SEC EDGAR DEF 14A</div>
            <div className="panel-title mt-8">Board Intelligence</div>
            {data
              ? <div className="panel-sub">
                  {data.company_name || ticker} · {proxy.length} proxy filing{proxy.length !== 1 ? "s" : ""}
                  {peerData ? ` · ${peerData.peers?.length || 0} peers` : ""}
                </div>
              : <div className="panel-sub">
                  Board composition, exec compensation, shareholder proposals &amp; peer benchmarks from SEC EDGAR.
                </div>
            }
          </div>
          {onRefresh && <RefreshBadge lastRefresh={lastRefresh} onRefresh={onRefresh} loading={loading} />}
        </div>
      </div>

      {/* Tab bar */}
      <div className="gov-tab-bar gov-view-tab-bar">
        {GOV_TABS.map(t => (
          <button key={t.id}
            className={"gov-tab" + (activeTab === t.id ? " active" : "")}
            onClick={() => onTabChange(t.id)}>
            {t.l}
          </button>
        ))}
        {proxy.length > 1 && (
          <div style={{marginLeft: "auto"}}>
            <FilingPicker filings={proxy} selected={filingIdx} onSelect={setFilingIdx}/>
          </div>
        )}
      </div>

      {/* Splash — no data yet */}
      {!data && !loading && (
        <div className="gov-splash gov-view-splash">
          <div className="gov-splash-icon">
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
              <rect x="3"  y="3"  width="11" height="11" rx="2.5" fill="var(--acc)" opacity=".25"/>
              <rect x="18" y="3"  width="11" height="11" rx="2.5" fill="var(--acc)" opacity=".45"/>
              <rect x="3"  y="18" width="11" height="11" rx="2.5" fill="var(--acc)" opacity=".65"/>
              <rect x="18" y="18" width="11" height="11" rx="2.5" fill="var(--acc)" opacity=".2"/>
            </svg>
          </div>
          <div className="gov-splash-title">Board Intelligence</div>
          <div className="gov-splash-desc">
            {govFetchError
              ? <>MCP server unreachable — start <code style={{fontFamily:"monospace",fontSize:11}}>api_server.py</code> before running in MCP mode.
                  <br/><span style={{color:"var(--red-ink)", marginTop: 4, display:"block"}}>{govFetchError}</span></>
              : "Switch to MCP mode and run the loop to fetch proxy data (DEF 14A), board composition, exec compensation, and peer benchmarks from SEC EDGAR."
            }
          </div>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="gov-splash gov-view-splash">
          <span className="spin" style={{width:20,height:20,borderWidth:2}}/>
          <div className="gov-splash-desc" style={{marginTop: 12}}>Fetching governance data from SEC EDGAR…</div>
        </div>
      )}

      {/* Content */}
      {data && !loading && (
        <div className="gov-view-content">
          {activeTab === "overview" && (
            <div className="gov-content">
              <div className="gov-overview-grid">
                <GovInfoCard title="Company" value={data.company_name || ticker}/>
                <GovInfoCard title="Latest Proxy" value={proxy[0]?.filing_date || "—"}/>
                <GovInfoCard title="Proxy Filings" value={`${proxy.length} in range`}/>
                {peerData && <GovInfoCard title="Peers" value={`${peerData.peers?.length || 0} · ${peerData.peer_source || "SIC"}`}/>}
              </div>
              <div className="gov-section-hd">Key Governance Sections Found</div>
              <div className="gov-section-chips">
                {Object.keys(sections).map(k => (
                  <span key={k} className="gov-chip">{_sectionLabel(k)}</span>
                ))}
                {Object.keys(sections).length === 0 && (
                  <span className="gov-empty">No structured sections extracted from this filing.</span>
                )}
              </div>
              {sections.executive_compensation && (
                <>
                  <div className="gov-section-hd">Compensation Snapshot</div>
                  <ProxySection text={sections.executive_compensation}/>
                </>
              )}
            </div>
          )}

          {activeTab === "board" && (
            <div className="gov-content">
              {sections.audit_committee ? (
                <>
                  <div className="gov-section-hd">Audit Committee</div>
                  <ProxySection text={sections.audit_committee}/>
                </>
              ) : (
                <div className="gov-empty">Audit committee section not extracted from this proxy filing.</div>
              )}
            </div>
          )}

          {activeTab === "comp" && (
            <div className="gov-content">
              {sections.executive_compensation ? (
                <>
                  <div className="gov-section-hd">Compensation Discussion & Analysis (CD&A)</div>
                  <ProxySection text={sections.executive_compensation}/>
                </>
              ) : (
                <div className="gov-empty">Compensation section not extracted from this proxy filing.</div>
              )}
            </div>
          )}

          {activeTab === "proposals" && (
            <div className="gov-content">
              {sections.shareholder_proposals ? (
                <>
                  <div className="gov-section-hd">Shareholder Proposals</div>
                  <ProxySection text={sections.shareholder_proposals}/>
                </>
              ) : (
                <div className="gov-empty">No shareholder proposals extracted from this proxy filing.</div>
              )}
            </div>
          )}

          {activeTab === "peers" && (
            <div className="gov-content">
              {peerFetchError && !peerData?.peers?.length && (
                <div style={{
                  fontSize: 11, color: "var(--red-ink)", background: "var(--red-soft)",
                  padding: "8px 12px", borderRadius: 6, marginBottom: 12, lineHeight: 1.5,
                }}>
                  Peer fetch failed — proxy data loaded fine, but the peer benchmarking request errored or timed out
                  separately (10-K competitor extraction + per-peer XBRL enrichment is the slow part). This is why
                  it may look like nothing happened rather than showing a generic "no data" message.
                  <div className="mono" style={{ marginTop: 4, fontSize: 10.5 }}>{peerFetchError}</div>
                </div>
              )}
              <div className="gov-section-hd">Peer Trend — Gross Margin / R&amp;D Intensity / Revenue Growth</div>
              <PeerTimeSeriesChart
                peers={peerData?.peers}
                subjectHistory={peerData?.subject_history}
                ticker={ticker}/>
              <div className="gov-section-hd" style={{marginTop: 16}}>Latest Snapshot</div>
              <PeerTable
                peers={peerData?.peers}
                sic={peerData?.sic}
                sic_description={peerData?.sic_description}
                peerSource={peerData?.peer_source}
                namedCompetitors={peerData?.named_competitors}
                ticker={ticker}/>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function GovInfoCard({ title, value }) {
  return (
    <div className="gov-info-card">
      <div className="gov-info-label">{title}</div>
      <div className="gov-info-val">{value}</div>
    </div>
  );
}

function _sectionLabel(key) {
  return {
    executive_compensation: "Exec Compensation (CD&A)",
    audit_committee:        "Audit Committee",
    shareholder_proposals:  "Shareholder Proposals",
    vote_results:           "Vote Results",
    director_compensation:  "Director Compensation",
  }[key] || key.replace(/_/g, " ");
}

window.GovernancePane = GovernancePane;
window.GovernanceView = GovernanceView;
