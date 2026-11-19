/* ============================================================
   Control Cost Efficiency — admin-entered control cost vs. estimated
   risk-score reduction.

   No system this platform ingests from carries what a control costs to
   run, so that half of "is this control worth it" has never existed here —
   only the risk side (risk-engine.js's objectives[].controls free-text
   list, and each linked MAP's reduction_pct). This screen is the first
   place the two meet:

     control ref   <- first token of each objectives[].controls string
                       (e.g. "CUS-101" from "CUS-101 Top-10 customer
                       concentration KRI")
     $ cost         <- admin-entered here, persisted via
                       observability.control_cost_profiles
     est. points    <- (map.reduction_pct / controls on that objective)
     reduced           * risk.score — the MAP's documented reduction %,
                       split evenly across every control linked to the
                       same objective (a simplification, not a measured
                       per-control effect — labelled as such, never as a
                       precise causal number)
     $/point        <- annual_cost_usd / est. points reduced

   A control with no cost on file, or whose objective has no MAP (so no
   reduction_pct at all), is flagged rather than silently scored — exactly
   the "never fabricate" discipline coverage-gap.jsx and disclosure-risk.js
   already apply to missing data elsewhere in this platform.
   ============================================================ */

function _ccParseControls(objective) {
  return (objective.controls || []).map(raw => {
    const sp = raw.indexOf(" ");
    return sp === -1 ? { ref: raw.toUpperCase(), label: raw } : { ref: raw.slice(0, sp).toUpperCase(), label: raw.slice(sp + 1) };
  });
}

// One row per control-ref actually referenced by the live register — not
// per DB profile, so a control the register no longer links to doesn't
// show up as a phantom row, and a control with no cost on file still does.
function _ccBuildRows(risks, objectives, maps, profiles) {
  const profileByRef = new Map(profiles.map(p => [p.control_ref, p]));
  const rows = [];
  const seen = new Set();
  for (const o of objectives || []) {
    const risk = (risks || []).find(r => r.id === o.linked_risk);
    const map = (maps || []).find(m => m.linked_risk === o.linked_risk);
    const controls = _ccParseControls(o);
    for (const c of controls) {
      const key = `${c.ref}|${o.id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const profile = profileByRef.get(c.ref) || null;
      const reductionPctTotal = map?.reduction_pct ?? null;
      const apportionedPct = reductionPctTotal != null && controls.length ? reductionPctTotal / controls.length : null;
      const pointsReduced = apportionedPct != null && risk?.score != null ? (apportionedPct / 100) * risk.score : null;
      const costPerPoint = profile?.annual_cost_usd != null && pointsReduced != null && pointsReduced > 0
        ? profile.annual_cost_usd / pointsReduced : null;
      rows.push({
        ref: c.ref, label: c.label, riskId: o.linked_risk, riskName: risk?.name || o.linked_risk,
        objectiveId: o.id, annualCostUsd: profile?.annual_cost_usd ?? null,
        hoursPerMonth: profile?.hours_per_month ?? null, notes: profile?.notes || "",
        pointsReduced, costPerPoint, hasCost: profile?.annual_cost_usd != null,
        hasReduction: pointsReduced != null,
      });
    }
  }
  return rows;
}

function CcEditForm({ row, onSave, onCancel }) {
  const { useState } = React;
  const [cost, setCost] = useState(row.annualCostUsd ?? "");
  const [hours, setHours] = useState(row.hoursPerMonth ?? "");
  const [notes, setNotes] = useState(row.notes || "");
  const [saving, setSaving] = useState(false);

  async function submit() {
    setSaving(true);
    try {
      await onSave(row.ref, {
        annualCostUsd: cost === "" ? null : Number(cost),
        hoursPerMonth: hours === "" ? null : Number(hours),
        notes: notes || null,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", padding: "8px 10px", background: "var(--surface-2)", borderRadius: 6 }}>
      <input className="input" style={{ width: 110 }} type="number" min="0" placeholder="Annual $ cost"
        value={cost} onChange={e => setCost(e.target.value)} />
      <input className="input" style={{ width: 90 }} type="number" min="0" placeholder="Hrs/mo"
        value={hours} onChange={e => setHours(e.target.value)} />
      <input className="input" style={{ flex: 1 }} placeholder="Notes (optional)"
        value={notes} onChange={e => setNotes(e.target.value)} />
      <button className="btn btn-sm btn-acc" onClick={submit} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
      <button className="btn btn-sm" onClick={onCancel}>Cancel</button>
    </div>
  );
}

function ControlCostRow({ row, onSave }) {
  const { useState } = React;
  const [editing, setEditing] = useState(false);

  if (editing) {
    return (
      <tr>
        <td colSpan={7} style={{ padding: "6px 8px" }}>
          <CcEditForm row={row} onSave={async (ref, fields) => { await onSave(ref, fields); setEditing(false); }} onCancel={() => setEditing(false)} />
        </td>
      </tr>
    );
  }

  return (
    <tr style={{ borderBottom: "1px solid var(--line-2)" }}>
      <td style={{ padding: "7px 8px", fontFamily: "var(--mono)", fontSize: 10.5 }}>{row.ref}</td>
      <td style={{ padding: "7px 8px", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.label}</td>
      <td style={{ padding: "7px 8px", fontSize: 10.5, color: "var(--ink-3)" }}>{row.riskName}</td>
      <td style={{ padding: "7px 8px" }}>
        {row.hasCost ? `$${row.annualCostUsd.toLocaleString()}/yr` : <span style={{ color: "var(--amber-ink)" }}>No cost on file</span>}
      </td>
      <td style={{ padding: "7px 8px" }}>
        {row.hasReduction ? row.pointsReduced.toFixed(2) : <span style={{ color: "var(--ink-4)" }}>not computed</span>}
      </td>
      <td style={{ padding: "7px 8px", fontWeight: 700 }}>
        {row.costPerPoint == null ? "—" : `$${row.costPerPoint.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
      </td>
      <td style={{ padding: "7px 8px" }}>
        <button className="btn btn-sm" onClick={() => setEditing(true)}>Edit cost</button>
      </td>
    </tr>
  );
}

function ControlCostScreen({ risks = [], objectives = [], maps = [], onNavigate } = {}) {
  const { useState, useEffect, useMemo } = React;
  const [profiles, setProfiles] = useState(null);
  const [error, setError] = useState(null);

  function load() {
    window.MCP.controlCostListProfiles()
      .then(d => { setProfiles(d.profiles || []); setError(null); })
      .catch(e => setError(e.message || String(e)));
  }
  useEffect(load, []);

  const rows = useMemo(() => profiles ? _ccBuildRows(risks, objectives, maps, profiles) : [], [risks, objectives, maps, profiles]);
  const costed = rows.filter(r => r.hasCost);
  const scored = rows.filter(r => r.costPerPoint != null);
  const totalAnnualCost = costed.reduce((s, r) => s + r.annualCostUsd, 0);
  const noCost = rows.filter(r => !r.hasCost).length;
  const noReduction = rows.filter(r => !r.hasReduction).length;
  const reviewForValue = [...scored].sort((a, b) => b.costPerPoint - a.costPerPoint).slice(0, 5);

  async function handleSave(ref, fields) {
    await window.MCP.controlCostUpsertProfile(ref, fields);
    load();
  }

  const sorted = [...rows].sort((a, b) => (b.costPerPoint ?? -1) - (a.costPerPoint ?? -1));

  return (
    <div style={{ padding: "0 20px 32px", maxWidth: 1100 }}>
      <div className="panel-head" style={{ paddingLeft: 0, paddingRight: 0 }}>
        <div>
          <div className="kicker">Operations Intelligence</div>
          <div className="panel-title mt-8">Control Cost Efficiency</div>
          <div className="panel-sub">
            Every control the live register links to a risk, against what it costs to run (admin-entered —
            no system here tracks that) and an estimated $/risk-point figure. The estimate apportions each
            action plan's documented reduction % evenly across its linked controls; it is not a measured
            per-control effect.
          </div>
        </div>
      </div>

      {error && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 10 }}>{error}</div>}
      {!objectives.length ? (
        <Empty>Run the loop to populate the risk register and audit objectives first.</Empty>
      ) : (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 20 }}>
            {[
              { label: "Controls tracked", value: rows.length },
              { label: "Total annual cost on file", value: `$${totalAnnualCost.toLocaleString()}`, sub: `${costed.length}/${rows.length} controls costed` },
              { label: "No cost on file", value: noCost, color: noCost ? "var(--amber-ink)" : "var(--green-ink)" },
              { label: "No reduction % to compare against", value: noReduction, color: noReduction ? "var(--amber-ink)" : "var(--green-ink)" },
            ].map(s => (
              <div key={s.label} style={{ flex: "1 1 180px", minWidth: 160, background: "var(--surface-2)",
                border: "1px solid var(--line)", borderRadius: 8, padding: "10px 14px" }}>
                <div style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4 }}>{s.label}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: s.color }}>{s.value}</div>
                {s.sub && <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 2 }}>{s.sub}</div>}
              </div>
            ))}
          </div>

          {reviewForValue.length > 0 && (
            <div style={{ background: "var(--amber-soft)", border: "1px solid var(--amber)", borderRadius: 8, padding: "12px 16px", marginBottom: 20 }}>
              <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 6 }}>Highest cost per risk-point reduced — review for value</div>
              {reviewForValue.map(r => (
                <div key={r.ref} style={{ fontSize: 11, marginBottom: 2 }}>
                  <b>{r.ref}</b> {r.label} — ${r.costPerPoint.toLocaleString(undefined, { maximumFractionDigits: 0 })}/point
                </div>
              ))}
            </div>
          )}

          {!profiles ? (
            <div style={{ padding: 20, color: "var(--ink-3)", fontSize: 11.5 }}>Loading…</div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)" }}>
                  {["Ref", "Control", "Risk", "Annual cost", "Est. points reduced", "$/point", ""].map(h => (
                    <th key={h} style={{ textAlign: "left", padding: "6px 8px", color: "var(--ink-3)", fontSize: 10, fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map(r => <ControlCostRow key={`${r.ref}|${r.objectiveId}`} row={r} onSave={handleSave} />)}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}

Object.assign(window, { ControlCostScreen });
