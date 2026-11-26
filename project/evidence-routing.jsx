/* ============================================================
   Evidence & Routing Review (HITL Gate 1 transparency)
   Shows the reviewer WHY each score is what it is, before they sign off:
     - data-quality banner (unavailable / partial + the gaps)
     - 8-K events that need explicit attention (bankruptcy, adverse
       auditor change, disagreement departures) with acknowledgement
     - where every 8-K landed: category, likelihood points, rationale
       (and every filing that was NOT scored, with the reason)
     - categories that are Not assessed (coverage gaps)
     - the evidence list behind each scored risk
   Pure presentation: all routing/scoring is decided upstream
   (event_risk_map.py, risk_taxonomy.py, RISK_ENGINE.scoreTaxonomy).
   ============================================================ */

const FLAG_LABELS = {
  bankruptcy:            { title: "Bankruptcy / receivership (8-K Item 1.03)", tone: "red",
                           why: "Liquidity & Going Concern is floored at Red. Explicit reviewer sign-off is required before Gate 1 can be approved." },
  forces_gate_signoff:   { title: "Explicit sign-off required", tone: "red", why: "" },
  auditor_change_p1:     { title: "Auditor change with adverse indicator(s) (8-K Item 4.01)", tone: "amber",
                           why: "Resignation, disagreement, reportable event, adverse / going-concern audit report, or a dismissal without documented audit-committee approval." },
  disagreement_departure:{ title: "Departure attributed to a disagreement (8-K Item 5.02)", tone: "amber",
                           why: "An Item 5.02(a) disagreement can trigger a letter to the SEC." },
};

const ER_TONE = {
  red:   { bg: "var(--red-bg, #fdecec)",   bd: "var(--red-ink, #b42318)",   ink: "var(--red-ink, #b42318)" },
  amber: { bg: "var(--amber-bg, #fff4e0)", bd: "var(--amber-ink, #b54708)", ink: "var(--amber-ink, #b54708)" },
  grey:  { bg: "var(--bg-2, #f4f4f5)",     bd: "var(--line, #d4d4d8)",      ink: "var(--ink-3, #52525b)" },
};

function ERBanner({ tone = "grey", children }) {
  const t = ER_TONE[tone];
  return (
    <div style={{ background: t.bg, border: `1px solid ${t.bd}`, color: t.ink, borderRadius: 6,
                  padding: "8px 12px", fontSize: 12, lineHeight: 1.45 }}>{children}</div>
  );
}

function ERChip({ children, tone = "grey" }) {
  const t = ER_TONE[tone];
  return <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 8, border: `1px solid ${t.bd}`, color: t.ink, background: t.bg, whiteSpace: "nowrap" }}>{children}</span>;
}

function detailLine(item, d) {
  if (!d) return null;
  if (item === "1.03") return d.chapter ? `Chapter ${d.chapter}${d.liquidation ? " · liquidation" : ""}` : (d.receivership ? "Receivership" : null);
  if (item === "4.01") {
    const flags = d.p1_reasons?.length ? d.p1_reasons.map(r => r.replace(/_/g, " ")).join(", ") : (d.routine ? "routine rotation (audit-committee approved)" : "nature unclear");
    return `Auditor change: ${flags}`;
  }
  if (item === "5.02") {
    const roles = Object.entries(d.roles || {}).filter(([, v]) => v).map(([k]) => k.replace("_", " ")).join(", ");
    const nature = d.departure ? "departure" : d.appointment ? "appointment" : d.compensation_only ? "compensation" : "change";
    return `${nature}${roles ? ` · ${roles}` : ""}${d.disagreement ? " · disagreement" : ""}`;
  }
  return null;
}

function EvidenceRoutingReview({ routing, notAssessed = [], risks = [], onAckGateFlag }) {
  const r = routing || {};
  const events = r.events || [];
  const flags = r.gateFlags || [];
  const acks = r.acks || {};
  const dq = r.dataQuality;
  const unrouted = r.unrouted || [];
  const eventFor = (acc) => events.find(e => e.id === `8K-${String(acc).replace(/[^A-Z0-9]/gi, "")}`);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: "4px 2px" }}>
      {r.blockMsg && <ERBanner tone="red"><b>{r.blockMsg}</b></ERBanner>}

      {dq?.status === "unavailable" && (
        <ERBanner tone="red">
          <b>No usable data — nothing could be assessed.</b> There is no industry template to fall back to, so every category is “Not assessed”.
          Approve is disabled; use Override and record a reason.
          {(dq.gaps || []).map((g, i) => <div key={i}>• {g}</div>)}
        </ERBanner>
      )}
      {dq?.status === "partial" && (dq.gaps || []).length > 0 && (
        <ERBanner tone="amber">
          <b>Partial evidence.</b> Some sources did not return data; the affected categories rest on other evidence or are Not assessed.
          {dq.gaps.map((g, i) => <div key={i}>• {g}</div>)}
        </ERBanner>
      )}

      {flags.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="kicker">Events needing reviewer attention</div>
          {flags.filter(f => f.flag !== "forces_gate_signoff").map((f, i) => {
            const meta = FLAG_LABELS[f.flag] || { title: f.flag, tone: "amber", why: "" };
            const ev = eventFor(f.accession);
            const forced = flags.some(x => x.accession === f.accession && x.flag === "forces_gate_signoff");
            const ack = acks[f.accession];
            return (
              <ERBanner key={`${f.accession}-${f.flag}-${i}`} tone={meta.tone}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                  <div>
                    <b>{meta.title}</b> — filed {f.date} · Item(s) {f.items}
                    {meta.why && <div style={{ marginTop: 2 }}>{meta.why}</div>}
                    {ev && ev.routing?.length > 0 && (
                      <div style={{ marginTop: 4 }}>Moves: {[...new Set(ev.routing.map(x => x.name))].join(" · ")}</div>
                    )}
                  </div>
                  {forced && (ack
                    ? <ERChip tone="grey">Acknowledged by {ack.by}</ERChip>
                    : <button type="button" className="btn btn-sm btn-acc" style={{ flexShrink: 0 }} onClick={() => onAckGateFlag && onAckGateFlag(f.accession)}>Acknowledge</button>)}
                </div>
              </ERBanner>
            );
          })}
        </div>
      )}

      <div>
        <div className="kicker" style={{ marginBottom: 6 }}>8-K → risk category mapping</div>
        {events.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--ink-3)" }}>No 8-K events were retrieved for this entity.</div>
        ) : (
          <table style={{ width: "100%", fontSize: 11.5, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--ink-3)" }}>
                <th style={{ padding: "3px 6px" }}>Filed</th><th>Item(s)</th><th>Status</th><th>Routed to (likelihood points)</th><th>Content detail</th>
              </tr>
            </thead>
            <tbody>
              {events.map(e => {
                const detail = Object.entries(e.details || {}).map(([k, v]) => detailLine(k, v)).filter(Boolean).join(" · ");
                return (
                  <tr key={e.id} style={{ borderTop: "1px solid var(--line, #e4e4e7)", verticalAlign: "top" }}>
                    <td style={{ padding: "4px 6px", whiteSpace: "nowrap" }}>{e.filingDate}</td>
                    <td>{(e.items || []).join(", ")}</td>
                    <td><ERChip tone={e.mappingStatus === "mapped" ? "grey" : "amber"}>{e.mappingStatus === "review" ? "needs review" : e.mappingStatus}</ERChip>{" "}
                        {e.severity && <ERChip tone={e.severity === "P1" ? "red" : "grey"}>{e.severity}</ERChip>}</td>
                    <td>
                      {(e.routing || []).length ? e.routing.map((x, i) => (
                        <div key={i} title={x.rationale}><b>{x.name}</b> +{x.points} <span style={{ color: "var(--ink-3)" }}>— {x.rationale}</span></div>
                      )) : <span style={{ color: "var(--ink-3)" }}>not scored</span>}
                      {(e.unscoredItems || []).map((u, i) => (
                        <div key={`u${i}`} style={{ color: "var(--ink-3)" }}>Item {u.item} not scored — {u.reason}</div>
                      ))}
                    </td>
                    <td>{detail || "—"}{e.needsReview && !detail && " (filing text unavailable — classify manually)"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {unrouted.length > 0 && (
          <details style={{ marginTop: 6 }}>
            <summary style={{ fontSize: 11.5, cursor: "pointer", color: "var(--ink-3)" }}>{unrouted.length} filing(s) retrieved but not scored</summary>
            <ul style={{ fontSize: 11.5, margin: "4px 0 0 16px", color: "var(--ink-3)" }}>
              {unrouted.map((u, i) => (
                <li key={i}>{u.date} · Item(s) {u.items || "—"} · {u.status} — {(u.reasons || []).join("; ")}</li>
              ))}
            </ul>
          </details>
        )}
      </div>

      {notAssessed.length > 0 && (
        <div>
          <div className="kicker" style={{ marginBottom: 6 }}>Not assessed — coverage gaps ({notAssessed.length})</div>
          <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginBottom: 4 }}>
            No evidence reached these categories, so they carry no score and are excluded from RAG counts, appetite and audit scope. Add a data source or assess manually if they matter for this entity.
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {notAssessed.map(n => <ERChip key={n.id}>{n.name}</ERChip>)}
          </div>
        </div>
      )}

      {risks.some(x => x.evidence?.length) && (
        <div>
          <div className="kicker" style={{ marginBottom: 6 }}>Evidence behind each score</div>
          {risks.filter(x => x.evidence?.length).map(x => (
            <details key={x.id} style={{ marginBottom: 4 }}>
              <summary style={{ fontSize: 12, cursor: "pointer" }}>
                <b>{x.name}</b> — {x.score}/25 ({x.rag}) · {x.evidence.length} evidence item{x.evidence.length === 1 ? "" : "s"}
              </summary>
              <ul style={{ fontSize: 11.5, margin: "4px 0 6px 16px" }}>
                {x.evidence.map((e, i) => (
                  <li key={i}><ERChip>{e.source}</ERChip> {e.label} <b style={{ color: e.points > 0 ? "var(--red-ink, #b42318)" : "var(--ink-3)" }}>{e.points > 0 ? `+${e.points}` : e.points}</b></li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { EvidenceRoutingReview });
export { EvidenceRoutingReview };
