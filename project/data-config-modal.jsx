/* ============================================================
   Data Connection Configuration Modal
   FRED API key · EDGAR peer tickers · FRED series selection
   ============================================================ */

function DataConfigModal({ open, onClose, dataConfig, setDataConfig, cfg, onFetchNow }) {
  const [apiKey, setApiKey]           = useState(dataConfig.fredApiKey || '');
  const [keyStatus, setKeyStatus]     = useState(null); // null|'testing'|'ok'|'error'
  const [peers, setPeers]             = useState(dataConfig.tickers || []);
  const [selectedSeries, setSeries]   = useState(
    dataConfig.fredSeriesIds?.length
      ? dataConfig.fredSeriesIds
      : ['IPG3344S', 'CAPUTLG3311A2S', 'INDPRO', 'FEDFUNDS', 'T10Y2Y', 'T10Y2Y', 'TOTALSA']
  );

  // Re-sync when dataConfig changes from outside (e.g., localStorage load)
  useEffect(() => {
    if (!open) return;
    setApiKey(dataConfig.fredApiKey || '');
    setPeers(dataConfig.tickers || []);
    setSeries(dataConfig.fredSeriesIds?.length
      ? dataConfig.fredSeriesIds
      : ['IPG3344S', 'CAPUTLG3311A2S', 'INDPRO', 'FEDFUNDS', 'T10Y2Y', 'TOTALSA']);
    setKeyStatus(null);
  }, [open]);

  async function testKey() {
    if (!apiKey) return;
    setKeyStatus('testing');
    try {
      const result = await LIVE.validateFredKey(apiKey);
      setKeyStatus(result.ok ? 'ok' : 'error');
    } catch {
      setKeyStatus('error');
    }
  }

  function buildConfig() {
    return { fredApiKey: apiKey.trim(), tickers: peers, fredSeriesIds: selectedSeries };
  }

  function save() {
    const config = buildConfig();
    setDataConfig(config);
    try { localStorage.setItem('dendrai_data_config', JSON.stringify(config)); } catch {}
    onClose();
  }

  async function saveAndFetch() {
    const config = buildConfig();
    setDataConfig(config);
    try { localStorage.setItem('dendrai_data_config', JSON.stringify(config)); } catch {}
    onClose();
    onFetchNow?.(config);
  }

  function togglePeer(t) {
    setPeers(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t]);
  }
  function toggleSeries(id) {
    setSeries(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  }

  const allTickers = Object.keys(LIVE.TICKER_CIK).filter(t => t !== (cfg?.ticker || ''));

  // Group FRED series by category
  const byCategory = {};
  for (const s of LIVE.FRED_SERIES_OPTIONS) {
    (byCategory[s.cat] = byCategory[s.cat] || []).push(s);
  }

  return (
    <Modal open={open} onClose={onClose} boxClassName="dc-modal"
      title="Data Connection"
      titleSub="Configure EDGAR tickers and FRED macro series for live forecasting"
      foot={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <div style={{display:'flex', gap: 8}}>
          <button className="btn" onClick={save}>Save</button>
          <button className="btn btn-primary" onClick={saveAndFetch}>
            <Icon name="wifi" size={11}/> Save &amp; Fetch Now
          </button>
        </div>
      </>}>
        <div className="dc-body">

          {/* ── FRED API Key ─────────────────────────── */}
          <div className="dc-section">
            <div className="dc-section-title">FRED API Key</div>
            <div className="dc-section-sub">
              Free key from <span className="mono" style={{fontSize:10.5}}>fred.stlouisfed.org/api/key</span> · stored in browser localStorage
            </div>
            <div className="dc-key-row">
              <input
                className="input dc-key-input"
                type="password"
                placeholder="Paste FRED API key (32 hex chars)…"
                value={apiKey}
                onChange={e => { setApiKey(e.target.value); setKeyStatus(null); }}
                onKeyDown={e => e.key === 'Enter' && testKey()}
              />
              <button
                className="btn btn-sm"
                onClick={testKey}
                disabled={!apiKey.trim() || keyStatus === 'testing'}
                style={{flexShrink: 0}}
              >
                {keyStatus === 'testing' ? <span className="spin"/> : 'Test'}
              </button>
            </div>
            {keyStatus === 'ok'    && <div className="dc-key-ok">Key valid · FRED connection OK</div>}
            {keyStatus === 'error' && <div className="dc-key-err">Invalid key or network error · check and retry</div>}
            {!apiKey.trim() && (
              <div className="dc-hint">
                Leave blank to use bundled FRED snapshot (Q1 2021–Q1 2026).
                Live API gives current data up to this week.
              </div>
            )}
          </div>

          {/* ── EDGAR Peer Tickers ───────────────────── */}
          <div className="dc-section">
            <div className="dc-section-title">EDGAR Peer Tickers</div>
            <div className="dc-section-sub">
              Target: <b style={{fontWeight:500}}>{cfg?.ticker || '—'}</b> (always fetched) · select peers for benchmarking
            </div>
            <div className="dc-ticker-grid">
              {allTickers.map(t => (
                <label key={t} className={'dc-ticker' + (peers.includes(t) ? ' on' : '')}>
                  <input
                    type="checkbox"
                    style={{display:'none'}}
                    checked={peers.includes(t)}
                    onChange={() => togglePeer(t)}
                  />
                  {t}
                </label>
              ))}
            </div>
            {peers.length > 3 && (
              <div className="dc-hint">Fetching many peers slows initial load (~300 ms each). 2–3 peers recommended.</div>
            )}
          </div>

          {/* ── FRED Series ──────────────────────────── */}
          <div className="dc-section">
            <div className="dc-section-title">FRED Macro Series</div>
            <div className="dc-section-sub">
              Selected series are aligned to EDGAR quarterly dates and used as leading-indicator features in Random Forest and Ensemble models.
            </div>
            {Object.entries(byCategory).map(([cat, series]) => (
              <div key={cat} className="dc-cat">
                <div className="dc-cat-label">{cat}</div>
                <div className="dc-series-grid">
                  {series.map(s => (
                    <label key={s.id} className={'dc-series' + (selectedSeries.includes(s.id) ? ' on' : '')}>
                      <input
                        type="checkbox"
                        style={{display:'none'}}
                        checked={selectedSeries.includes(s.id)}
                        onChange={() => toggleSeries(s.id)}
                      />
                      <span className="dc-series-name">{s.name}</span>
                      <span className="dc-series-id mono">{s.id}</span>
                    </label>
                  ))}
                </div>
              </div>
            ))}
            <div className="dc-hint">
              {selectedSeries.length} series selected · cross-correlation computed at lags 1–4 quarters
            </div>
          </div>

        </div>
    </Modal>
  );
}

window.DataConfigModal = DataConfigModal;
