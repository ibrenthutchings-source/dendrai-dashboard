/* ============================================================
   Risk Coverage Cube — COSO ERM 2017 component (X) x objective
   category (Y) grid, showing how much of the risk universe is
   actually covered and by what. Standalone nav screen, scoped to
   the ticker's latest risk-loop run (risk_coverage_cube.py) —
   spans the whole loop plus RaC/CaC/PaC, so it doesn't belong
   inside a single Assess Risk stage canvas.

   Each cell is one of three states, never collapsed to a binary
   green/red:
     empty              — no risk in the current run falls here
     mapped_unverified  — a risk is here, but no linked control has
                           real, tested/observed assurance evidence
     verified           — a risk is here AND at least one linked
                           control has proven, not just asserted,
                           evidence (last_test_passed or fired
                           recently)
   ============================================================ */

const _CUBE_STATE_META = {
  empty:              { label: "No coverage",       fg: "var(--ink-4)" },
  mapped_unverified:  { label: "Mapped, unverified", fg: "var(--amber-ink)", bg: "var(--amber-soft)", border: "var(--amber)" },
  verified:           { label: "Verified",           fg: "var(--green-ink)", bg: "var(--green-soft, var(--acc-soft))", border: "var(--green-ink)" },
};

const _RAG_META = {
  R: { label: "Red",   color: "var(--red-ink)" },
  A: { label: "Amber", color: "var(--amber-ink)" },
  G: { label: "Green", color: "var(--green-ink)" },
};

function CubeLegend() {
  return (
    <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
      {Object.entries(_CUBE_STATE_META).map(([state, meta]) => (
        <div key={state} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{
            width: 12, height: 12, borderRadius: 3, display: "inline-block",
            background: meta.bg || "var(--surface-3)",
            border: `1px solid ${meta.border || "var(--line-2)"}`,
          }} />
          <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>{meta.label}</span>
        </div>
      ))}
    </div>
  );
}

function CubeCell({ cell, onSelect, selected }) {
  const meta = _CUBE_STATE_META[cell.state] || _CUBE_STATE_META.empty;
  const empty = cell.state === "empty";
  return (
    <button
      type="button"
      onClick={() => !empty && onSelect(cell)}
      disabled={empty}
      title={empty ? "No risk in this cell" : `${cell.risk_count} risk(s) — click for detail`}
      style={{
        width: "100%", minHeight: 64, padding: "8px 10px",
        display: "flex", flexDirection: "column", justifyContent: "space-between",
        borderRadius: 6, textAlign: "left", cursor: empty ? "default" : "pointer",
        background: empty ? "var(--surface-2)" : (meta.bg || "var(--surface-2)"),
        border: selected ? "2px solid var(--acc)" : `1px solid ${empty ? "var(--line)" : (meta.border || "var(--line)")}`,
      }}
    >
      {empty ? (
        <span className="mono" style={{ fontSize: 10, color: "var(--ink-4)" }}>—</span>
      ) : (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <span className="mono" style={{ fontSize: 16, fontWeight: 600, color: "var(--ink)" }}>
              {cell.risk_count}
            </span>
            {cell.worst_rag && (
              <span className="mono" style={{
                fontSize: 9, fontWeight: 600, color: _RAG_META[cell.worst_rag]?.color || "var(--ink-3)",
              }}>
                {cell.worst_rag}
              </span>
            )}
          </div>
          <span className="mono" style={{ fontSize: 9.5, color: meta.fg }}>{meta.label}</span>
        </>
      )}
    </button>
  );
}

function CubeCellDetail({ cell, onClose }) {
  if (!cell) return null;
  const mix = cell.control_env_mix || {};
  return (
    <div style={{
      border: "1px solid var(--line)", borderRadius: 8, padding: 14, marginTop: 12,
      background: "var(--surface-2)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <div className="kicker">{cell.objective_category} · {cell.coso_component}</div>
          <div style={{ fontSize: 13, fontWeight: 600, marginTop: 2 }}>
            {cell.risk_count} risk{cell.risk_count === 1 ? "" : "s"} — {(_CUBE_STATE_META[cell.state] || {}).label}
          </div>
        </div>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onClose}>✕</button>
      </div>

      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", marginTop: 10 }}>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>Max score</div>
          <div className="mono" style={{ fontSize: 13 }}>{cell.max_score ?? "—"}</div>
        </div>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>Velocity</div>
          <div className="mono" style={{ fontSize: 13 }}>{cell.velocity_label || "—"}</div>
        </div>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>Mapped controls</div>
          <div className="mono" style={{ fontSize: 13 }}>
            {cell.verified_control_count}/{cell.mapped_control_count} verified
          </div>
        </div>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>
            Inferred control strength (loop, unverified)
          </div>
          <div className="mono" style={{ fontSize: 11 }}>
            weak {mix.WEAK ?? 0} · adequate {mix.ADEQUATE ?? 0} · strong {mix.STRONG ?? 0}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 10 }}>
        <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)", marginBottom: 4 }}>Risks</div>
        <div className="mono" style={{ fontSize: 11, display: "flex", flexWrap: "wrap", gap: 6 }}>
          {(cell.risk_refs || []).map(ref => (
            <span key={ref} style={{
              padding: "2px 6px", borderRadius: 4, border: "1px solid var(--line)", background: "var(--surface)",
            }}>{ref}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

function SegmentStrip({ segments }) {
  if (!segments || segments.length === 0) {
    return (
      <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
        Consolidated — no geography / business-segment breakdown on file for this entity.
      </div>
    );
  }
  const byType = { geography: [], business_segment: [] };
  for (const s of segments) (byType[s.segment_type] || (byType[s.segment_type] = [])).push(s);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {Object.entries(byType).filter(([, rows]) => rows.length > 0).map(([type, rows]) => (
        <div key={type}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)", marginBottom: 4 }}>
            {type === "geography" ? "Geography" : "Business segment"} — revenue mix ({rows[0]?.source || "filed"})
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {rows.map(r => (
              <span key={r.segment_name} className="mono" style={{
                fontSize: 10.5, padding: "3px 8px", borderRadius: 5,
                border: "1px solid var(--line)", background: "var(--surface-2)",
              }}>
                {r.segment_name} · {r.revenue_pct != null ? `${r.revenue_pct}%` : "—"}
              </span>
            ))}
          </div>
        </div>
      ))}
      <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", fontStyle: "italic" }}>
        Not yet joined to individual risks — shown for entity context only. The grid above is consolidated-only.
      </div>
    </div>
  );
}

function RiskCoverageCubeScreen({ ticker }) {
  const [state, setState] = useState({ loading: false, error: null, data: null });
  const [selectedKey, setSelectedKey] = useState(null);

  useEffect(() => {
    if (!ticker || typeof window === "undefined" || !window.MCP?.getCoverageCube) return;
    let cancelled = false;
    setState({ loading: true, error: null, data: null });
    window.MCP.getCoverageCube(ticker)
      .then(data => { if (!cancelled) setState({ loading: false, error: null, data }); })
      .catch(e => { if (!cancelled) setState({ loading: false, error: e.message || "Request failed", data: null }); });
    return () => { cancelled = true; };
  }, [ticker]);

  const data = state.data;
  const cellsByKey = {};
  (data?.cells || []).forEach(c => { cellsByKey[`${c.objective_category}::${c.coso_component}`] = c; });
  const selected = selectedKey ? cellsByKey[selectedKey] : null;

  return (
    <div className="panel active" data-screen-label="Risk Coverage Cube">
      <div className="panel-head">
        <div>
          <div className="kicker">Risk Intelligence</div>
          <div className="panel-title mt-8">Risk Coverage Cube</div>
          <div className="panel-sub">
            Where risk assessment (RaC), control assurance (CaC/PaC), and policy enforcement actually meet — and
            where nothing is watching. COSO ERM 2017 component x objective category, for {ticker || "—"}'s latest run.
          </div>
        </div>
      </div>

      {!ticker ? (
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          Set a Company / Ticker in Mission Control first.
        </div>
      ) : state.loading ? (
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>Loading…</div>
      ) : state.error ? (
        <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)" }}>⚠ {state.error}</div>
      ) : !data || data.run_id == null ? (
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          No risk-loop run found for {ticker} yet — run Assess Risk first.
        </div>
      ) : (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 10 }}>
            <CubeLegend />
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
              {data.total_risks} risks assessed
              {data.unmapped_risk_count > 0 ? ` · ${data.unmapped_risk_count} in an unmapped category` : ""}
            </div>
          </div>

          <div style={{ overflowX: "auto" }}>
            <div style={{
              display: "grid",
              gridTemplateColumns: `140px repeat(${data.coso_components.length}, minmax(110px, 1fr))`,
              gap: 6, minWidth: 780,
            }}>
              <div />
              {data.coso_components.map(c => (
                <div key={c} className="mono" style={{
                  fontSize: 9.5, color: "var(--ink-3)", textAlign: "center", padding: "0 2px",
                  display: "flex", alignItems: "flex-end", justifyContent: "center", textWrap: "balance",
                }}>{c}</div>
              ))}

              {data.objective_categories.map(row => (
                <React.Fragment key={row}>
                  <div className="mono" style={{
                    fontSize: 10.5, color: "var(--ink-2, var(--ink))", fontWeight: 600,
                    display: "flex", alignItems: "center",
                  }}>{row}</div>
                  {data.coso_components.map(col => {
                    const key = `${row}::${col}`;
                    const cell = cellsByKey[key] || { objective_category: row, coso_component: col, state: "empty", risk_count: 0 };
                    return (
                      <CubeCell key={key} cell={cell} selected={selectedKey === key}
                        onSelect={c => setSelectedKey(prev => (prev === key ? null : key))} />
                    );
                  })}
                </React.Fragment>
              ))}
            </div>
          </div>

          <CubeCellDetail cell={selected} onClose={() => setSelectedKey(null)} />

          <div style={{ marginTop: 20, paddingTop: 14, borderTop: "1px solid var(--line)" }}>
            <div className="kicker" style={{ marginBottom: 8 }}>Operating unit context</div>
            <SegmentStrip segments={data.segments} />
          </div>
        </>
      )}
    </div>
  );
}

Object.assign(window, { RiskCoverageCubeScreen });
