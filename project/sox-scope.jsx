/* ============================================================
   SOX Scope Panel
   Risk-based SOX ICFR scoping driven by pipeline forecasts
   and Stage 2 risk scores (AS2201 / AS2315 aligned).
   ============================================================ */

// ── Utility helpers ────────────────────────────────────────────────────────────

function fmtM(v) {
  if (v == null) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e9)  return `$${(v / 1e9).toFixed(1)}B`;
  if (abs >= 1e6)  return `$${(v / 1e6).toFixed(0)}M`;
  if (abs >= 1e3)  return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function fmtPct(v) {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

const RAG_COLORS = {
  Red:   { ink: "var(--red-ink)",   soft: "var(--red-soft)"   },
  Amber: { ink: "var(--amber-ink)", soft: "var(--amber-soft)" },
  Green: { ink: "var(--green-ink)", soft: "var(--green-soft)" },
};

const DECISION_COLORS = {
  in_scope: { label: "IN SCOPE",   ink: "var(--green-ink)",  soft: "var(--green-soft)"  },
  review:   { label: "REVIEW",     ink: "var(--amber-ink)",  soft: "var(--amber-soft)"  },
  out:      { label: "OUT",        ink: "var(--ink-4)",      soft: "var(--surface-2, var(--surface))" },
};

const COV_COLORS = {
  P1:  { label: "P1",  ink: "var(--red-ink)",   soft: "var(--red-soft)"   },
  P2:  { label: "P2",  ink: "var(--amber-ink)", soft: "var(--amber-soft)" },
  Out: { label: "OUT", ink: "var(--ink-4)",      soft: "var(--surface-2, var(--surface))" },
};

function Pill({ label, ink, soft, size = 10 }) {
  return (
    <span className="mono" style={{
      fontSize: size, padding: "2px 7px", borderRadius: 999,
      background: soft, color: ink, letterSpacing: "0.05em", flexShrink: 0,
    }}>{label}</span>
  );
}

function Stat({ label, value, sub, ink }) {
  return (
    <div style={{flex: 1, minWidth: 80}}>
      <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 2}}>{label.toUpperCase()}</div>
      <div style={{fontSize: 18, fontWeight: 700, color: ink || "var(--ink)", fontFamily: "var(--mono)"}}>{value}</div>
      {sub && <div style={{fontSize: 9.5, color: "var(--ink-4)", marginTop: 1}}>{sub}</div>}
    </div>
  );
}

function SectionHead({ title, count, countColor }) {
  return (
    <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 8, display: "flex", alignItems: "center", gap: 8}}>
      {title}
      {count != null && (
        <span style={{fontSize: 9, padding: "1px 6px", borderRadius: 999, background: countColor || "var(--acc-soft)", color: "var(--acc-ink, var(--ink-2))"}}>{count}</span>
      )}
    </div>
  );
}


// ── Materiality card ──────────────────────────────────────────────────────────

function MaterialityCard({ scope }) {
  const pm  = scope.planning_materiality;
  const pem = scope.performance_materiality;
  const tri = scope.trivial_threshold;
  const rev = scope.revenue_forecast_fy;
  const pti = scope.pretax_income_estimate;

  return (
    <div style={{background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "14px 16px", marginBottom: 12}}>
      <div style={{display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10}}>
        <div style={{fontSize: 12, fontWeight: 600, color: "var(--ink)"}}>Materiality Thresholds · {scope.fiscal_year}</div>
        <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)"}}>{scope.materiality_basis}</div>
      </div>

      {/* Threshold meter */}
      <div style={{display: "flex", gap: 0, height: 6, borderRadius: 4, overflow: "hidden", marginBottom: 10}}>
        <div style={{flex: 5, background: "var(--red-soft)"}}/>
        <div style={{flex: 2, background: "var(--amber-soft)"}}/>
        <div style={{flex: 1, background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)"}}/>
      </div>

      <div style={{display: "flex", gap: 12, flexWrap: "wrap"}}>
        <div style={{flex: 1, minWidth: 100}}>
          <div className="mono" style={{fontSize: 9, color: "var(--ink-4)", letterSpacing: "0.06em"}}>PLANNING MAT.</div>
          <div style={{fontSize: 15, fontWeight: 700, color: "var(--ink)", fontFamily: "var(--mono)"}}>{fmtM(pm)}</div>
          <div style={{fontSize: 9.5, color: "var(--ink-4)"}}>AS2315 primary</div>
        </div>
        <div style={{flex: 1, minWidth: 100}}>
          <div className="mono" style={{fontSize: 9, color: "var(--ink-4)", letterSpacing: "0.06em"}}>PERFORMANCE MAT.</div>
          <div style={{fontSize: 15, fontWeight: 700, color: "var(--amber-ink)", fontFamily: "var(--mono)"}}>{fmtM(pem)}</div>
          <div style={{fontSize: 9.5, color: "var(--ink-4)"}}>tolerable misstatement</div>
        </div>
        <div style={{flex: 1, minWidth: 100}}>
          <div className="mono" style={{fontSize: 9, color: "var(--ink-4)", letterSpacing: "0.06em"}}>TRIVIAL</div>
          <div style={{fontSize: 15, fontWeight: 700, color: "var(--ink-3)", fontFamily: "var(--mono)"}}>{fmtM(tri)}</div>
          <div style={{fontSize: 9.5, color: "var(--ink-4)"}}>clearly inconsequential</div>
        </div>
        <div style={{flex: 1, minWidth: 100}}>
          <div className="mono" style={{fontSize: 9, color: "var(--ink-4)", letterSpacing: "0.06em"}}>FY REVENUE FORECAST</div>
          <div style={{fontSize: 15, fontWeight: 700, color: "var(--acc-ink, var(--ink))", fontFamily: "var(--mono)"}}>{fmtM(rev)}</div>
          <div style={{fontSize: 9.5, color: "var(--ink-4)"}}>4Q ensemble</div>
        </div>
        {pti != null && (
          <div style={{flex: 1, minWidth: 100}}>
            <div className="mono" style={{fontSize: 9, color: "var(--ink-4)", letterSpacing: "0.06em"}}>PRE-TAX INCOME EST.</div>
            <div style={{fontSize: 15, fontWeight: 700, color: pti >= 0 ? "var(--green-ink)" : "var(--red-ink)", fontFamily: "var(--mono)"}}>{fmtM(pti)}</div>
            <div style={{fontSize: 9.5, color: "var(--ink-4)"}}>basis for 5% rule</div>
          </div>
        )}
      </div>
    </div>
  );
}


// ── Accounts table ────────────────────────────────────────────────────────────

function AccountsTable({ accounts }) {
  const inScope = accounts.filter(a => a.in_scope);
  const outScope = accounts.filter(a => !a.in_scope);
  const [showOut, setShowOut] = React.useState(false);

  function AccountRow({ acc }) {
    const [open, setOpen] = React.useState(false);
    const rag = RAG_COLORS[acc.rag_linkage] || {};
    return (
      <div style={{borderBottom: "1px solid var(--line)"}}>
        <div
          onClick={() => setOpen(o => !o)}
          style={{display: "flex", alignItems: "center", gap: 8, padding: "7px 0", cursor: "pointer"}}>
          <span style={{width: 6, height: 6, borderRadius: "50%", background: rag.ink || "var(--ink-4)", flexShrink: 0}}/>
          <span style={{flex: 1, fontSize: 11.5, color: "var(--ink)", fontWeight: 500}}>{acc.account_name}</span>
          {acc.balance_estimate && (
            <span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>{fmtM(acc.balance_estimate)}</span>
          )}
          {acc.priority && <Pill label={acc.priority} ink={COV_COLORS[acc.priority]?.ink} soft={COV_COLORS[acc.priority]?.soft}/>}
          <Icon name={open ? "chev-u" : "chev-d"} size={11} className="muted"/>
        </div>
        {open && (
          <div style={{paddingLeft: 14, paddingBottom: 8, fontSize: 10.5, color: "var(--ink-3)", borderLeft: "2px solid var(--line)", marginLeft: 3}}>
            <div style={{marginBottom: 4}}>{acc.rationale}</div>
            {acc.linked_risks?.length > 0 && (
              <div style={{display: "flex", gap: 4, flexWrap: "wrap"}}>
                {acc.linked_risks.map((r, i) => (
                  <span key={i} className="mono" style={{fontSize: 9.5, padding: "1px 6px", borderRadius: 4, background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)", color: "var(--ink-3)"}}>{r}</span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "12px 16px", marginBottom: 12}}>
      <SectionHead title="SIGNIFICANT ACCOUNTS" count={`${inScope.length} in scope`} countColor="var(--green-soft)"/>
      {inScope.map((a, i) => <AccountRow key={i} acc={a}/>)}
      {outScope.length > 0 && (
        <div style={{marginTop: 8}}>
          <button className="cfg-link" style={{fontSize: 10}} onClick={() => setShowOut(s => !s)}>
            {showOut ? "▲" : "▼"} {outScope.length} accounts out of scope
          </button>
          {showOut && outScope.map((a, i) => (
            <div key={i} style={{display: "flex", alignItems: "center", gap: 8, padding: "5px 0", borderBottom: "1px solid var(--line)", opacity: 0.55}}>
              <span style={{width: 5, height: 5, borderRadius: "50%", background: "var(--ink-4)", flexShrink: 0}}/>
              <span style={{flex: 1, fontSize: 11, color: "var(--ink-3)"}}>{a.account_name}</span>
              {a.balance_estimate && <span className="mono" style={{fontSize: 10, color: "var(--ink-4)"}}>{fmtM(a.balance_estimate)}</span>}
              <span className="mono" style={{fontSize: 9, color: "var(--ink-4)"}}>OUT</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


// ── Processes table ───────────────────────────────────────────────────────────

function ProcessesTable({ processes }) {
  const p1 = processes.filter(p => p.coverage_level === "P1");
  const p2 = processes.filter(p => p.coverage_level === "P2");
  const out = processes.filter(p => p.coverage_level === "Out");
  const [showOut, setShowOut] = React.useState(false);

  function ProcRow({ proc }) {
    const [open, setOpen] = React.useState(false);
    const cov = COV_COLORS[proc.coverage_level] || {};
    return (
      <div style={{borderBottom: "1px solid var(--line)"}}>
        <div onClick={() => setOpen(o => !o)}
          style={{display: "flex", alignItems: "center", gap: 8, padding: "7px 0", cursor: "pointer"}}>
          <Pill label={cov.label || proc.coverage_level} ink={cov.ink} soft={cov.soft}/>
          <span style={{flex: 1, fontSize: 11.5, fontWeight: 500, color: "var(--ink)"}}>{proc.process_name}</span>
          {proc.always_in && <span className="mono" style={{fontSize: 9, color: "var(--acc-ink, var(--ink-3))"}}>REQUIRED</span>}
          <Icon name={open ? "chev-u" : "chev-d"} size={11} className="muted"/>
        </div>
        {open && (
          <div style={{paddingLeft: 14, paddingBottom: 8, fontSize: 10.5, color: "var(--ink-3)", borderLeft: "2px solid var(--line)", marginLeft: 3}}>
            <div style={{marginBottom: 4}}>{proc.description}</div>
            <div style={{fontSize: 10, color: "var(--ink-4)", marginBottom: 4}}>{proc.rationale}</div>
            {proc.linked_risks?.length > 0 && (
              <div style={{display: "flex", gap: 4, flexWrap: "wrap"}}>
                {proc.linked_risks.map((r, i) => (
                  <span key={i} className="mono" style={{fontSize: 9.5, padding: "1px 6px", borderRadius: 4, background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)", color: "var(--ink-3)"}}>{r}</span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "12px 16px", marginBottom: 12}}>
      <SectionHead title="SOX PROCESSES" count={`${p1.length} P1 · ${p2.length} P2`} countColor="var(--amber-soft)"/>
      {p1.map((p, i) => <ProcRow key={i} proc={p}/>)}
      {p2.map((p, i) => <ProcRow key={i} proc={p}/>)}
      {out.length > 0 && (
        <div style={{marginTop: 8}}>
          <button className="cfg-link" style={{fontSize: 10}} onClick={() => setShowOut(s => !s)}>
            {showOut ? "▲" : "▼"} {out.length} processes out of scope
          </button>
          {showOut && out.map((p, i) => (
            <div key={i} style={{display: "flex", alignItems: "center", gap: 8, padding: "5px 0", borderBottom: "1px solid var(--line)", opacity: 0.50}}>
              <Pill label="OUT" ink="var(--ink-4)" soft="var(--surface-2, var(--surface))"/>
              <span style={{flex: 1, fontSize: 11, color: "var(--ink-3)"}}>{p.process_name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


// ── Systems panel ─────────────────────────────────────────────────────────────

const SYSTEM_TYPE_LABELS = {
  erp: "ERP", consolidation: "Consolidation", reporting: "Reporting",
  treasury: "Treasury", hr_payroll: "HR / Payroll", tax: "Tax",
  sub_ledger: "Sub-ledger", crm: "CRM", billing: "Billing", epm: "EPM", custom: "Custom",
};

const SYSTEM_TYPES = Object.entries(SYSTEM_TYPE_LABELS).map(([v, l]) => ({ value: v, label: l }));

function SystemsPanel({ systems, ticker, onAdd, onRemove }) {
  const [showForm, setShowForm] = React.useState(false);
  const [form, setForm] = React.useState({ system_name: "", system_type: "erp", vendor: "", significance: "medium", linked_processes: [], notes: "" });
  const [saving, setSaving] = React.useState(false);
  const [err, setErr] = React.useState(null);

  const PROCESS_OPTIONS = [
    "order_to_cash", "procure_to_pay", "financial_close", "itgc",
    "treasury", "payroll_hr", "tax_provision", "inventory_cost",
    "fixed_assets", "equity_goodwill", "segment_reporting",
  ];

  async function handleAdd() {
    if (!form.system_name.trim()) { setErr("System name required"); return; }
    setSaving(true); setErr(null);
    try {
      const apiBase = (window.MCP_API_BASE || "http://127.0.0.1:8001");
      const res = await fetch(`${apiBase}/sox/systems/${encodeURIComponent(ticker)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      onAdd && onAdd({ ...form, id: data.system_id });
      setShowForm(false);
      setForm({ system_name: "", system_type: "erp", vendor: "", significance: "medium", linked_processes: [], notes: "" });
    } catch (e) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove(sys) {
    if (!confirm(`Remove ${sys.system_name} from SOX registry?`)) return;
    const apiBase = (window.MCP_API_BASE || "http://127.0.0.1:8001");
    await fetch(`${apiBase}/sox/systems/${encodeURIComponent(ticker)}/${sys.id}`, { method: "DELETE" });
    onRemove && onRemove(sys.id);
  }

  function toggleProc(p) {
    setForm(f => ({
      ...f,
      linked_processes: f.linked_processes.includes(p)
        ? f.linked_processes.filter(x => x !== p)
        : [...f.linked_processes, p],
    }));
  }

  const inScope  = systems.filter(s => s.decision === "in_scope");
  const review   = systems.filter(s => s.decision === "review");
  const outSys   = systems.filter(s => s.decision === "out");

  return (
    <div style={{background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "12px 16px", marginBottom: 12}}>
      <div style={{display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10}}>
        <SectionHead title="IN-SCOPE SYSTEMS" count={`${inScope.length + review.length} / ${systems.length}`} countColor="var(--acc-soft)"/>
        <button className="btn btn-sm" style={{fontSize: 10, padding: "3px 10px"}} onClick={() => setShowForm(s => !s)}>
          <Icon name="plus" size={10}/> Add system
        </button>
      </div>

      {showForm && (
        <div style={{background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)", borderRadius: 6, padding: "12px 14px", marginBottom: 12}}>
          <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 8}}>NEW SYSTEM</div>
          <div style={{display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8}}>
            <input placeholder="System name *" value={form.system_name} onChange={e => setForm(f => ({...f, system_name: e.target.value}))}
              style={{flex: 2, minWidth: 140, fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"}}/>
            <select value={form.system_type} onChange={e => setForm(f => ({...f, system_type: e.target.value}))}
              style={{flex: 1, minWidth: 110, fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"}}>
              {SYSTEM_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
            <input placeholder="Vendor (optional)" value={form.vendor} onChange={e => setForm(f => ({...f, vendor: e.target.value}))}
              style={{flex: 1, minWidth: 110, fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"}}/>
            <select value={form.significance} onChange={e => setForm(f => ({...f, significance: e.target.value}))}
              style={{minWidth: 90, fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"}}>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>
          <div style={{marginBottom: 8}}>
            <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", marginBottom: 4}}>LINKED PROCESSES (optional — auto-assigned by type if empty)</div>
            <div style={{display: "flex", gap: 4, flexWrap: "wrap"}}>
              {PROCESS_OPTIONS.map(p => (
                <button key={p} type="button"
                  onClick={() => toggleProc(p)}
                  style={{fontSize: 9.5, padding: "2px 7px", borderRadius: 4, cursor: "pointer",
                    background: form.linked_processes.includes(p) ? "var(--acc-soft)" : "var(--surface)",
                    color: form.linked_processes.includes(p) ? "var(--acc-ink, var(--ink))" : "var(--ink-4)",
                    border: `1px solid ${form.linked_processes.includes(p) ? "var(--acc-ink, var(--line))" : "var(--line)"}`,
                  }}>
                  {p.replace(/_/g, " ")}
                </button>
              ))}
            </div>
          </div>
          <input placeholder="Notes (optional)" value={form.notes} onChange={e => setForm(f => ({...f, notes: e.target.value}))}
            style={{width: "100%", fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)", marginBottom: 8, boxSizing: "border-box"}}/>
          {err && <div className="mono" style={{fontSize: 10, color: "var(--red-ink)", marginBottom: 6}}>{err}</div>}
          <div style={{display: "flex", gap: 8}}>
            <button className="btn btn-sm approve" onClick={handleAdd} disabled={saving}>{saving ? "Saving…" : "Save system"}</button>
            <button className="btn btn-sm" onClick={() => setShowForm(false)}>Cancel</button>
          </div>
        </div>
      )}

      {systems.length === 0 && !showForm && (
        <div style={{fontSize: 11, color: "var(--ink-4)", padding: "8px 0"}}>No systems registered. Click "Add system" to build the SOX system registry.</div>
      )}

      {[...inScope, ...review, ...outSys].map((sys, i) => {
        const dec = DECISION_COLORS[sys.decision] || {};
        return (
          <div key={i} style={{display: "flex", alignItems: "center", gap: 8, padding: "6px 0",
            borderBottom: "1px solid var(--line)", opacity: sys.decision === "out" ? 0.5 : 1}}>
            <Pill label={dec.label || sys.decision.toUpperCase()} ink={dec.ink} soft={dec.soft}/>
            <div style={{flex: 1, minWidth: 0}}>
              <span style={{fontSize: 11.5, fontWeight: 500, color: "var(--ink)"}}>{sys.system_name}</span>
              {sys.vendor && <span style={{fontSize: 10, color: "var(--ink-4)", marginLeft: 6}}>{sys.vendor}</span>}
              <span className="mono" style={{fontSize: 9, color: "var(--ink-4)", marginLeft: 6}}>
                {SYSTEM_TYPE_LABELS[sys.system_type] || sys.system_type}
              </span>
            </div>
            <span style={{fontSize: 9.5, color: "var(--ink-4)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{sys.rationale}</span>
            {sys.system_id && (
              <button className="btn btn-sm" style={{padding: "2px 7px", fontSize: 9, opacity: 0.7}}
                onClick={() => handleRemove(sys)}>Remove</button>
            )}
          </div>
        );
      })}
    </div>
  );
}


// ── Segment coverage ──────────────────────────────────────────────────────────

function SegmentCoverage({ segments }) {
  if (!segments?.length) return null;
  return (
    <div style={{background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "12px 16px", marginBottom: 12}}>
      <SectionHead title="GEOGRAPHY / SEGMENT COVERAGE" count={`${segments.filter(s => s.in_scope).length} in scope`} countColor="var(--green-soft)"/>
      {segments.map((seg, i) => (
        <div key={i} style={{display: "flex", alignItems: "center", gap: 8, padding: "6px 0",
          borderBottom: "1px solid var(--line)", opacity: seg.in_scope ? 1 : 0.55}}>
          <span style={{width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
            background: seg.in_scope ? "var(--green-ink)" : "var(--ink-4)"}}/>
          <span style={{flex: 1, fontSize: 11.5, fontWeight: 500, color: "var(--ink)"}}>{seg.segment_name}</span>
          <span className="mono" style={{fontSize: 9.5, color: "var(--ink-4)"}}>{seg.segment_type}</span>
          {seg.revenue && <span className="mono" style={{fontSize: 10, color: "var(--ink-2)"}}>{fmtM(seg.revenue)}</span>}
          {seg.revenue_pct != null && (
            <span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>{seg.revenue_pct.toFixed(1)}%</span>
          )}
          <Pill
            label={seg.in_scope ? "IN SCOPE" : "OUT"}
            ink={seg.in_scope ? "var(--green-ink)" : "var(--ink-4)"}
            soft={seg.in_scope ? "var(--green-soft)" : "var(--surface-2, var(--surface))"}
          />
        </div>
      ))}
    </div>
  );
}


// ── Risk linkage bar ──────────────────────────────────────────────────────────

function RiskSummaryBar({ summary }) {
  if (!summary) return null;
  return (
    <div style={{display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14}}>
      <Stat label="Red risks"   value={summary.red_risks}   ink="var(--red-ink)"/>
      <Stat label="Amber risks" value={summary.amber_risks} ink="var(--amber-ink)"/>
      <Stat label="Accts in scope" value={`${summary.accounts_in}/${summary.accounts_total}`} ink="var(--green-ink)"/>
      <Stat label="P1 processes" value={summary.processes_p1} ink="var(--red-ink)"/>
      <Stat label="P2 processes" value={summary.processes_p2} ink="var(--amber-ink)"/>
      <Stat label="Systems in"  value={`${summary.systems_in}/${summary.systems_total}`}/>
      {summary.segments_total > 0 && (
        <Stat label="Segments in" value={`${summary.segments_in}/${summary.segments_total}`}/>
      )}
    </div>
  );
}


// ── Rescoping status badge ────────────────────────────────────────────────────

function RescopingBadge({ scoped_at, trigger_reason }) {
  if (!scoped_at) return null;
  const d = new Date(scoped_at);
  const ts = isNaN(d) ? scoped_at : d.toLocaleString();
  const isAuto = (trigger_reason || "").includes("auto");
  return (
    <div style={{display: "flex", alignItems: "center", gap: 6, fontSize: 10, color: "var(--ink-4)"}}>
      <span style={{width: 6, height: 6, borderRadius: "50%",
        background: isAuto ? "var(--green-ink)" : "var(--amber-ink)", flexShrink: 0}}/>
      <span>Scoped {ts}</span>
      <span className="mono" style={{padding: "1px 6px", borderRadius: 4, background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)", fontSize: 9}}>
        {isAuto ? "AUTO" : "MANUAL"}
      </span>
    </div>
  );
}


// ── Main panel ────────────────────────────────────────────────────────────────

function SoxScopePanel({
  scope,           // pre-loaded scope object (from API or passed from app state)
  ticker,
  runId,
  forecasts,       // from pipeline state (used for on-demand scope)
  risks,           // Stage 2 risks
  ratios,          // financial ratios
  hasRun,
}) {
  const [localScope, setLocalScope]       = React.useState(scope || null);
  const [localSystems, setLocalSystems]   = React.useState(scope?.systems_in_scope || []);
  const [loading, setLoading]             = React.useState(false);
  const [error, setError]                 = React.useState(null);
  const [activeTab, setActiveTab]         = React.useState("accounts");

  const displayScope = localScope;
  const apiBase = window.MCP_API_BASE || "http://127.0.0.1:8001";

  // Load from API on mount / when runId changes
  React.useEffect(() => {
    if (!runId) return;
    (async () => {
      try {
        const r = await fetch(`${apiBase}/sox/scope/${runId}`);
        if (r.ok) {
          const data = await r.json();
          setLocalScope(data);
          setLocalSystems(data.systems_in_scope || []);
        }
      } catch (_) {}
    })();
  }, [runId]);

  async function runScoping() {
    if (!forecasts || !risks) { setError("Run the pipeline first to generate forecasts and risk scores"); return; }
    setLoading(true); setError(null);
    try {
      // Build forecast payload in backend format from frontend forecasts object
      const fcPayload = {
        forecasts: (forecasts.revenue?.forecast || []).map((q, i) => ({
          horizon: i + 1,
          point: q.base ?? q.v ?? 0,
          ci_lower: q.low  ?? 0,
          ci_upper: q.high ?? 0,
        })),
        metric: "Revenue",
      };

      const body = {
        run_id: runId || 0,
        ticker: ticker || "",
        forecast: fcPayload,
        risk_scores: { risks: risks || [] },
        ratios: ratios || {},
        fiscal_year: "",
        trigger_reason: "manual_rerun",
      };

      const res = await fetch(`${apiBase}/sox/scope`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setLocalScope(data);
      setLocalSystems(data.systems_in_scope || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  const tabs = [
    { id: "accounts",  label: "Accounts" },
    { id: "processes", label: "Processes" },
    { id: "systems",   label: "Systems" },
    ...(displayScope?.segments_coverage?.length ? [{ id: "segments", label: "Geography" }] : []),
  ];

  return (
    <div style={{display: "flex", flexDirection: "column", gap: 0, height: "100%", overflow: "auto"}}>
      {/* Header */}
      <div style={{padding: "18px 24px 0"}}>
        <div style={{display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 14}}>
          <div>
            <div className="kicker">SOX ICFR</div>
            <div style={{fontSize: 20, fontWeight: 700, color: "var(--ink)", lineHeight: 1.2}}>
              SOX Scope · {displayScope?.fiscal_year || "—"}
            </div>
            <div style={{fontSize: 12, color: "var(--ink-3)", marginTop: 4}}>
              Risk-based scoping from pipeline forecasts — AS2201 / AS2315 aligned
            </div>
          </div>
          <div style={{display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8}}>
            <button className="btn btn-sm" onClick={runScoping} disabled={loading || !hasRun}>
              <Icon name="reset" size={10}/> {loading ? "Scoping…" : displayScope ? "Rescope" : "Scope now"}
            </button>
            {displayScope && <RescopingBadge scoped_at={displayScope.scoped_at} trigger_reason={displayScope.trigger_reason}/>}
          </div>
        </div>

        {error && (
          <div className="mono" style={{fontSize: 10.5, color: "var(--red-ink)", background: "var(--red-soft)", padding: "6px 10px", borderRadius: 4, marginBottom: 10}}>{error}</div>
        )}

        {!displayScope && !loading && (
          <div style={{fontSize: 12, color: "var(--ink-3)", padding: "24px 0", textAlign: "center"}}>
            {hasRun
              ? "Scope not yet generated. Click \"Scope now\" to compute based on current pipeline outputs."
              : "Run the pipeline first — SOX scoping uses the Stage 1 forecast and Stage 2 risk scores."}
          </div>
        )}

        {displayScope && <RiskSummaryBar summary={displayScope.summary}/>}
        {displayScope && <MaterialityCard scope={displayScope}/>}
      </div>

      {displayScope && (
        <>
          {/* Tab bar */}
          <div style={{display: "flex", gap: 0, padding: "0 24px", borderBottom: "1px solid var(--line)", marginBottom: 0}}>
            {tabs.map(t => (
              <button key={t.id}
                onClick={() => setActiveTab(t.id)}
                style={{
                  padding: "8px 16px", fontSize: 11, fontWeight: activeTab === t.id ? 600 : 400,
                  background: "none", border: "none", borderBottom: activeTab === t.id ? "2px solid var(--acc-ink, var(--ink))" : "2px solid transparent",
                  color: activeTab === t.id ? "var(--ink)" : "var(--ink-4)", cursor: "pointer",
                }}>
                {t.label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div style={{padding: "14px 24px", flex: 1, overflow: "auto"}}>
            {activeTab === "accounts"  && <AccountsTable accounts={displayScope.accounts_in_scope || []}/>}
            {activeTab === "processes" && <ProcessesTable processes={displayScope.processes_in_scope || []}/>}
            {activeTab === "systems"   && (
              <SystemsPanel
                systems={localSystems}
                ticker={ticker || ""}
                onAdd={sys => setLocalSystems(prev => [...prev.filter(s => s.system_name !== sys.system_name), {
                  ...sys, decision: "review",
                  rationale: "Added — rescope to compute coverage",
                }])}
                onRemove={id => setLocalSystems(prev => prev.filter(s => s.system_id !== id && s.id !== id))}
              />
            )}
            {activeTab === "segments"  && <SegmentCoverage segments={displayScope.segments_coverage || []}/>}
          </div>
        </>
      )}
    </div>
  );
}

Object.assign(window, { SoxScopePanel });
