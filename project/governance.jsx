/* ============================================================
   Governance Intelligence
   • GovernancePane  — bottom navigation slideout (bar + nav strip)
   • GovernanceView  — main-pane content (all tabs live here)
   ============================================================ */

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
  const paras = text.split(/\n{2,}/).filter(p => p.trim().length > 40).slice(0, 6);
  return (
    <div className="gov-prose">
      {paras.map((p, i) => <p key={i}>{p.trim()}</p>)}
    </div>
  );
}

// ── Peer table ───────────────────────────────────────────────────────────────
function PeerTable({ peers, sic, sic_description, ticker }) {
  if (!peers?.length) return <div className="gov-empty">No peer data — run in MCP mode to fetch SIC peers.</div>;
  return (
    <div>
      <div className="gov-meta-row">
        <span className="gov-meta-label">SIC</span>
        <span className="gov-meta-val mono">{sic}</span>
        <span className="gov-meta-label" style={{marginLeft: 16}}>Industry</span>
        <span className="gov-meta-val">{sic_description || "—"}</span>
        <span className="gov-meta-label" style={{marginLeft: 16}}>{peers.length} peers identified</span>
      </div>
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
          <span className="gov-bar-label">Governance Intelligence</span>
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
function GovernanceView({ data, peerData, ticker, loading, activeTab, onTabChange, govFetchError }) {
  const [filingIdx, setFilingIdx] = useState(0);

  const proxy    = data?.proxy_filings || [];
  const filing   = proxy[filingIdx];
  const sections = filing?.sections || {};

  return (
    <div className="gov-view">
      {/* Header */}
      <div className="panel-head">
        <div>
          <div className="kicker">Proxy Data · SEC EDGAR DEF 14A</div>
          <div className="panel-title mt-8">Governance Intelligence</div>
          {data
            ? <div className="panel-sub">
                {data.company_name || ticker} · {proxy.length} proxy filing{proxy.length !== 1 ? "s" : ""}
                {peerData ? ` · ${peerData.peers?.length || 0} SIC peers` : ""}
              </div>
            : <div className="panel-sub">
                Board composition, exec compensation, shareholder proposals &amp; peer benchmarks from SEC EDGAR.
              </div>
          }
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
          <div className="gov-splash-title">Governance Intelligence</div>
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
                {peerData && <GovInfoCard title="Industry Peers" value={`${peerData.peers?.length || 0} (SIC ${peerData.sic})`}/>}
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
              <PeerTable
                peers={peerData?.peers}
                sic={peerData?.sic}
                sic_description={peerData?.sic_description}
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
