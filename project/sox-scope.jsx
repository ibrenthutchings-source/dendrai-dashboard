/* ============================================================
   SOX Scope Panel
   Risk-based SOX ICFR scoping driven by pipeline forecasts
   and Stage 2 risk scores (AS2201 / AS2315 aligned).
   ============================================================ */

import { hierarchy, treemap, treemapSquarify } from 'd3-hierarchy';

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


// ── Inline detail/override editor (shared by accounts + processes) ───────────

function DetailEditor({ form, setForm, scopeField, showExposure }) {
  return (
    <div onClick={e => e.stopPropagation()} style={{marginTop: 8, padding: "10px 12px", background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)", borderRadius: 6}}>
      <div style={{display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8}}>
        <input placeholder="Geography (comma-separated, e.g. US, EMEA, APAC)" value={form.geography}
          onChange={e => setForm(f => ({...f, geography: e.target.value}))}
          style={{flex: 1, minWidth: 160, fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"}}/>
        <input placeholder="Business unit (comma-separated)" value={form.segments}
          onChange={e => setForm(f => ({...f, segments: e.target.value}))}
          style={{flex: 1, minWidth: 160, fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"}}/>
        {showExposure && (
          <input placeholder="Override $ exposure (blank = auto-derived from linked accounts)" value={form.estimated_exposure}
            onChange={e => setForm(f => ({...f, estimated_exposure: e.target.value}))}
            style={{flex: 1, minWidth: 160, fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"}}/>
        )}
      </div>
      <textarea placeholder="Notes / additional detail" value={form.notes} rows={2}
        onChange={e => setForm(f => ({...f, notes: e.target.value}))}
        style={{width: "100%", fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)", marginBottom: 8, boxSizing: "border-box", resize: "vertical", fontFamily: "inherit"}}/>
      {scopeField}
    </div>
  );
}

// ── Accounts table ────────────────────────────────────────────────────────────

function AccountsTable({ accounts, ticker, onUpdate }) {
  const inScope = accounts.filter(a => a.in_scope);
  const outScope = accounts.filter(a => !a.in_scope);
  const [showOut, setShowOut] = React.useState(false);

  function AccountRow({ acc }) {
    const [open, setOpen] = React.useState(false);
    const [editing, setEditing] = React.useState(false);
    const [saving, setSaving] = React.useState(false);
    const [err, setErr] = React.useState(null);
    const [form, setForm] = React.useState(() => ({
      geography: (acc.geography || []).join(", "),
      segments: (acc.segments || []).join(", "),
      notes: acc.notes || "",
      manual_in_scope: acc.manual_override ? (acc.in_scope ? "in" : "out") : "auto",
      manual_priority: acc.priority || "",
    }));
    const rag = RAG_COLORS[acc.rag_linkage] || {};

    async function handleSave() {
      setSaving(true); setErr(null);
      const payload = {
        geography: form.geography.split(",").map(s => s.trim()).filter(Boolean),
        segments: form.segments.split(",").map(s => s.trim()).filter(Boolean),
        notes: form.notes.trim() || null,
        manual_in_scope: form.manual_in_scope === "auto" ? null : form.manual_in_scope === "in",
        manual_priority: form.manual_in_scope === "in" ? (form.manual_priority || null) : null,
      };
      try {
        const res = await fetch(`/api/mcp/sox/accounts/${encodeURIComponent(ticker)}/${acc.account_id}`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(await res.text());
        onUpdate && onUpdate(acc.account_id, {
          geography: payload.geography,
          segments: payload.segments,
          notes: payload.notes,
          manual_override: payload.manual_in_scope !== null,
          in_scope: payload.manual_in_scope !== null ? payload.manual_in_scope : acc.in_scope,
          priority: payload.manual_in_scope !== null ? (payload.manual_priority || (payload.manual_in_scope ? "P2" : null)) : acc.priority,
        });
        setEditing(false);
      } catch (e) {
        setErr(e.message);
      } finally {
        setSaving(false);
      }
    }

    return (
      <div style={{borderBottom: "1px solid var(--line)"}}>
        <Clickable
          onClick={() => setOpen(o => !o)}
          style={{display: "flex", alignItems: "center", gap: 8, padding: "7px 0", cursor: "pointer"}}>
          <span style={{width: 6, height: 6, borderRadius: "50%", background: rag.ink || "var(--ink-4)", flexShrink: 0}}/>
          <span style={{flex: 1, fontSize: 11.5, color: "var(--ink)", fontWeight: 500}}>{acc.account_name}</span>
          {acc.balance_estimate && (
            <span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}
                  title={acc.balance_source === "estimated" ? "Heuristic estimate — no filed or uploaded figure available for this account" : "Filed XBRL or uploaded figure"}>
              {fmtM(acc.balance_estimate)}{acc.balance_source === "estimated" ? "*" : ""}
            </span>
          )}
          {acc.manual_override && <Pill ink="var(--acc-ink, var(--ink-2))" soft="var(--acc-soft)" size={9}>MANUAL</Pill>}
          {acc.priority && <Pill ink={COV_COLORS[acc.priority]?.ink} soft={COV_COLORS[acc.priority]?.soft} size={10}>{acc.priority}</Pill>}
          <Icon name={open ? "chev-u" : "chev-d"} size={11} className="muted"/>
        </Clickable>
        {open && (
          <div style={{paddingLeft: 14, paddingBottom: 10, fontSize: 10.5, color: "var(--ink-3)", borderLeft: "2px solid var(--line)", marginLeft: 3}}>
            <div style={{marginBottom: 4}}>{acc.rationale}</div>
            {(acc.geography?.length > 0 || acc.segments?.length > 0) && (
              <div style={{display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 4}}>
                {acc.geography?.length > 0 && <span>🌐 {acc.geography.join(", ")}</span>}
                {acc.segments?.length > 0 && <span>▤ {acc.segments.join(", ")}</span>}
              </div>
            )}
            {acc.notes && <div style={{marginBottom: 4, fontStyle: "italic"}}>{acc.notes}</div>}
            {acc.linked_risks?.length > 0 && (
              <div style={{display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6}}>
                {acc.linked_risks.map((r, i) => (
                  <span key={i} className="mono" style={{fontSize: 9.5, padding: "1px 6px", borderRadius: 4, background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)", color: "var(--ink-3)"}}>{r}</span>
                ))}
              </div>
            )}
            {!editing ? (
              <button className="cfg-link" style={{fontSize: 10}} onClick={() => setEditing(true)}>
                <Icon name="edit" size={10}/> Edit detail
              </button>
            ) : (
              <DetailEditor form={form} setForm={setForm} scopeField={
                <div onClick={e => e.stopPropagation()}>
                  <div style={{display: "flex", gap: 8, alignItems: "center", marginBottom: 8, flexWrap: "wrap"}}>
                    <select value={form.manual_in_scope} onChange={e => setForm(f => ({...f, manual_in_scope: e.target.value}))}
                      style={{fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"}}>
                      <option value="auto">Auto (computed)</option>
                      <option value="in">Force in-scope</option>
                      <option value="out">Force out-of-scope</option>
                    </select>
                    {form.manual_in_scope === "in" && (
                      <select value={form.manual_priority} onChange={e => setForm(f => ({...f, manual_priority: e.target.value}))}
                        style={{fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"}}>
                        <option value="">Priority…</option>
                        <option value="P1">P1</option>
                        <option value="P2">P2</option>
                      </select>
                    )}
                  </div>
                  {err && <div className="mono" style={{fontSize: 10, color: "var(--red-ink)", marginBottom: 6}}>{err}</div>}
                  <div style={{display: "flex", gap: 8}}>
                    <button className="btn btn-sm btn-approve" onClick={handleSave} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
                    <button className="btn btn-sm" onClick={() => setEditing(false)}>Cancel</button>
                  </div>
                </div>
              }/>
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

function ProcessesTable({ processes, ticker, onUpdate }) {
  const p1 = processes.filter(p => p.coverage_level === "P1");
  const p2 = processes.filter(p => p.coverage_level === "P2");
  const out = processes.filter(p => p.coverage_level === "Out");
  const [showOut, setShowOut] = React.useState(false);

  function ProcRow({ proc }) {
    const [open, setOpen] = React.useState(false);
    const [editing, setEditing] = React.useState(false);
    const [saving, setSaving] = React.useState(false);
    const [err, setErr] = React.useState(null);
    const [form, setForm] = React.useState(() => ({
      geography: (proc.geography || []).join(", "),
      segments: (proc.segments || []).join(", "),
      notes: proc.notes || "",
      estimated_exposure: proc.estimated_exposure != null ? String(proc.estimated_exposure) : "",
      manual_coverage_level: proc.manual_override ? proc.coverage_level : "auto",
    }));
    const cov = COV_COLORS[proc.coverage_level] || {};

    async function handleSave() {
      setSaving(true); setErr(null);
      const expNum = form.estimated_exposure.trim() === "" ? null : Number(form.estimated_exposure);
      const payload = {
        geography: form.geography.split(",").map(s => s.trim()).filter(Boolean),
        segments: form.segments.split(",").map(s => s.trim()).filter(Boolean),
        notes: form.notes.trim() || null,
        estimated_exposure: Number.isFinite(expNum) ? expNum : null,
        manual_coverage_level: form.manual_coverage_level === "auto" ? null : form.manual_coverage_level,
      };
      try {
        const res = await fetch(`/api/mcp/sox/processes/${encodeURIComponent(ticker)}/${proc.process_id}`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(await res.text());
        onUpdate && onUpdate(proc.process_id, {
          geography: payload.geography,
          segments: payload.segments,
          notes: payload.notes,
          // A cleared override falls back to the derived value server-side,
          // which we can't recompute here (needs linked-account balances) —
          // only patch these optimistically when a manual value was entered;
          // otherwise leave the previously-shown figure until "Rescope".
          ...(payload.estimated_exposure != null
            ? { estimated_exposure: payload.estimated_exposure, estimated_exposure_source: "manual" }
            : {}),
          manual_override: payload.manual_coverage_level !== null,
          coverage_level: payload.manual_coverage_level || proc.coverage_level,
        });
        setEditing(false);
      } catch (e) {
        setErr(e.message);
      } finally {
        setSaving(false);
      }
    }

    return (
      <div style={{borderBottom: "1px solid var(--line)"}}>
        <Clickable onClick={() => setOpen(o => !o)}
          style={{display: "flex", alignItems: "center", gap: 8, padding: "7px 0", cursor: "pointer"}}>
          <Pill ink={cov.ink} soft={cov.soft} size={10}>{cov.label || proc.coverage_level}</Pill>
          <span style={{flex: 1, fontSize: 11.5, fontWeight: 500, color: "var(--ink)"}}>{proc.process_name}</span>
          {proc.estimated_exposure != null && (
            <span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}
              title={proc.estimated_exposure_source === "derived" ? "Derived from linked accounts' projected balances" : "Manually entered"}>
              {proc.estimated_exposure_source === "derived" ? "≈ " : ""}{fmtM(proc.estimated_exposure)}
            </span>
          )}
          {proc.manual_override && <Pill ink="var(--acc-ink, var(--ink-2))" soft="var(--acc-soft)" size={9}>MANUAL</Pill>}
          {proc.always_in && <span className="mono" style={{fontSize: 9, color: "var(--acc-ink, var(--ink-3))"}}>REQUIRED</span>}
          <Icon name={open ? "chev-u" : "chev-d"} size={11} className="muted"/>
        </Clickable>
        {open && (
          <div style={{paddingLeft: 14, paddingBottom: 10, fontSize: 10.5, color: "var(--ink-3)", borderLeft: "2px solid var(--line)", marginLeft: 3}}>
            <div style={{marginBottom: 4}}>{proc.description}</div>
            <div style={{fontSize: 10, color: "var(--ink-4)", marginBottom: 4}}>{proc.rationale}</div>
            {(proc.geography?.length > 0 || proc.segments?.length > 0) && (
              <div style={{display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 4}}>
                {proc.geography?.length > 0 && <span>🌐 {proc.geography.join(", ")}</span>}
                {proc.segments?.length > 0 && <span>▤ {proc.segments.join(", ")}</span>}
              </div>
            )}
            {proc.notes && <div style={{marginBottom: 4, fontStyle: "italic"}}>{proc.notes}</div>}
            {proc.linked_risks?.length > 0 && (
              <div style={{display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6}}>
                {proc.linked_risks.map((r, i) => (
                  <span key={i} className="mono" style={{fontSize: 9.5, padding: "1px 6px", borderRadius: 4, background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)", color: "var(--ink-3)"}}>{r}</span>
                ))}
              </div>
            )}
            {!editing ? (
              <button className="cfg-link" style={{fontSize: 10}} onClick={() => setEditing(true)}>
                <Icon name="edit" size={10}/> Edit detail
              </button>
            ) : (
              <DetailEditor form={form} setForm={setForm} showExposure scopeField={
                <div onClick={e => e.stopPropagation()}>
                  <div style={{marginBottom: 8}}>
                    <select value={form.manual_coverage_level} onChange={e => setForm(f => ({...f, manual_coverage_level: e.target.value}))}
                      style={{fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"}}>
                      <option value="auto">Auto (computed)</option>
                      <option value="P1">Force P1</option>
                      <option value="P2">Force P2</option>
                      <option value="Out">Force out-of-scope</option>
                    </select>
                  </div>
                  {err && <div className="mono" style={{fontSize: 10, color: "var(--red-ink)", marginBottom: 6}}>{err}</div>}
                  <div style={{display: "flex", gap: 8}}>
                    <button className="btn btn-sm btn-approve" onClick={handleSave} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
                    <button className="btn btn-sm" onClick={() => setEditing(false)}>Cancel</button>
                  </div>
                </div>
              }/>
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
              <Pill ink="var(--ink-4)" soft="var(--surface-2, var(--surface))" size={10}>OUT</Pill>
              <span style={{flex: 1, fontSize: 11, color: "var(--ink-3)"}}>{p.process_name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


// ── Business unit / geography breakdown (accounts + processes) ───────────────
// "Business unit" reuses the existing `segments` field (business_segment) —
// there's no separate business-unit dimension in the data model. Accounts
// carry a computed `balance_estimate`; processes carry a manually-entered
// `estimated_exposure` (see DetailEditor's showExposure field) since there's
// no algorithmic dollar figure for a process the way there is for an account.
// "Approvals required" = in-scope (in_scope for accounts, coverage_level !==
// "Out" for processes) — any in-scope item requires SOX control-testing
// sign-off; out-of-scope items don't. When an item carries more than one
// geography/business-unit tag, its dollar amount is split evenly across
// each tag it carries, so each breakdown's amounts sum to the group total.

function _computeBreakdown(items, valueOf, inScopeOf, tagsOf) {
  const groups = {};
  let total = 0;
  for (const item of items) {
    const val = valueOf(item);
    if (!val) continue;
    total += val;
    const inScope = inScopeOf(item);
    const tags = (tagsOf(item) || []).filter(Boolean);
    const bucket = tags.length ? tags : ["Unassigned"];
    const share = val / bucket.length;
    for (const tag of bucket) {
      if (!groups[tag]) groups[tag] = { amount: 0, inScopeAmount: 0 };
      groups[tag].amount += share;
      if (inScope) groups[tag].inScopeAmount += share;
    }
  }
  const rows = Object.entries(groups)
    .map(([tag, g]) => ({
      tag,
      amount: g.amount,
      pctOfTotal: total ? g.amount / total : 0,
      pctApprovalRequired: g.amount ? g.inScopeAmount / g.amount : 0,
    }))
    .sort((a, b) => (a.tag === "Unassigned") - (b.tag === "Unassigned") || b.amount - a.amount);
  return { rows, total };
}

// Materiality scoping is inherently spatial — a rectangle per segment sized
// by dollar amount, colored by how much of it is in-scope. Real squarified
// layout via d3-hierarchy (already a transitive dep of the d3 package this
// codebase already uses for risk-sankey.jsx), not a hand-rolled grid.
function SegmentTreemap({ title, rows, valueLabel }) {
  if (!rows.length) return null;
  const W = 420, H = 190;
  const root = hierarchy({ children: rows }).sum(d => d.amount || 0);
  treemap().tile(treemapSquarify).size([W, H]).paddingInner(2).round(true)(root);
  const leaves = root.leaves();

  function scopeColor(pct) {
    if (pct >= 0.66) return "var(--green)";
    if (pct >= 0.33) return "var(--amber)";
    return "var(--ink-4)";
  }

  return (
    <div style={{ flex: 1, minWidth: 300 }}>
      <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 8 }}>{title}</div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMinYMin meet" style={{ maxWidth: W }}>
        {leaves.map(leaf => {
          const w = leaf.x1 - leaf.x0, h = leaf.y1 - leaf.y0;
          const d = leaf.data;
          const color = scopeColor(d.pctApprovalRequired);
          const showLabel = w > 46 && h > 18;
          const showAmt = w > 46 && h > 34;
          return (
            <g key={d.tag} transform={`translate(${leaf.x0},${leaf.y0})`}>
              <rect width={w} height={h} fill={color} opacity={d.tag === "Unassigned" ? 0.35 : 0.72} rx={2} />
              <rect width={w} height={h} fill="none" stroke="var(--surface)" strokeWidth={1.5} rx={2} />
              {showLabel && (
                <text x={6} y={15} fontSize={9.5} fontWeight={700} fill="#fff"
                  style={{ paintOrder: "stroke", stroke: "rgba(0,0,0,0.45)", strokeWidth: 2.5 }}>
                  {d.tag.length > Math.floor(w / 6) ? d.tag.slice(0, Math.floor(w / 6) - 1) + "…" : d.tag}
                </text>
              )}
              {showAmt && (
                <text x={6} y={29} fontSize={8.5} fill="#fff" opacity={0.9}
                  style={{ paintOrder: "stroke", stroke: "rgba(0,0,0,0.45)", strokeWidth: 2.5 }}>
                  {fmtM(d.amount)} · {fmtPct(d.pctOfTotal)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <div style={{ display: "flex", gap: 12, marginTop: 6, fontSize: 9, color: "var(--ink-4)" }}>
        <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: "var(--green)", opacity: 0.72, marginRight: 4 }} />≥66% in scope</span>
        <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: "var(--amber)", opacity: 0.72, marginRight: 4 }} />33–66%</span>
        <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: "var(--ink-4)", opacity: 0.72, marginRight: 4 }} />&lt;33%</span>
      </div>
    </div>
  );
}

// Total → in-scope → residual against the materiality threshold, in one bar —
// the actual argument for "why is this in scope" an auditor wants to see.
function CoverageWaterfall({ total, inScopeTotal, materiality, valueLabel }) {
  if (!total) return null;
  const inPct = Math.max(0, Math.min(1, inScopeTotal / total));
  const matPct = materiality != null ? Math.max(0, Math.min(1, materiality / total)) : null;

  return (
    <div style={{ marginTop: 14 }}>
      <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 8 }}>
        COVERAGE VS. MATERIALITY ({valueLabel})
      </div>
      <div style={{ position: "relative", height: 26, borderRadius: 5, overflow: "hidden", background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)" }}>
        <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${inPct * 100}%`, background: "var(--green)", opacity: 0.75 }} />
        {matPct != null && (
          <div style={{ position: "absolute", left: `${matPct * 100}%`, top: -3, bottom: -3, width: 2, background: "var(--red-ink)" }} title={`Planning materiality: ${fmtM(materiality)}`} />
        )}
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", padding: "0 8px", fontSize: 10, fontWeight: 700, color: "var(--ink)", fontFamily: "var(--mono)" }}>
          {fmtM(inScopeTotal)} in scope · {fmtPct(inPct)} of {fmtM(total)}
        </div>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "var(--ink-4)", marginTop: 4 }}>
        <span>$0</span>
        {matPct != null && <span style={{ color: "var(--red-ink)" }}>▲ materiality {fmtM(materiality)}</span>}
        <span>{fmtM(total)}</span>
      </div>
    </div>
  );
}

function BreakdownTable({ title, rows, total }) {
  if (!rows.length) {
    return (
      <div style={{flex: 1, minWidth: 260}}>
        <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 8}}>{title}</div>
        <div style={{fontSize: 10.5, color: "var(--ink-4)", padding: "8px 0"}}>No dollar figures tagged yet.</div>
      </div>
    );
  }
  return (
    <div style={{flex: 1, minWidth: 260}}>
      <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 8}}>{title}</div>
      <div style={{overflowX: "auto"}}>
        <table style={{width: "100%", borderCollapse: "collapse", fontSize: 11}}>
          <thead>
            <tr style={{borderBottom: "2px solid var(--line)"}}>
              {["Name", "Amount", "% of Total", "% Approval Req'd"].map(h => (
                <th key={h} style={{
                  textAlign: h === "Name" ? "left" : "right",
                  padding: "4px 10px 5px 0",
                  color: "var(--ink-4)", fontWeight: 400,
                  fontFamily: "Geist Mono, monospace", fontSize: 9, whiteSpace: "nowrap",
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} style={{borderBottom: "1px solid var(--line)", opacity: r.tag === "Unassigned" ? 0.55 : 1}}>
                <td style={{padding: "7px 10px 7px 0", fontSize: 11.5, fontWeight: 500, color: "var(--ink)"}}>{r.tag}</td>
                <td style={{textAlign: "right", padding: "7px 10px 7px 0", fontFamily: "Geist Mono, monospace"}}>{fmtM(r.amount)}</td>
                <td style={{textAlign: "right", padding: "7px 10px 7px 0", fontFamily: "Geist Mono, monospace", color: "var(--ink-3)"}}>
                  {(r.pctOfTotal * 100).toFixed(1)}%
                </td>
                <td style={{textAlign: "right", padding: "7px 10px 7px 0", fontFamily: "Geist Mono, monospace", color: r.pctApprovalRequired > 0 ? "var(--amber-ink)" : "var(--ink-4)"}}>
                  {(r.pctApprovalRequired * 100).toFixed(1)}%
                </td>
              </tr>
            ))}
            <tr>
              <td style={{padding: "7px 10px 0 0", fontSize: 10, color: "var(--ink-4)"}}>Total</td>
              <td style={{textAlign: "right", padding: "7px 10px 0 0", fontFamily: "Geist Mono, monospace", fontSize: 10, color: "var(--ink-4)"}}>{fmtM(total)}</td>
              <td/><td/>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

function BreakdownPanel({ items, valueOf, inScopeOf, valueLabel, materiality }) {
  const [showTable, setShowTable] = React.useState(false);
  const byBU  = React.useMemo(() => _computeBreakdown(items, valueOf, inScopeOf, i => i.segments), [items, valueOf, inScopeOf]);
  const byGeo = React.useMemo(() => _computeBreakdown(items, valueOf, inScopeOf, i => i.geography), [items, valueOf, inScopeOf]);

  const grandTotal = items.reduce((a, i) => a + (valueOf(i) || 0), 0);
  const inScopeTotal = items.reduce((a, i) => a + (inScopeOf(i) ? (valueOf(i) || 0) : 0), 0);

  const hasData = byBU.total > 0 || byGeo.total > 0;

  return (
    <div style={{background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "12px 16px", marginBottom: 12}}>
      <div style={{display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: hasData ? 10 : 0}}>
        <div style={{fontSize: 12, fontWeight: 600, color: "var(--ink)"}}>Scope by business unit &amp; geography ({valueLabel})</div>
        {hasData && (
          <button className="cfg-link" style={{fontSize: 10, display: "flex", alignItems: "center", gap: 6}} onClick={() => setShowTable(o => !o)}>
            <Icon name={showTable ? "chev-u" : "chev-d"} size={11}/>
            {showTable ? "Hide" : "Show"} detail table
          </button>
        )}
      </div>

      {!hasData ? (
        <div style={{fontSize: 10.5, color: "var(--ink-4)"}}>
          No items have both a dollar amount and a business unit/geography tag yet — add them via "Edit detail" on each row.
        </div>
      ) : (
        <>
          <div style={{display: "flex", gap: 24, flexWrap: "wrap"}}>
            <SegmentTreemap title="BY BUSINESS UNIT" rows={byBU.rows} valueLabel={valueLabel} />
            <SegmentTreemap title="BY GEOGRAPHY" rows={byGeo.rows} valueLabel={valueLabel} />
          </div>
          <CoverageWaterfall total={grandTotal} inScopeTotal={inScopeTotal} materiality={materiality} valueLabel={valueLabel} />
          {showTable && (
            <div style={{display: "flex", gap: 24, flexWrap: "wrap", marginTop: 16, paddingTop: 12, borderTop: "1px solid var(--line)"}}>
              <BreakdownTable title="BY BUSINESS UNIT" rows={byBU.rows} total={byBU.total}/>
              <BreakdownTable title="BY GEOGRAPHY" rows={byGeo.rows} total={byGeo.total}/>
            </div>
          )}
        </>
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
  const [confirmRemove, setConfirmRemove] = React.useState(null);

  const PROCESS_OPTIONS = [
    "order_to_cash", "procure_to_pay", "financial_close", "itgc",
    "treasury", "payroll_hr", "tax_provision", "inventory_cost",
    "fixed_assets", "equity_goodwill", "segment_reporting",
  ];

  async function handleAdd() {
    if (!form.system_name.trim()) { setErr("System name required"); return; }
    setSaving(true); setErr(null);
    try {
      const res = await fetch(`/api/mcp/sox/systems/${encodeURIComponent(ticker)}`, {
        method: "POST",
        credentials: "include",
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
    await fetch(`/api/mcp/sox/systems/${encodeURIComponent(ticker)}/${sys.id}`, { method: "DELETE", credentials: "include" });
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
            <button className="btn btn-sm btn-approve" onClick={handleAdd} disabled={saving}>{saving ? "Saving…" : "Save system"}</button>
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
            <Pill ink={dec.ink} soft={dec.soft} size={10}>{dec.label || sys.decision.toUpperCase()}</Pill>
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
                onClick={() => setConfirmRemove(sys)}>Remove</button>
            )}
          </div>
        );
      })}
      <ConfirmModal
        open={!!confirmRemove}
        title="Remove system?"
        message={confirmRemove ? `Remove ${confirmRemove.system_name} from SOX registry?` : ""}
        danger confirmLabel="Remove"
        onCancel={() => setConfirmRemove(null)}
        onConfirm={() => { const sys = confirmRemove; setConfirmRemove(null); handleRemove(sys); }}
      />
    </div>
  );
}


// ── Segment data manager (DB-backed, edit + save) ─────────────────────────────

const SEGMENT_TYPE_LABELS = {
  geography: "Geography", business_segment: "Business Segment", product_line: "Product Line",
};
const SEGMENT_TYPES = Object.entries(SEGMENT_TYPE_LABELS).map(([v, l]) => ({ value: v, label: l }));

// Provenance pill for a saved segment row — mirrors the balance_source ===
// "estimated" convention already used elsewhere on this screen (Accounts
// tab). 'filed': every derived financial field was read straight off the
// filing. 'filed+estimated': revenue is filed but gross_profit/operating_
// income/net_income/assets/margins are partly or wholly allocated by
// revenue_pct (edgar_segments.persist_segments). 'manual'/anything else:
// entered by hand.
const _SEGMENT_SOURCE_META = {
  filed:            { label: "Filed",     title: "Every financial field on this row was read directly off the filer's own XBRL.", color: "var(--green-ink)" },
  "filed+estimated": { label: "Filed+est.", title: "Revenue is filed; gross profit / operating income / net income / assets weren't all broken out by the filer, so some are allocated from consolidated totals by revenue %.", color: "var(--amber-ink)" },
};
function SegmentSourceBadge({ source }) {
  const meta = _SEGMENT_SOURCE_META[source] || { label: "Manual", title: "Entered by hand.", color: "var(--ink-4)" };
  return (
    <span className="mono" title={meta.title} style={{
      fontSize: 9, padding: "1px 6px", borderRadius: 999, border: `1px solid ${meta.color}`,
      color: meta.color, flexShrink: 0,
    }}>{meta.label}</span>
  );
}

const EMPTY_SEGMENT_FORM = {
  segment_name: "", segment_type: "geography",
  revenue: "", revenue_pct: "", rev_growth_yoy_pct: "",
  gross_profit: "", operating_income: "", net_income: "", assets: "",
  gross_margin_pct: "", op_margin_pct: "", net_margin_pct: "",
};

function segFormToPayload(form) {
  const num = v => (v === "" || v == null ? null : Number(v));
  return {
    segment_name: form.segment_name.trim(),
    segment_type: form.segment_type,
    revenue: num(form.revenue),
    revenue_pct: num(form.revenue_pct),
    rev_growth_yoy_pct: num(form.rev_growth_yoy_pct),
    gross_profit: num(form.gross_profit),
    operating_income: num(form.operating_income),
    net_income: num(form.net_income),
    assets: num(form.assets),
    gross_margin_pct: num(form.gross_margin_pct),
    op_margin_pct: num(form.op_margin_pct),
    net_margin_pct: num(form.net_margin_pct),
    source: "manual",
  };
}

function segToForm(seg) {
  const str = v => (v == null ? "" : String(v));
  return {
    segment_name: seg.segment_name || "", segment_type: seg.segment_type || "geography",
    revenue: str(seg.revenue), revenue_pct: str(seg.revenue_pct), rev_growth_yoy_pct: str(seg.rev_growth_yoy_pct),
    gross_profit: str(seg.gross_profit), operating_income: str(seg.operating_income),
    net_income: str(seg.net_income), assets: str(seg.assets),
    gross_margin_pct: str(seg.gross_margin_pct), op_margin_pct: str(seg.op_margin_pct), net_margin_pct: str(seg.net_margin_pct),
  };
}

// Auto-fills gross_profit/operating_income/net_income/assets + all three
// margins from POST /sox/segments/{ticker}/estimate-financials — the
// percentage-of-consolidated allocation edgar_segments.estimate_segment_
// financials computes for a segment with no filed breakdown of its own
// (import-xbrl, on SegmentsManager's toolbar, is the filed-data path; this
// is the fallback for a segment an auditor is defining by hand). Requires
// revenue_pct — that's the % being allocated by — so the button stays
// disabled until one is entered.
function useFinancialsEstimate(ticker, form, setForm) {
  const [estimating, setEstimating] = React.useState(false);
  const [error, setError] = React.useState(null);

  async function run() {
    const pct = Number(form.revenue_pct);
    if (!ticker || !form.revenue_pct || Number.isNaN(pct)) {
      setError("Enter Revenue % of total first.");
      return;
    }
    setEstimating(true); setError(null);
    try {
      const res = await fetch(`/api/mcp/sox/segments/${encodeURIComponent(ticker)}/estimate-financials?revenue_pct=${pct}`,
        { method: "POST", credentials: "include" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Estimate failed");
      if (data.estimated === false) throw new Error(data.reason || "No consolidated data available for this ticker");
      const str = v => (v == null ? "" : String(v));
      setForm(f => ({
        ...f,
        revenue: f.revenue || str(data.revenue),
        gross_profit: str(data.gross_profit), operating_income: str(data.operating_income),
        net_income: str(data.net_income), assets: str(data.assets),
        gross_margin_pct: str(data.gross_margin_pct), op_margin_pct: str(data.op_margin_pct),
        net_margin_pct: str(data.net_margin_pct),
      }));
    } catch (e) {
      setError(e.message);
    } finally {
      setEstimating(false);
    }
  }

  return { run, estimating, error };
}

function SegmentFieldGrid({ form, setForm, ticker }) {
  const set = k => e => setForm(f => ({ ...f, [k]: e.target.value }));
  const inputStyle = {fontSize: 11, padding: "4px 8px", border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink)"};
  const estimate = useFinancialsEstimate(ticker, form, setForm);
  return (
    <>
      <div style={{display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8}}>
        <input placeholder="Segment name *" value={form.segment_name} onChange={set("segment_name")}
          style={{...inputStyle, flex: 2, minWidth: 140}}/>
        <select value={form.segment_type} onChange={set("segment_type")} style={{...inputStyle, flex: 1, minWidth: 130}}>
          {SEGMENT_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      </div>
      <div style={{display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8}}>
        <input placeholder="Revenue $" value={form.revenue} onChange={set("revenue")} style={{...inputStyle, flex: 1, minWidth: 100}}/>
        <input placeholder="Revenue % of total" value={form.revenue_pct} onChange={set("revenue_pct")} style={{...inputStyle, flex: 1, minWidth: 100}}/>
        <input placeholder="Rev. growth YoY %" value={form.rev_growth_yoy_pct} onChange={set("rev_growth_yoy_pct")} style={{...inputStyle, flex: 1, minWidth: 100}}/>
      </div>
      <div style={{display: "flex", gap: 8, alignItems: "center", marginBottom: 8}}>
        <button type="button" className="btn btn-sm" style={{fontSize: 10, padding: "3px 10px"}}
          onClick={estimate.run} disabled={estimate.estimating}
          title="Fill gross profit / operating income / net income / assets / margins below by allocating this ticker's most recent consolidated figures by Revenue % of total.">
          {estimate.estimating ? "Calculating…" : "Calculate from % of consolidated"}
        </button>
        {estimate.error && <span style={{fontSize: 10, color: "var(--red-ink)"}}>{estimate.error}</span>}
      </div>
      <div style={{display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8}}>
        <input placeholder="Gross profit $" value={form.gross_profit} onChange={set("gross_profit")} style={{...inputStyle, flex: 1, minWidth: 100}}/>
        <input placeholder="Operating income $" value={form.operating_income} onChange={set("operating_income")} style={{...inputStyle, flex: 1, minWidth: 100}}/>
        <input placeholder="Net income $" value={form.net_income} onChange={set("net_income")} style={{...inputStyle, flex: 1, minWidth: 100}}/>
        <input placeholder="Assets $" value={form.assets} onChange={set("assets")} style={{...inputStyle, flex: 1, minWidth: 100}}/>
      </div>
      <div style={{display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8}}>
        <input placeholder="Gross margin %" value={form.gross_margin_pct} onChange={set("gross_margin_pct")} style={{...inputStyle, flex: 1, minWidth: 100}}/>
        <input placeholder="Op. margin %" value={form.op_margin_pct} onChange={set("op_margin_pct")} style={{...inputStyle, flex: 1, minWidth: 100}}/>
        <input placeholder="Net margin %" value={form.net_margin_pct} onChange={set("net_margin_pct")} style={{...inputStyle, flex: 1, minWidth: 100}}/>
      </div>
      <div style={{fontSize: 9.5, color: "var(--ink-4)", marginBottom: 8}}>
        Figures filled by "Calculate from %" are estimates (consolidated × Revenue %), not filed data — review before saving.
      </div>
    </>
  );
}

function SegmentsManager({ ticker, fiscalYear }) {
  const [segments, setSegments] = React.useState([]);
  const [loading, setLoading]   = React.useState(false);
  const [error, setError]       = React.useState(null);
  const [showForm, setShowForm] = React.useState(false);
  const [addForm, setAddForm]   = React.useState(EMPTY_SEGMENT_FORM);
  const [saving, setSaving]     = React.useState(false);
  const [editingId, setEditingId] = React.useState(null);
  const [editForm, setEditForm]   = React.useState(EMPTY_SEGMENT_FORM);
  const [confirmDelete, setConfirmDelete] = React.useState(null);
  const [importing, setImporting] = React.useState(false);
  const [importResult, setImportResult] = React.useState(null);

  const reload = React.useCallback(async () => {
    if (!ticker || !fiscalYear) return;
    setLoading(true); setError(null);
    try {
      const r = await fetch(`/api/mcp/sox/segments/${encodeURIComponent(ticker)}/${encodeURIComponent(fiscalYear)}`, { credentials: "include" });
      if (r.ok) {
        const data = await r.json();
        setSegments(data.segments || []);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [ticker, fiscalYear]);

  React.useEffect(() => { reload(); }, [reload]);

  async function handleAdd() {
    if (!addForm.segment_name.trim()) { setError("Segment name required"); return; }
    setSaving(true); setError(null);
    try {
      const res = await fetch(`/api/mcp/sox/segments/${encodeURIComponent(ticker)}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segments: [segFormToPayload(addForm)], fiscal_year: fiscalYear }),
      });
      if (!res.ok) throw new Error(await res.text());
      setAddForm(EMPTY_SEGMENT_FORM);
      setShowForm(false);
      await reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveEdit(seg) {
    setSaving(true); setError(null);
    try {
      const res = await fetch(`/api/mcp/sox/segments/${encodeURIComponent(ticker)}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segments: [segFormToPayload(editForm)], fiscal_year: fiscalYear }),
      });
      if (!res.ok) throw new Error(await res.text());
      setEditingId(null);
      await reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(seg) {
    try {
      await fetch(`/api/mcp/sox/segments/${encodeURIComponent(ticker)}/${seg.id}`, { method: "DELETE", credentials: "include" });
      await reload();
    } catch (e) {
      setError(e.message);
    }
  }

  // Pulls segment/geography revenue straight off the company's latest
  // 10-K/10-Q XBRL (edgar_segments.py) — filed dimensionally when the
  // filer reports it that way, otherwise allocated from consolidated
  // totals by revenue_pct (source 'filed' vs 'filed+estimated' on each
  // resulting row — see the badge in the list below). Only reconciled
  // breakdowns are written; anything skipped is reported here, not silently
  // dropped.
  async function handleImportXbrl() {
    setImporting(true); setError(null); setImportResult(null);
    try {
      const res = await fetch(`/api/mcp/sox/segments/${encodeURIComponent(ticker)}/import-xbrl`,
        { method: "POST", credentials: "include" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Import failed");
      setImportResult(data);
      if (data.persisted?.length) await reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setImporting(false);
    }
  }

  return (
    <div style={{background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "12px 16px", marginBottom: 12}}>
      <div style={{display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10, gap: 8, flexWrap: "wrap"}}>
        <SectionHead title="SEGMENT DATA" count={segments.length || null} countColor="var(--acc-soft)"/>
        <div style={{display: "flex", gap: 6}}>
          <button className="btn btn-sm" style={{fontSize: 10, padding: "3px 10px"}} onClick={handleImportXbrl} disabled={importing || !ticker}
            title="Auto-fill segment/geography revenue (and, where filed, gross profit) from the latest 10-K/10-Q XBRL.">
            {importing ? "Importing…" : "⇩ Import from filings"}
          </button>
          <button className="btn btn-sm" style={{fontSize: 10, padding: "3px 10px"}} onClick={() => setShowForm(s => !s)}>
            <Icon name="plus" size={10}/> Add segment
          </button>
        </div>
      </div>

      {error && (
        <div className="mono" style={{fontSize: 10.5, color: "var(--red-ink)", background: "var(--red-soft)", padding: "6px 10px", borderRadius: 4, marginBottom: 10}}>{error}</div>
      )}

      {importResult && (
        <div className="mono" style={{fontSize: 10.5, color: "var(--ink-3)", background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)", padding: "6px 10px", borderRadius: 4, marginBottom: 10}}>
          {importResult.extracted === false
            ? `Nothing imported — ${importResult.reason || "no dimensional segment data found"}.`
            : `Imported ${importResult.persisted?.length || 0} segment${importResult.persisted?.length === 1 ? "" : "s"} from ${importResult.source_form || "the latest filing"}` +
              (importResult.skipped?.length ? `; ${importResult.skipped.length} skipped (${importResult.skipped.map(s => s.reason).join("; ")}).` : ".")}
        </div>
      )}

      {showForm && (
        <div style={{background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)", borderRadius: 6, padding: "12px 14px", marginBottom: 12}}>
          <div className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 8}}>NEW SEGMENT · {fiscalYear}</div>
          <SegmentFieldGrid form={addForm} setForm={setAddForm} ticker={ticker}/>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn btn-sm btn-approve" onClick={handleAdd} disabled={saving}>{saving ? "Saving…" : "Save segment"}</button>
            <button className="btn btn-sm" onClick={() => setShowForm(false)}>Cancel</button>
          </div>
        </div>
      )}

      {loading && <div style={{fontSize: 11, color: "var(--ink-4)", padding: "6px 0"}}>Loading…</div>}

      {!loading && segments.length === 0 && !showForm && (
        <div style={{fontSize: 11, color: "var(--ink-4)", padding: "8px 0"}}>
          No segment data on file for {fiscalYear}. Click "Import from filings" to auto-fill from the latest 10-K/10-Q,
          or "Add segment" to enter one by hand.
        </div>
      )}

      {segments.map(seg => {
        const isEditing = editingId === seg.id;
        return (
          <div key={seg.id} style={{padding: "8px 0", borderBottom: "1px solid var(--line)"}}>
            {!isEditing ? (
              <div style={{display: "flex", alignItems: "center", gap: 8}}>
                <span className="mono" style={{fontSize: 9, padding: "1px 6px", borderRadius: 4, background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)", color: "var(--ink-4)"}}>
                  {SEGMENT_TYPE_LABELS[seg.segment_type] || seg.segment_type}
                </span>
                <div style={{flex: 1, minWidth: 0}}>
                  <span style={{fontSize: 11.5, fontWeight: 500, color: "var(--ink)"}}>{seg.segment_name}</span>
                </div>
                <SegmentSourceBadge source={seg.source}/>
                <span className="mono" style={{fontSize: 10, color: "var(--ink-3)"}}>{fmtM(seg.revenue)}</span>
                <span className="mono" style={{fontSize: 10, color: "var(--ink-4)", minWidth: 44, textAlign: "right"}}>{seg.revenue_pct != null ? `${seg.revenue_pct.toFixed(1)}%` : "—"}</span>
                <button className="btn btn-sm" style={{padding: "2px 7px", fontSize: 9}}
                  onClick={() => { setEditForm(segToForm(seg)); setEditingId(seg.id); }}>
                  <Icon name="edit" size={9}/> Edit
                </button>
                <button className="btn btn-sm" style={{padding: "2px 7px", fontSize: 9, opacity: 0.7}}
                  onClick={() => setConfirmDelete(seg)}>Delete</button>
              </div>
            ) : (
              <div style={{background: "var(--surface-2, var(--surface))", border: "1px solid var(--line)", borderRadius: 6, padding: "10px 12px"}}>
                <SegmentFieldGrid form={editForm} setForm={setEditForm} ticker={ticker}/>
                <div style={{display: "flex", gap: 8}}>
                  <button className="btn btn-sm btn-approve" onClick={() => handleSaveEdit(seg)} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
                  <button className="btn btn-sm" onClick={() => setEditingId(null)}>Cancel</button>
                </div>
              </div>
            )}
          </div>
        );
      })}

      <div style={{marginTop: 10, fontSize: 9.5, color: "var(--ink-4)"}}>
        Saved to the database immediately. Click "Rescope" above to recompute SOX materiality coverage below with this data.
      </div>
      <ConfirmModal
        open={!!confirmDelete}
        title="Delete segment?"
        message={confirmDelete ? `Delete segment "${confirmDelete.segment_name}"?` : ""}
        danger confirmLabel="Delete"
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => { const seg = confirmDelete; setConfirmDelete(null); handleDelete(seg); }}
      />
    </div>
  );
}


// ── Segment coverage + SOX materiality scoring ────────────────────────────────

function SegmentCoverage({ segments, scope }) {
  if (!segments?.length) {
    return (
      <div style={{background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "20px 16px", marginBottom: 12, textAlign: "center"}}>
        <div style={{fontSize: 13, fontWeight: 600, color: "var(--ink)", marginBottom: 6}}>No segment data on file</div>
        <div style={{fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.6, maxWidth: 480, margin: "0 auto"}}>
          Coverage decisions are computed from the geography &amp; segment data above.
          Add segment data, then click "Rescope" to compute per-segment SOX materiality.
          Segments with revenue ≥ 15% of total are automatically flagged P1 (AS2201).
        </div>
      </div>
    );
  }

  const geoSegs = segments.filter(s => s.segment_type === "geography");
  const bizSegs = segments.filter(s => s.segment_type !== "geography");

  // Materiality rates from consolidated scope
  const fyRev   = scope?.revenue_forecast_fy;
  const pm      = scope?.planning_materiality;
  const pem     = scope?.performance_materiality;
  const matRate = fyRev && pm ? pm / fyRev : 0.05;
  const perfRate = pm && pem ? pem / pm : 0.75;

  function SegSection({ rows, title }) {
    if (!rows.length) return null;
    return (
      <div style={{marginTop: 16}}>
        <div style={{display: "flex", alignItems: "center", gap: 8, marginBottom: 8}}>
          <span className="mono" style={{fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em"}}>{title}</span>
          <span style={{height: 1, flex: 1, background: "var(--line)"}}/>
          <span className="mono" style={{fontSize: 8.5, color: "var(--ink-4)"}}>
            {rows.filter(r => r.in_scope).length}/{rows.length} IN SCOPE
          </span>
        </div>
        <div style={{overflowX: "auto"}}>
          <table style={{width: "100%", borderCollapse: "collapse", fontSize: 11}}>
            <thead>
              <tr style={{borderBottom: "2px solid var(--line)"}}>
                {["Segment","Revenue","Rev %","Planning Mat.","Perf. Mat.","SOX Priority","Decision","Rationale"].map(h => (
                  <th key={h} style={{
                    textAlign: (h === "Segment" || h === "Rationale") ? "left" : "right",
                    padding: "4px 10px 5px 0",
                    color: "var(--ink-4)", fontWeight: 400,
                    fontFamily: "Geist Mono, monospace", fontSize: 9, whiteSpace: "nowrap",
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((seg, i) => {
                const segPM    = seg.revenue != null ? seg.revenue * matRate : null;
                const segPerfM = segPM != null ? segPM * perfRate : null;
                const pct      = seg.revenue_pct;
                const pri      = pct >= 15 ? "P1" : pct >= 5 ? "P2" : "Out";
                const priClr   = {P1: COV_COLORS.P1, P2: COV_COLORS.P2, Out: COV_COLORS.Out}[pri];
                const decClr   = DECISION_COLORS[seg.in_scope ? "in_scope" : "out"];
                return (
                  <tr key={i} style={{borderBottom: "1px solid var(--line)", opacity: seg.in_scope ? 1 : 0.6}}>
                    <td style={{padding: "7px 10px 7px 0"}}>
                      <div style={{display: "flex", alignItems: "center", gap: 6}}>
                        <span style={{width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                          background: seg.in_scope ? "var(--green-ink)" : "var(--ink-4)"}}/>
                        <span style={{fontSize: 11.5, fontWeight: 500, color: "var(--ink)"}}>{seg.segment_name}</span>
                      </div>
                    </td>
                    <td style={{textAlign: "right", padding: "7px 10px 7px 0", fontFamily: "Geist Mono, monospace"}}>
                      {fmtM(seg.revenue)}
                    </td>
                    <td style={{textAlign: "right", padding: "7px 10px 7px 0", fontFamily: "Geist Mono, monospace", color: "var(--ink-3)"}}>
                      {pct != null ? `${pct.toFixed(1)}%` : "—"}
                    </td>
                    <td style={{textAlign: "right", padding: "7px 10px 7px 0", fontFamily: "Geist Mono, monospace"}}>
                      {segPM != null ? fmtM(segPM) : "—"}
                    </td>
                    <td style={{textAlign: "right", padding: "7px 10px 7px 0", fontFamily: "Geist Mono, monospace", color: "var(--amber-ink)"}}>
                      {segPerfM != null ? fmtM(segPerfM) : "—"}
                    </td>
                    <td style={{textAlign: "right", padding: "7px 10px 7px 0"}}>
                      <Pill ink={priClr.ink} soft={priClr.soft} size={9}>{priClr.label}</Pill>
                    </td>
                    <td style={{textAlign: "right", padding: "7px 10px 7px 0"}}>
                      <Pill ink={decClr.ink} soft={decClr.soft} size={9}>{decClr.label}</Pill>
                    </td>
                    <td style={{padding: "7px 0 7px 0", maxWidth: 260, fontSize: 10, color: "var(--ink-3)", lineHeight: 1.4}}>
                      {seg.rationale || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return (
    <div style={{background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "12px 16px", marginBottom: 12}}>
      <SectionHead
        title="SOX MATERIALITY BY SEGMENT"
        count={`${segments.filter(s => s.in_scope).length} of ${segments.length} in scope`}
        countColor="var(--green-soft)"
      />

      {/* Consolidated header */}
      {(pm || fyRev) && (
        <div style={{display: "flex", gap: 20, padding: "10px 0 12px", borderBottom: "2px solid var(--line)", flexWrap: "wrap"}}>
          {fyRev && (
            <div>
              <div className="mono" style={{fontSize: 9, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 2}}>CONSOLIDATED REVENUE</div>
              <div style={{fontSize: 14, fontWeight: 700, color: "var(--ink)", fontFamily: "var(--mono)"}}>{fmtM(fyRev)}</div>
            </div>
          )}
          {pm && (
            <div>
              <div className="mono" style={{fontSize: 9, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 2}}>PLANNING MATERIALITY</div>
              <div style={{fontSize: 14, fontWeight: 700, color: "var(--ink)", fontFamily: "var(--mono)"}}>{fmtM(pm)}</div>
              <div style={{fontSize: 9.5, color: "var(--ink-4)"}}>≈{(matRate * 100).toFixed(1)}% of revenue</div>
            </div>
          )}
          {pem && (
            <div>
              <div className="mono" style={{fontSize: 9, color: "var(--ink-4)", letterSpacing: "0.06em", marginBottom: 2}}>PERFORMANCE MATERIALITY</div>
              <div style={{fontSize: 14, fontWeight: 700, color: "var(--amber-ink)", fontFamily: "var(--mono)"}}>{fmtM(pem)}</div>
              <div style={{fontSize: 9.5, color: "var(--ink-4)"}}>≈{(perfRate * 100).toFixed(0)}% of planning</div>
            </div>
          )}
        </div>
      )}

      <SegSection rows={geoSegs.length ? geoSegs : segments} title="GEOGRAPHY"/>
      {bizSegs.length > 0 && geoSegs.length > 0 && (
        <SegSection rows={bizSegs} title="BUSINESS SEGMENT"/>
      )}
      {geoSegs.length === 0 && bizSegs.length > 0 && (
        <SegSection rows={bizSegs} title="BUSINESS SEGMENT"/>
      )}

      <div style={{marginTop: 12, padding: "8px 10px", background: "var(--surface-2)", borderRadius: 6, fontSize: 10, color: "var(--ink-4)", lineHeight: 1.6}}>
        <b style={{fontWeight: 500}}>P1</b> = ≥15% of total revenue (AS2201 threshold — automatic in-scope) ·
        <b style={{fontWeight: 500}}> P2</b> = 5–15% (monitor; qualitative factors may trigger) ·
        <b style={{fontWeight: 500}}> Out</b> = &lt;5%.
        Segment planning materiality applies the consolidated rate of {(matRate * 100).toFixed(1)}%.
      </div>
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

  // ── HITL gates (Gate S1: materiality + accounts, Gate S2: process coverage) ──
  const auth = window.useAuth ? window.useAuth() : null;
  const auditorName = auth?.user?.display_name || auth?.user?.username || "Auditor";
  const [hitlSox, setHitlSox] = React.useState({ accounts: true, coverage: true });
  const [gateState, setGateState] = React.useState({ g1: null, g2: null }); // null / "pending" / "approved" / "overridden"
  const [materialityApproval, setMaterialityApproval] = React.useState({ status: "pending" });
  const [accountApprovals, setAccountApprovals] = React.useState({});
  const [processApprovals, setProcessApprovals] = React.useState({});
  const [adjustMatOpen, setAdjustMatOpen] = React.useState(false);
  const [adjustAccountId, setAdjustAccountId] = React.useState(null);
  const [adjustAccountOpen, setAdjustAccountOpen] = React.useState(false);
  const [adjustProcessId, setAdjustProcessId] = React.useState(null);
  const [adjustProcessOpen, setAdjustProcessOpen] = React.useState(false);
  const [overrideOpen, setOverrideOpen] = React.useState(false);
  const [overrideGateNum, setOverrideGateNum] = React.useState(null);

  const displayScope = localScope;

  // Maps an approval_tasks row (server) onto the shape SoxGate1Review /
  // SoxGate2Review expect for a single item's approval state.
  function taskToApproval(task) {
    return {
      status: task.status,
      adjustments: task.adjustments || undefined,
      rationale: task.rationale || undefined,
      adjustedBy: task.prepared_by_name || undefined,
      adjustedAt: task.prepared_at ? new Date(task.prepared_at).getTime() : undefined,
      managerName: task.manager_name || undefined,
      reviewerName: task.reviewed_by_name || undefined,
      reviewedAt: task.reviewed_at ? new Date(task.reviewed_at).getTime() : undefined,
      reviewComment: task.review_comment || undefined,
    };
  }

  const isResolvedStatus = (s) => s === "approved" || s === "submitted" || s === "manager_approved";

  function initGateApprovals(data, tasks) {
    const byKey = {};
    (tasks || []).forEach(t => { byKey[`${t.gate_type}:${t.item_ref}`] = t; });

    const matTask = byKey["sox_materiality:materiality"];
    setMaterialityApproval(matTask ? taskToApproval(matTask) : { status: "pending" });

    let allAccResolved = true;
    const initAcc = {};
    (data.accounts_in_scope || []).forEach(a => {
      const t = byKey[`sox_account:${a.account_id}`];
      const approval = t ? taskToApproval(t) : { status: "pending" };
      initAcc[a.account_id] = { ...approval, account_name: a.account_name };
      if (!isResolvedStatus(approval.status)) allAccResolved = false;
    });
    setAccountApprovals(initAcc);

    let allProcResolved = true;
    const initProc = {};
    (data.processes_in_scope || []).forEach(p => {
      const t = byKey[`sox_process:${p.process_id}`];
      const approval = t ? taskToApproval(t) : { status: "pending" };
      initProc[p.process_id] = { ...approval, process_name: p.process_name };
      if (!isResolvedStatus(approval.status)) allProcResolved = false;
    });
    setProcessApprovals(initProc);

    const g1Done = isResolvedStatus((matTask ? taskToApproval(matTask) : { status: "pending" }).status) && allAccResolved;
    setGateState({
      g1: g1Done ? "approved" : (hitlSox.accounts ? "pending" : "approved"),
      g2: (g1Done && allProcResolved) ? "approved" : (g1Done ? (hitlSox.coverage ? "pending" : "approved") : null),
    });
  }

  // Load from API on mount / when runId changes
  React.useEffect(() => {
    if (!runId) return;
    (async () => {
      try {
        const [scopeRes, statusRes] = await Promise.all([
          fetch(`/api/mcp/sox/scope/${runId}`, { credentials: "include" }),
          fetch(`/approvals/status/${runId}`, { credentials: "include" }),
        ]);
        let tasks = [];
        try { if (statusRes.ok) tasks = (await statusRes.json()).tasks || []; } catch (_) {}
        if (scopeRes.ok) {
          const data = await scopeRes.json();
          setLocalScope(data);
          setLocalSystems(data.systems_in_scope || []);
          initGateApprovals(data, tasks.filter(t => t.gate_type.startsWith("sox_")));
        }
      } catch (_) {}
    })();
  }, [runId]);

  async function runScoping() {
    if (!forecasts || !risks) { setError("Run the pipeline first to generate forecasts and risk scores"); return; }
    setLoading(true); setError(null);
    try {
      // Build forecast payload in backend format from frontend forecasts object.
      // forecasts.revenue.forecast[].base/lo/hi are stored in $M (see app.jsx /
      // risk-engine.js, both divide by 1e6 for chart display) — sox_scoping_tool.py
      // expects raw dollars (it mixes revenue_fy with ratios.assets_now/cash_now,
      // which come straight from EDGAR XBRL, unscaled), so convert back here.
      const fcPayload = {
        forecasts: (forecasts.revenue?.forecast || []).map((q, i) => ({
          horizon: i + 1,
          point: (q.base ?? q.v ?? 0) * 1e6,
          ci_lower: (q.lo ?? q.base ?? q.v ?? 0) * 1e6,
          ci_upper: (q.hi ?? q.base ?? q.v ?? 0) * 1e6,
        })),
        metric: "Revenue",
      };

      const body = {
        run_id: runId ?? null,
        ticker: ticker || "",
        forecast: fcPayload,
        risk_scores: { risks: risks || [] },
        ratios: ratios || {},
        fiscal_year: "",
        trigger_reason: "manual_rerun",
      };

      const res = await fetch(`/api/mcp/sox/scope`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        // Prefer FastAPI's {"detail": "..."} shape over dumping the raw
        // response body — a bare 401 body is just {"detail":"Not authenticated"},
        // which read as a confusing raw-JSON error banner otherwise.
        const errBody = await res.json().catch(() => null);
        throw new Error(errBody?.detail || `Scoping failed (HTTP ${res.status})`);
      }
      const data = await res.json();
      setLocalScope(data);
      setLocalSystems(data.systems_in_scope || []);
      // A fresh Rescope always re-opens the gates for review, mirroring the
      // Enterprise Risk pipeline's rerunFromS3 resetting Gate 2 on rerun.
      initGateApprovals(data, []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  // ---- Real approval workflow: submit a preparer disposition, get back the
  // resolved status (submitted-to-manager, or auto-approved if no manager) ----
  async function submitSoxApprovalTask(gateType, itemRef, itemLabel, disposition, adjustments, rationale) {
    if (!runId || !window.MCP) return null;
    try {
      const result = await window.MCP.prepareApprovalTask({
        runId, gateType, itemRef: String(itemRef), itemLabel, disposition, adjustments, rationale,
      });
      return result.task;
    } catch (_) {
      return null;
    }
  }

  // ---- Gate S1 handlers (materiality + per-account) ----
  const approveMaterial = () => {
    setMaterialityApproval(prev => ({ ...prev, status: "approved" }));
    submitSoxApprovalTask("sox_materiality", "materiality", "Materiality thresholds", "approved", null, null);
  };
  const submitMaterialAdjustment = async (payload) => {
    const adjustments = { materiality_pct: payload.materiality_pct, performance_mat_pct: payload.performance_mat_pct };
    const task = await submitSoxApprovalTask("sox_materiality", "materiality", "Materiality thresholds", "adjusted", adjustments, payload.rationale);
    setMaterialityApproval({
      status: task?.status || "submitted",
      adjustments, rationale: payload.rationale, adjustedBy: auditorName, adjustedAt: Date.now(),
      managerName: task?.manager_name || null,
    });
    setAdjustMatOpen(false);
  };

  const approveAccount = (id) => {
    setAccountApprovals(prev => ({ ...prev, [id]: { ...(prev[id] || {}), status: "approved" } }));
    const acc = (displayScope?.accounts_in_scope || []).find(a => a.account_id === id);
    submitSoxApprovalTask("sox_account", id, acc?.account_name, "approved", null, null);
  };
  const openAdjustAccount = (id) => { setAdjustAccountId(id); setAdjustAccountOpen(true); };
  const submitAccountAdjustment = async (payload) => {
    const id = adjustAccountId;
    if (!id) return;
    const acc = (displayScope?.accounts_in_scope || []).find(a => a.account_id === id);
    const adjustments = { in_scope: payload.in_scope, priority: payload.priority };
    const task = await submitSoxApprovalTask("sox_account", id, acc?.account_name, "adjusted", adjustments, payload.rationale);
    setAccountApprovals(prev => ({
      ...prev,
      [id]: {
        status: task?.status || "submitted",
        adjustments, rationale: payload.rationale, adjustedBy: auditorName, adjustedAt: Date.now(),
        managerName: task?.manager_name || null, account_name: acc?.account_name,
      },
    }));
    setAdjustAccountOpen(false);
    setAdjustAccountId(null);
  };
  const approveAllRemainingAccounts = () => setAccountApprovals(prev => {
    const next = { ...prev };
    Object.keys(next).forEach(id => {
      if (next[id].status === "pending") {
        next[id] = { ...next[id], status: "approved" };
        const acc = (displayScope?.accounts_in_scope || []).find(a => a.account_id === id);
        submitSoxApprovalTask("sox_account", id, acc?.account_name, "approved", null, null);
      }
    });
    return next;
  });

  function confirmGate1() {
    setLocalScope(prev => {
      if (!prev) return prev;
      const merged = (prev.accounts_in_scope || []).map(a => {
        const ap = accountApprovals[a.account_id];
        if (ap && isResolvedStatus(ap.status) && ap.status !== "approved" && ap.adjustments) {
          return { ...a, in_scope: ap.adjustments.in_scope, priority: ap.adjustments.priority, manual_override: true };
        }
        return a;
      });
      return { ...prev, accounts_in_scope: merged };
    });
    setGateState(prev => ({ ...prev, g1: "approved", g2: hitlSox.coverage ? "pending" : "approved" }));
  }

  // ---- Gate S2 handlers (per-process coverage) ----
  const approveProcess = (id) => {
    setProcessApprovals(prev => ({ ...prev, [id]: { ...(prev[id] || {}), status: "approved" } }));
    const proc = (displayScope?.processes_in_scope || []).find(p => p.process_id === id);
    submitSoxApprovalTask("sox_process", id, proc?.process_name, "approved", null, null);
  };
  const openAdjustProcess = (id) => { setAdjustProcessId(id); setAdjustProcessOpen(true); };
  const submitProcessAdjustment = async (payload) => {
    const id = adjustProcessId;
    if (!id) return;
    const proc = (displayScope?.processes_in_scope || []).find(p => p.process_id === id);
    const adjustments = { coverage_level: payload.coverage_level };
    const task = await submitSoxApprovalTask("sox_process", id, proc?.process_name, "adjusted", adjustments, payload.rationale);
    setProcessApprovals(prev => ({
      ...prev,
      [id]: {
        status: task?.status || "submitted",
        adjustments, rationale: payload.rationale, adjustedBy: auditorName, adjustedAt: Date.now(),
        managerName: task?.manager_name || null, process_name: proc?.process_name,
      },
    }));
    setAdjustProcessOpen(false);
    setAdjustProcessId(null);
  };
  const approveAllRemainingProcesses = () => setProcessApprovals(prev => {
    const next = { ...prev };
    Object.keys(next).forEach(id => {
      if (next[id].status === "pending") {
        next[id] = { ...next[id], status: "approved" };
        const proc = (displayScope?.processes_in_scope || []).find(p => p.process_id === id);
        submitSoxApprovalTask("sox_process", id, proc?.process_name, "approved", null, null);
      }
    });
    return next;
  });

  function confirmGate2() {
    setLocalScope(prev => {
      if (!prev) return prev;
      const merged = (prev.processes_in_scope || []).map(p => {
        const ap = processApprovals[p.process_id];
        if (ap && isResolvedStatus(ap.status) && ap.status !== "approved" && ap.adjustments) {
          return { ...p, coverage_level: ap.adjustments.coverage_level, manual_override: true };
        }
        return p;
      });
      return { ...prev, processes_in_scope: merged };
    });
    setGateState(prev => ({ ...prev, g2: "approved" }));
  }

  function requestOverrideGate(n) { setOverrideGateNum(n); setOverrideOpen(true); }
  function confirmOverrideGate(reason) {
    const n = overrideGateNum;
    if (n === 1) setGateState(prev => ({ ...prev, g1: "overridden", g2: hitlSox.coverage ? "pending" : "approved" }));
    else setGateState(prev => ({ ...prev, g2: "overridden" }));
    setOverrideOpen(false);
  }

  function patchAccount(accountId, patch) {
    setLocalScope(prev => prev ? {
      ...prev,
      accounts_in_scope: (prev.accounts_in_scope || []).map(a => a.account_id === accountId ? {...a, ...patch} : a),
    } : prev);
  }

  function patchProcess(processId, patch) {
    setLocalScope(prev => prev ? {
      ...prev,
      processes_in_scope: (prev.processes_in_scope || []).map(p => p.process_id === processId ? {...p, ...patch} : p),
    } : prev);
  }

  const tabs = [
    { id: "accounts",  label: "Accounts" },
    { id: "processes", label: "Processes" },
    { id: "systems",   label: "Systems" },
    ...(displayScope ? [{ id: "segments", label: "Segments" }] : []),
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
            <div style={{display: "flex", gap: 10}}>
              <label style={{display: "flex", alignItems: "center", gap: 4, fontSize: 10, color: "var(--ink-3)", cursor: "pointer"}}>
                <input type="checkbox" checked={hitlSox.accounts} onChange={e => setHitlSox(h => ({...h, accounts: e.target.checked}))}/> Gate S1 review
              </label>
              <label style={{display: "flex", alignItems: "center", gap: 4, fontSize: 10, color: "var(--ink-3)", cursor: "pointer"}}>
                <input type="checkbox" checked={hitlSox.coverage} onChange={e => setHitlSox(h => ({...h, coverage: e.target.checked}))}/> Gate S2 review
              </label>
            </div>
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
            {activeTab === "accounts"  && (
              gateState.g1 === "pending" ? (
                <SoxGate1Review
                  scope={displayScope} accounts={displayScope.accounts_in_scope || []}
                  materialityApproval={materialityApproval} accountApprovals={accountApprovals}
                  onApproveMateriality={approveMaterial} onAdjustMateriality={() => setAdjustMatOpen(true)}
                  onApproveAccount={approveAccount} onAdjustAccount={openAdjustAccount}
                  onApproveAllAccounts={approveAllRemainingAccounts}
                  onSubmit={confirmGate1}
                  onOverrideGate={() => requestOverrideGate(1)}
                />
              ) : (
                <>
                  <SoxGateBanner label="Gate S1 (Materiality & Accounts)" state={gateState.g1}/>
                  <AccountsTable accounts={displayScope.accounts_in_scope || []} ticker={ticker || ""} onUpdate={patchAccount}/>
                  <BreakdownPanel
                    items={displayScope.accounts_in_scope || []}
                    valueOf={a => a.balance_estimate}
                    inScopeOf={a => !!a.in_scope}
                    valueLabel="balance"
                    materiality={displayScope.planning_materiality}
                  />
                </>
              )
            )}
            {activeTab === "processes" && (
              gateState.g1 !== "approved" && gateState.g1 !== "overridden" ? (
                <div style={{fontSize: 12, color: "var(--ink-3)", padding: "24px 0", textAlign: "center"}}>
                  Complete Gate S1 (Materiality &amp; Accounts) first — process coverage review unlocks once accounts are approved.
                </div>
              ) : gateState.g2 === "pending" ? (
                <SoxGate2Review
                  processes={displayScope.processes_in_scope || []} processApprovals={processApprovals}
                  onApproveProcess={approveProcess} onAdjustProcess={openAdjustProcess}
                  onApproveAllProcesses={approveAllRemainingProcesses}
                  onSubmit={confirmGate2}
                  onOverrideGate={() => requestOverrideGate(2)}
                />
              ) : (
                <>
                  <SoxGateBanner label="Gate S2 (Process Coverage)" state={gateState.g2}/>
                  <ProcessesTable processes={displayScope.processes_in_scope || []} ticker={ticker || ""} onUpdate={patchProcess}/>
                  <BreakdownPanel
                    items={displayScope.processes_in_scope || []}
                    valueOf={p => p.estimated_exposure}
                    inScopeOf={p => p.coverage_level !== "Out"}
                    valueLabel="estimated exposure"
                    materiality={displayScope.planning_materiality}
                  />
                </>
              )
            )}
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
            {activeTab === "segments"  && (
              <>
                <SegmentsManager ticker={ticker || ""} fiscalYear={displayScope.fiscal_year}/>
                <SegmentCoverage segments={displayScope.segments_coverage || []} scope={displayScope}/>
              </>
            )}
          </div>
        </>
      )}

      <AdjustMaterialityModal
        open={adjustMatOpen} scope={displayScope} ticker={ticker || ""}
        onClose={() => setAdjustMatOpen(false)} onSubmit={submitMaterialAdjustment}
      />
      <AdjustAccountModal
        open={adjustAccountOpen}
        acc={(displayScope?.accounts_in_scope || []).find(a => a.account_id === adjustAccountId) || null}
        ticker={ticker || ""}
        onClose={() => setAdjustAccountOpen(false)} onSubmit={submitAccountAdjustment}
      />
      <AdjustProcessModal
        open={adjustProcessOpen}
        proc={(displayScope?.processes_in_scope || []).find(p => p.process_id === adjustProcessId) || null}
        ticker={ticker || ""}
        onClose={() => setAdjustProcessOpen(false)} onSubmit={submitProcessAdjustment}
      />
      {window.OverrideModal && (
        <window.OverrideModal
          open={overrideOpen} gateNum={overrideGateNum === 1 ? "S1" : "S2"}
          onClose={() => setOverrideOpen(false)} onConfirm={confirmOverrideGate}
        />
      )}
    </div>
  );
}

Object.assign(window, { SoxScopePanel });
