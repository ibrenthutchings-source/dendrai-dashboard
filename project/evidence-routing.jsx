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
  say_on_pay_failed:     { title: "Say-on-pay vote failed (8-K Item 5.07)", tone: "red",
                           why: "Less than 50% support. Adds Governance likelihood; check the board's stated response in the next proxy." },
  say_on_pay_low:        { title: "Low say-on-pay support (8-K Item 5.07)", tone: "amber",
                           why: "Below the 70% level at which ISS expects board responsiveness (Glass Lewis 80%)." },
  vote_failed:           { title: "A shareholder vote failed (8-K Item 5.07)", tone: "amber",
                           why: "A proposal or director election did not receive the required vote." },
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
  if (item === "5.07") {
    if (d.say_on_pay_support != null) return `Say-on-pay support ${Math.round(d.say_on_pay_support * 100)}%${d.proposal_failed ? " · a proposal failed" : ""}`;
    return d.proposal_failed ? "A proposal failed" : (d.has_say_on_pay ? "Say-on-pay tallies unparsed" : "No say-on-pay vote");
  }
  if (item === "5.02") {
    const roles = Object.entries(d.roles || {}).filter(([, v]) => v).map(([k]) => k.replace("_", " ")).join(", ");
    const nature = d.departure ? "departure" : d.appointment ? "appointment" : d.compensation_only ? "compensation" : "change";
    return `${nature}${roles ? ` · ${roles}` : ""}${d.disagreement ? " · disagreement" : ""}`;
  }
  return null;
}


function RiskFactorChanges({ data }) {
  if (!data) return null;
  const changes = data.changes || [];
  const tone = (c) => (c === "escalated" ? "red" : c === "new" ? "amber" : "grey");
  return (
    <div>
      <div className="kicker" style={{ marginBottom: 6 }}>10-K Item 1A risk-factor changes (deterministic year-over-year diff)</div>
      {data.status !== "ok" && (
        <ERBanner tone="grey">Year-over-year changes not scored — {data.message || data.status}.</ERBanner>
      )}
      {data.status === "ok" && (
        <>
          <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginBottom: 4 }}>
            {data.current?.date} 10-K ({data.current?.n_factors} factors) vs {data.prior?.date} ({data.prior?.n_factors}); {data.matched} matched.
            New +0.3, escalated wording (hypothetical → realised) +0.5, expanded ≥1.5× +0.2, weighted by filing age, max 1.5 per category. Removed and unclassified factors are listed, not scored.
          </div>
          {changes.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--ink-3)" }}>No new, escalated or expanded risk factors versus the prior 10-K.</div>
          ) : (
            <table style={{ width: "100%", fontSize: 11.5, borderCollapse: "collapse" }}>
              <thead><tr style={{ textAlign: "left", color: "var(--ink-3)" }}><th style={{ padding: "3px 6px" }}>Change</th><th>Risk factor</th><th>Category</th><th>Points</th></tr></thead>
              <tbody>
                {changes.map((c, i) => (
                  <tr key={i} style={{ borderTop: "1px solid var(--line, #e4e4e7)", verticalAlign: "top" }} title={c.rationale}>
                    <td style={{ padding: "4px 6px" }}><ERChip tone={tone(c.change)}>{c.change}</ERChip></td>
                    <td>{c.heading}</td>
                    <td>{c.topic ? <>{c.topic} <span style={{ color: "var(--ink-3)" }}>({c.tx})</span></> : <span style={{ color: "var(--ink-3)" }}>no topic rule matched — not scored</span>}</td>
                    <td>{c.tx ? `+${c.weighted_points ?? c.points}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {(data.removed || []).length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ fontSize: 11.5, cursor: "pointer", color: "var(--ink-3)" }}>{data.removed.length} factor(s) removed since the prior 10-K (not scored)</summary>
              <ul style={{ fontSize: 11.5, margin: "4px 0 0 16px", color: "var(--ink-3)" }}>
                {data.removed.map((x, i) => <li key={i}>{x.heading}{x.topic ? ` — ${x.topic}` : ""}</li>)}
              </ul>
            </details>
          )}
        </>
      )}

      {(data.presence || []).length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginBottom: 4 }}>
            Standing disclosures (baseline — a disclosed risk factor makes the category assessable; it does not imply a change):
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {data.presence.map(p => <ERChip key={p.tx}>{p.topic} · {p.n} · +{p.points}</ERChip>)}
          </div>
        </div>
      )}

      {data.icfr && (
        <div style={{ marginTop: 8 }}>
          <div className="kicker" style={{ marginBottom: 4 }}>10-K Item 9A — internal control &amp; going concern</div>
          {!data.icfr.item_9a?.found ? (
            <ERBanner tone="grey">No Item 9A section was found by the parser — internal-control status is not assessed from the 10-K.</ERBanner>
          ) : (() => {
            const a = data.icfr.item_9a;
            const yn = (v, good, bad) => v === true ? good : v === false ? bad : null;
            const chips = [
              a.icfr_effective === false ? <ERChip key="i" tone="red">ICFR NOT effective</ERChip> : a.icfr_effective === true ? <ERChip key="i">✓ ICFR effective</ERChip> : <ERChip key="i" tone="amber">ICFR conclusion not found</ERChip>,
              a.disclosure_controls_effective === false ? <ERChip key="d" tone="red">Disclosure controls NOT effective</ERChip> : a.disclosure_controls_effective === true ? <ERChip key="d">✓ Disclosure controls effective</ERChip> : null,
              a.material_weakness ? <ERChip key="m" tone="red">Material weakness</ERChip> : null,
              a.weakness_remediated ? <ERChip key="r" tone="amber">Prior weakness remediated</ERChip> : null,
              a.icfr_changes ? <ERChip key="c" tone="amber">Change in ICFR</ERChip> : null,
              <ERChip key="a" tone={a.attestation === "exempt" ? "amber" : "grey"}>{a.attestation === "attested" ? "✓ Auditor attestation" : a.attestation === "exempt" ? "No attestation (exempt)" : "Attestation not found"}</ERChip>,
            ].filter(Boolean);
            return <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>{chips}</div>;
          })()}
          {data.icfr.going_concern?.status === "substantial_doubt" && (
            <div style={{ marginTop: 6 }}><ERBanner tone="red"><b>Going concern:</b> the 10-K states substantial doubt about the entity's ability to continue — Liquidity +2.5.</ERBanner></div>
          )}
          {data.icfr.going_concern?.status === "alleviated" && (
            <div style={{ marginTop: 6 }}><ERBanner tone="amber">Going-concern doubt was raised and alleviated by management's plans — Liquidity +0.8.</ERBanner></div>
          )}
        </div>
      )}

      {data.item_1c && (
        <div style={{ marginTop: 8 }}>
          <div className="kicker" style={{ marginBottom: 4 }}>10-K Item 1C — Cybersecurity risk management &amp; governance</div>
          {!data.item_1c.found ? (
            <ERBanner tone="amber">No Item 1C section was found by the parser (required for fiscal years ending on or after 2023-12-15). Verify manually — +0.4 on Cybersecurity.</ERBanner>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              {[["oversight", "Board oversight"], ["exec_role", "CISO / security leader"], ["incident_process", "Incident response"],
                ["third_party", "Third-party risk"], ["independent_assessment", "Independent testing"]].map(([k, label]) => (
                <ERChip key={k} tone={data.item_1c.checks[k] ? "grey" : "amber"}>{data.item_1c.checks[k] ? "✓" : "✗"} {label}</ERChip>
              ))}
              {data.item_1c.checks.incident_disclosed && <ERChip tone="red">Discloses a cyber incident</ERChip>}
            </div>
          )}
        </div>
      )}
    </div>
  );
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
          <b>No usable data — nothing could be assessed.</b> Every category is “Not assessed”.
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

      <RiskFactorChanges data={r.governance?.riskFactors} />

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


// ── Always-visible decision basis (top of Gate 1 / Gate 2) ────────────────
const SAY_ON_PAY_NOTE = "Advisory vote, non-binding. Fail <50%; ISS expects board responsiveness <70%; Glass Lewis <80%.";

function Stat({ label, value, tone = "grey", sub }) {
  const t = ER_TONE[tone];
  return (
    <div style={{ border: `1px solid ${t.bd}`, background: t.bg, borderRadius: 6, padding: "6px 10px", minWidth: 110 }}>
      <div style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: t.ink }}>{value}</div>
      {sub && <div style={{ fontSize: 10.5, color: "var(--ink-3)" }}>{sub}</div>}
    </div>
  );
}

function GovernanceCard({ governance }) {
  const g = governance || {};
  const k = g.kris, br = g.boardRotation, sop = g.sayOnPay;
  const pv = k?.policy_values || {};
  const pol = k?.policies || {};
  const parsed = k && k.n_tenure_parsed >= 3;
  const buckets = k?.tenure_buckets;
  const bucketMax = buckets ? Math.max(1, ...Object.values(buckets)) : 1;
  const policyChips = [
    pv.retirement_age ? `Retirement age ${pv.retirement_age}` : pol.mandatory_retirement_age ? "Retirement age policy" : null,
    pv.term_limit_years ? `Term limit ${pv.term_limit_years} yrs` : pol.term_limit ? "Term limit policy" : null,
    pol.board_refreshment ? "Refreshment policy" : null,
    pol.annual_board_evaluation ? "Annual board evaluation" : null,
    pv.classified_board ? "Classified board" : null,
  ].filter(Boolean);
  const stale = parsed && k.years_since_last_addition >= 5;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 10 }}>
      <div style={{ border: "1px solid var(--line, #e4e4e7)", borderRadius: 6, padding: "8px 10px" }}>
        <div className="kicker" style={{ marginBottom: 4 }}>Board rotation · DEF 14A → Governance</div>
        {!parsed ? (
          <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
            Not assessed from the proxy — {k ? `only ${k.n_tenure_parsed} director tenure(s) could be parsed (needs ≥3)` : "no DEF 14A was retrieved"}. Review board tenure manually.
          </div>
        ) : (
          <div style={{ fontSize: 11.5, lineHeight: 1.5 }}>
            Avg tenure <b>{k.avg_tenure_years} yrs</b> · longest <b>{k.max_tenure_years}</b> · newest director <b style={{ color: stale ? "var(--red-ink, #b42318)" : undefined }}>{k.years_since_last_addition} yr(s)</b><br />
            {Math.round(k.share_joined_5y * 100)}% joined in the last 5 yrs · {Math.round(k.share_over_10y * 100)}% serving ≥10 yrs
            {k.avg_age != null && <> · avg age {k.avg_age}</>}
            {pv.independence && <> · independence {pv.independence.independent}/{pv.independence.total}</>}
            <div style={{ display: "flex", gap: 4, alignItems: "flex-end", height: 34, margin: "6px 0 2px" }} title="Directors by years of service">
              {Object.entries(buckets).map(([b, n]) => (
                <div key={b} style={{ flex: 1, textAlign: "center", fontSize: 9.5, color: "var(--ink-3)" }}>
                  <div style={{ height: Math.max(2, (n / bucketMax) * 22), background: b === "10-14" || b === "15+" ? "var(--amber-ink, #b54708)" : "var(--ink-4, #a1a1aa)", borderRadius: 2 }} />
                  {b}y · {n}
                </div>
              ))}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
              {policyChips.length ? policyChips.map(c => <ERChip key={c}>{c}</ERChip>) : <ERChip tone={k.avg_tenure_years >= 9 ? "amber" : "grey"}>No refreshment policy found</ERChip>}
              {k.over_retirement_age > 0 && <ERChip tone="amber">{k.over_retirement_age} at/above retirement age</ERChip>}
            </div>
            <div style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 4 }}>
              Parsed {k.n_tenure_parsed} directors ({k.confidence} confidence). No SEC / exchange rule sets term limits; these are investor-policy signals.
            </div>
          </div>
        )}
      </div>

      <div style={{ border: "1px solid var(--line, #e4e4e7)", borderRadius: 6, padding: "8px 10px" }}>
        <div className="kicker" style={{ marginBottom: 4 }}>Board changes · 8-K Item 5.02 (24 mo)</div>
        {br ? (
          <div style={{ fontSize: 11.5, lineHeight: 1.5 }}>
            Director departures: <b>{br.departures_12m}</b> in 12 mo · <b>{br.departures_24m}</b> in 24 mo<br />
            Director appointments: <b>{br.appointments_12m}</b> in 12 mo · <b>{br.appointments_24m}</b> in 24 mo
            {br.departures_12m >= 3 && <div style={{ color: "var(--red-ink, #b42318)" }}>Elevated departures — board instability signal</div>}
            {br.departures_24m === 0 && br.appointments_24m === 0 && <div style={{ color: "var(--ink-3)" }}>No director-only changes in the filings retrieved.</div>}
          </div>
        ) : <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>No 8-K data retrieved.</div>}
      </div>

      <div style={{ border: "1px solid var(--line, #e4e4e7)", borderRadius: 6, padding: "8px 10px" }}>
        <div className="kicker" style={{ marginBottom: 4 }}>Say-on-pay · 8-K Item 5.07 / proxy</div>
        <div style={{ fontSize: 11.5, lineHeight: 1.5 }}>
          {sop ? <><b>{sop.rationale}</b> <span style={{ color: "var(--ink-3)" }}>(vote reported {sop.date})</span></>
               : k?.say_on_pay_prior != null ? <>Last annual meeting support <b>{Math.round(k.say_on_pay_prior * 100)}%</b> <span style={{ color: "var(--ink-3)" }}>(per proxy)</span></>
               : <span style={{ color: "var(--ink-3)" }}>No say-on-pay result found in the 8-K filings or proxy retrieved — not evaluated.</span>}
          <div style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 4 }}>{SAY_ON_PAY_NOTE}</div>
        </div>
      </div>
    </div>
  );
}

function DecisionBasis({ routing, notAssessed = [], risks = [], gateNum = 1, compact = false }) {
  const r = routing || {};
  const events = r.events || [];
  const flags = (r.gateFlags || []).filter(f => f.flag !== "forces_gate_signoff");
  const forced = (r.gateFlags || []).filter(f => f.flag === "forces_gate_signoff");
  const unacked = forced.filter(f => !(r.acks || {})[f.accession]).length;
  const counts = risks.reduce((a, x) => { a[x.rag] = (a[x.rag] || 0) + 1; return a; }, {});
  const mapped = events.filter(e => e.mappingStatus === "mapped").length;
  const review = events.filter(e => e.mappingStatus === "review").length;
  return (
    <div style={{ border: "1px solid var(--ink-4, #a1a1aa)", borderRadius: 8, padding: "10px 12px", display: "flex", flexDirection: "column", gap: 10, marginBottom: 12 }}
         data-screen-label={`HITL · Decision basis (Gate ${gateNum})`}>
      <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div>
          <div className="kicker">Decision basis · how these scores were made</div>
          <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2, maxWidth: 720 }}>
            Each category is scored from this entity's own evidence:{" "}
            <b>score = impact × likelihood</b>, likelihood = 1.0 + Σ evidence points (SEC 8-Ks routed by item, proxy board KRIs, SEC comment letters, XBRL ratios, RSS, FRED).
            A category with no evidence is <b>Not assessed</b>, not given a default.
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Stat label="Scored" value={risks.length} sub={`${counts.R || 0} R · ${counts.A || 0} A · ${counts.G || 0} G`} />
          <Stat label="Not assessed" value={notAssessed.length} tone={notAssessed.length ? "amber" : "grey"} sub="coverage gaps" />
          <Stat label="8-K routed" value={mapped} sub={`${review} review · ${(r.unrouted || []).length} not scored`} />
          <Stat label="10-K 1A" value={r.governance?.riskFactors?.status === "ok" ? (r.governance.riskFactors.changes || []).length : "—"} sub={r.governance?.riskFactors?.status === "ok" ? "changed factors" : "not compared"} />
          <Stat label="Flags" value={flags.length + forced.length} tone={unacked ? "red" : (flags.length ? "amber" : "grey")} sub={unacked ? `${unacked} need sign-off` : "none open"} />
        </div>
      </div>
      {!compact && <GovernanceCard governance={r.governance} />}
      {compact && flags.length > 0 && (
        <ERBanner tone="amber">{flags.length} event flag(s) were raised at Gate 1 (e.g. {flags.map(f => f.flag.replace(/_/g, " ")).slice(0, 3).join(", ")}) — review before scoping.</ERBanner>
      )}
    </div>
  );
}

// ── Inline per-risk drivers (visible without expanding the row) ──────────
function RiskDrivers({ risk, max = 3 }) {
  const ev = (risk?.evidence || []).filter(e => e.points !== 0).slice().sort((a, b) => Math.abs(b.points) - Math.abs(a.points)).slice(0, max);
  if (!ev.length) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
      {ev.map((e, i) => (
        <span key={i} title={`${e.source}: ${e.label}`} style={{ fontSize: 10, padding: "1px 6px", borderRadius: 8, background: "var(--bg-2, #f4f4f5)", border: "1px solid var(--line, #d4d4d8)", color: "var(--ink-2)", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          <b style={{ color: e.points > 0 ? "var(--red-ink, #b42318)" : "var(--green-ink, #067647)" }}>{e.points > 0 ? "+" : ""}{e.points}</b> {e.source}: {e.label}
        </span>
      ))}
    </div>
  );
}

// ── Expanded per-risk derivation (formula + every evidence item) ─────────
function RiskWhy({ risk }) {
  if (!risk?.evidence?.length) return null;
  return (
    <div style={{ padding: "6px 4px", fontSize: 11.5 }} onClick={(e) => e.stopPropagation()}>
      <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)", marginBottom: 4 }}>
        WHY THIS SCORE — impact {risk.impact} × likelihood {risk.likelihood} = {risk.score}
        {risk.base != null && risk.base !== risk.score && ` (structural base ${risk.base}; recent-change evidence adds ${(risk.score - risk.base).toFixed(1)})`}
      </div>
      <ul style={{ margin: "0 0 0 16px" }}>
        {risk.evidence.map((e, i) => (
          <li key={i}><ERChip>{e.source}</ERChip> {e.label}{" "}
            <b style={{ color: e.points > 0 ? "var(--red-ink, #b42318)" : "var(--ink-3)" }}>{e.points > 0 ? `+${e.points}` : e.points}</b>
            {e.trend && <span style={{ color: "var(--ink-3)" }}> · recent change</span>}</li>
        ))}
      </ul>
    </div>
  );
}

Object.assign(window, { EvidenceRoutingReview, DecisionBasis, RiskDrivers, RiskWhy });
export { EvidenceRoutingReview, DecisionBasis, RiskDrivers, RiskWhy };
