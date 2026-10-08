/* ============================================================
   SLA Tracker — ITSM/Jira-ServiceNow ticket SLA aging + reopen rate.

   itsm_endpoints.py / db.py's breach detection (itsm_sla_sweep.py, hourly)
   already existed with no screen of its own (see itsm_endpoints.py's own
   comment: "no dedicated nav item for ticket tracking yet"). This is that
   screen: aging buckets computed here from sla_due_at/status (the backend
   has no bucket endpoint, just the raw rows), plus reopened_count — a
   ticket resolved then reopened is a rework signal the backend now tracks
   per-ticket (db.update_itsm_ticket_status) but nothing displayed before.

   Data: GET /api/mcp/itsm/tickets (window.MCP.itsmListTickets).
   ============================================================ */

const _SLA_BUCKETS = [
  { id: "breached_72", label: "Breached >72h", test: (ageHrs) => ageHrs > 72, color: "var(--red-ink)" },
  { id: "breached_24", label: "Breached 24–72h", test: (ageHrs) => ageHrs > 24, color: "var(--red-ink)" },
  { id: "breached_0", label: "Breached <24h", test: () => true, color: "var(--amber-ink)" },
];

// Returns { bucket: label, ageHrs } for a breached ticket, or null for one
// that isn't (open-and-on-time, or already resolved before breach fired).
function _slaBucketFor(t) {
  if (!t.sla_breached_at || ["closed", "cancelled"].includes(t.status)) return null;
  const ageHrs = (Date.now() - new Date(t.sla_breached_at).getTime()) / 3_600_000;
  const bucket = _SLA_BUCKETS.find(b => b.test(ageHrs)) || _SLA_BUCKETS[_SLA_BUCKETS.length - 1];
  return { ...bucket, ageHrs };
}

function _fmtAge(hrs) {
  if (hrs < 24) return `${Math.round(hrs)}h`;
  return `${(hrs / 24).toFixed(1)}d`;
}

function SlaTicketRow({ t }) {
  const bucket = _slaBucketFor(t);
  return (
    <tr style={{ borderBottom: "1px solid var(--line-2)" }}>
      <td style={{ padding: "7px 8px", fontFamily: "var(--mono)", fontSize: 10.5 }}>
        {t.external_system}:{t.external_ticket_key}
      </td>
      <td style={{ padding: "7px 8px", maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {t.summary || "(no summary)"}
      </td>
      <td style={{ padding: "7px 8px" }}>
        <span style={{ fontSize: 10, fontWeight: 700, color: t.severity === "CRITICAL" || t.severity === "HIGH" ? "var(--red-ink)" : "var(--ink-2)" }}>
          {t.severity}
        </span>
      </td>
      <td style={{ padding: "7px 8px", textTransform: "capitalize" }}>{t.status.replace("_", " ")}</td>
      <td style={{ padding: "7px 8px" }}>
        {bucket ? (
          <span style={{ fontSize: 10.5, fontWeight: 700, color: bucket.color }}>
            {bucket.label} ({_fmtAge(bucket.ageHrs)})
          </span>
        ) : t.sla_due_at ? (
          <span style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
            Due {new Date(t.sla_due_at).toLocaleDateString()}
          </span>
        ) : "—"}
      </td>
      <td style={{ padding: "7px 8px", textAlign: "center" }}>
        {t.reopened_count > 0 ? (
          <span title="Resolved, then reopened" style={{
            fontSize: 10, fontWeight: 700, color: "var(--amber-ink)", background: "var(--amber-soft)",
            borderRadius: 999, padding: "1px 8px",
          }}>↺ {t.reopened_count}</span>
        ) : <span style={{ color: "var(--ink-4)" }}>—</span>}
      </td>
    </tr>
  );
}

function SlaTrackerScreen({ onNavigate } = {}) {
  const { useState, useEffect, useMemo } = React;
  const [tickets, setTickets] = useState(null);
  const [error, setError] = useState(null);
  const [statusFilter, setStatusFilter] = useState("");

  function load() {
    window.MCP.itsmListTickets({ status: statusFilter || null, limit: 500 })
      .then(d => { setTickets(d.tickets || []); setError(null); })
      .catch(e => setError(e.message || String(e)));
  }
  useEffect(load, [statusFilter]);

  const rows = tickets || [];
  const open = rows.filter(t => !["closed", "cancelled"].includes(t.status));
  const buckets = useMemo(() => {
    const counts = { breached_0: 0, breached_24: 0, breached_72: 0 };
    open.forEach(t => { const b = _slaBucketFor(t); if (b) counts[b.id] = (counts[b.id] || 0) + 1; });
    return counts;
  }, [open]);
  const totalBreached = buckets.breached_0 + buckets.breached_24 + buckets.breached_72;
  const reopened = open.filter(t => t.reopened_count > 0);
  const resolved = rows.filter(t => t.resolved_at);
  const reopenRate = resolved.length ? reopened.length / resolved.length : null;

  // Breached-longest first, then everything else newest first.
  const sorted = [...open].sort((a, b) => {
    const ba = _slaBucketFor(a), bb = _slaBucketFor(b);
    if (ba && bb) return bb.ageHrs - ba.ageHrs;
    if (ba) return -1;
    if (bb) return 1;
    return new Date(b.created_at) - new Date(a.created_at);
  });

  return (
    <div style={{ padding: "0 20px 32px", maxWidth: 1100 }}>
      <div className="panel-head" style={{ paddingLeft: 0, paddingRight: 0 }}>
        <div>
          <div className="kicker">Operations Intelligence</div>
          <div className="panel-title mt-8">SLA Tracker</div>
          <div className="panel-sub">
            Open Jira/ServiceNow tickets tracked for remediation SLA, aged by how long each has been breached,
            and how often a ticket bounces back open after being marked resolved.
          </div>
        </div>
        <select className="input" style={{ width: 160 }} value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
          <option value="">All open statuses</option>
          <option value="open">Open</option>
          <option value="in_progress">In progress</option>
          <option value="resolved">Resolved</option>
        </select>
      </div>

      {error && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 10 }}>{error}</div>}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 20 }}>
        {[
          { label: "Open tickets", value: open.length },
          { label: "Breached, total", value: totalBreached, color: totalBreached ? "var(--red-ink)" : "var(--green-ink)" },
          { label: "Breached >72h", value: buckets.breached_72, color: buckets.breached_72 ? "var(--red-ink)" : undefined },
          { label: "Reopen rate", value: reopenRate == null ? "—" : `${(reopenRate * 100).toFixed(0)}%`, sub: `${reopened.length}/${resolved.length} resolved tickets reopened` },
        ].map(s => (
          <div key={s.label} style={{ flex: "1 1 160px", minWidth: 150, background: "var(--surface-2)",
            border: "1px solid var(--line)", borderRadius: 8, padding: "10px 14px" }}>
            <div style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4 }}>{s.label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: s.color }}>{s.value}</div>
            {s.sub && <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 2 }}>{s.sub}</div>}
          </div>
        ))}
      </div>

      {!tickets ? (
        <div style={{ padding: 20, color: "var(--ink-3)", fontSize: 11.5 }}>Loading…</div>
      ) : !sorted.length ? (
        <Empty>No open tickets tracked. Tickets are opened via POST /itsm/tickets against a finding, or synced from Jira/ServiceNow webhooks.</Empty>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--line)" }}>
              {["Ticket", "Summary", "Severity", "Status", "SLA", "Reopened"].map(h => (
                <th key={h} style={{ textAlign: h === "Reopened" ? "center" : "left", padding: "6px 8px", color: "var(--ink-3)", fontSize: 10, fontWeight: 500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map(t => <SlaTicketRow key={t.id} t={t} />)}
          </tbody>
        </table>
      )}
    </div>
  );
}

Object.assign(window, { SlaTrackerScreen });
