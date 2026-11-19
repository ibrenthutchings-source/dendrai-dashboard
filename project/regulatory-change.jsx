/* ============================================================
   Regulatory Change Management — horizon scanning + HITL review.

   Watches four regulatory feeds (EU AI Act, DORA, NIS2, US state privacy —
   rss_ingest_service.FEEDS) for material changes to their published content,
   diffs each change against the last snapshot this system saw
   (regulatory_change_tool.py), and drafts a proposed control-register edit
   for a human to approve or reject. Nothing reaches the Risk & Control
   Ledger without an explicit decision — same "no ungrounded generation
   reaches the register" discipline Policy-as-Code's prose-to-Rego pipeline
   already enforces, applied here to regulatory text instead of uploaded
   policy documents.

   Two tabs:
     - Review Queue   — pending proposals: diff, suggested control edit, approve/reject
     - Scan History   — recent fetched snapshots per feed, for audit trail

   Data: window.MCP.regChange* (mcp-data.js) -> /regulatory-change/*
   (regulatory_change_endpoints.py).
   ============================================================ */

const _REGCHANGE_STATUS_STYLE = {
  pending_review: { bg: "var(--amber-soft)", ink: "var(--amber-ink)", label: "Pending review" },
  approved:       { bg: "var(--green-soft)", ink: "var(--green-ink)", label: "Approved" },
  rejected:       { bg: "var(--red-soft)",   ink: "var(--red-ink)",   label: "Rejected" },
};

function RegChangeStatusBadge({ status }) {
  const s = _REGCHANGE_STATUS_STYLE[status] || _REGCHANGE_STATUS_STYLE.pending_review;
  return (
    <span className="mono" style={{ fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: s.bg, color: s.ink, whiteSpace: "nowrap" }}>
      {s.label}
    </span>
  );
}

// Diff lines rendered with +/- coloring — same "the diff IS the evidence"
// framing pac_policy_docs.py's generated_rego/draft_rego split uses, just
// rendered as a real unified diff instead of a toggle between two textareas.
function DiffView({ diff }) {
  if (!diff) return <span style={{ fontSize: 10, color: "var(--ink-4)" }}>No diff captured.</span>;
  const lines = diff.split("\n");
  return (
    <div className="mono" style={{
      fontSize: 10.5, lineHeight: 1.6, background: "var(--surface-2)", border: "1px solid var(--line)",
      borderRadius: 6, padding: "10px 12px", maxHeight: 260, overflow: "auto", whiteSpace: "pre-wrap",
    }}>
      {lines.map((line, i) => {
        const color = line.startsWith("+") && !line.startsWith("+++") ? "var(--green-ink)"
          : line.startsWith("-") && !line.startsWith("---") ? "var(--red-ink)"
          : "var(--ink-3)";
        return <div key={i} style={{ color }}>{line || " "}</div>;
      })}
    </div>
  );
}

function ProposalRow({ proposal, onDecided }) {
  const [expanded, setExpanded] = React.useState(false);
  const [notes, setNotes] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState(null);

  async function decide(decision) {
    setBusy(true);
    setError(null);
    try {
      await window.MCP.regChangeDecide(proposal.id, decision, notes.trim() || null);
      onDecided(proposal.id);
    } catch (e) {
      setError(e.message || String(e));
      setBusy(false);
    }
  }

  const edit = proposal.proposed_edit || {};
  const pending = proposal.status === "pending_review";

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "12px 14px", marginBottom: 10, background: "var(--surface)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, cursor: "pointer" }}
        onClick={() => setExpanded(e => !e)}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--ink)" }}>
            {edit.name || proposal.title || "Untitled feed"}
          </div>
          <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 2 }}>
            {proposal.feed_id} · {proposal.proposed_control_ref ? `→ ${proposal.proposed_control_ref}` : "→ new control"}
            {proposal.created_at && <> · {new Date(proposal.created_at).toLocaleString()}</>}
          </div>
        </div>
        <RegChangeStatusBadge status={proposal.status} />
      </div>

      {expanded && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--line)" }}>
          {edit.summary && (
            <div style={{ fontSize: 11.5, color: "var(--ink-2)", marginBottom: 8, lineHeight: 1.5 }}>{edit.summary}</div>
          )}
          <div className="kicker" style={{ fontSize: 9.5, marginBottom: 4 }}>Suggested control edit</div>
          <div style={{ fontSize: 11, color: "var(--ink)", marginBottom: 10, padding: "8px 10px", borderRadius: 5, background: "var(--surface-2)" }}>
            {edit.description || "—"}
          </div>
          <div className="kicker" style={{ fontSize: 9.5, marginBottom: 4 }}>What changed</div>
          <DiffView diff={proposal.diff_summary} />

          {pending ? (
            <>
              <textarea className="code-input" rows={2} placeholder="Review notes (optional)…"
                value={notes} onChange={e => setNotes(e.target.value)}
                style={{ width: "100%", fontSize: 11, marginTop: 10, marginBottom: 8, resize: "vertical" }} />
              {error && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 8 }}>{error}</div>}
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn btn-sm btn-acc" disabled={busy} onClick={() => decide("approved")}>
                  {busy ? "Applying…" : "Approve — apply to control register"}
                </button>
                <button className="btn btn-sm" disabled={busy} onClick={() => decide("rejected")} style={{ color: "var(--red-ink)" }}>
                  Reject
                </button>
              </div>
            </>
          ) : (
            <div style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 8 }}>
              {proposal.reviewer && <>Decided by <b>{proposal.reviewer}</b></>}
              {proposal.reviewed_at && <> · {new Date(proposal.reviewed_at).toLocaleString()}</>}
              {proposal.review_notes && <div style={{ marginTop: 4 }}>{proposal.review_notes}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ReviewQueueTab() {
  const [proposals, setProposals] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [scanning, setScanning] = React.useState(false);
  const [scanResult, setScanResult] = React.useState(null);
  const [error, setError] = React.useState(null);

  const load = React.useCallback(() => {
    setLoading(true);
    return window.MCP.regChangeProposals("pending_review")
      .then(d => { setProposals(d.proposals || []); setError(null); })
      .catch(e => setError(e.message || String(e)))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => { load(); }, [load]);

  async function handleScan() {
    setScanning(true);
    setScanResult(null);
    try {
      const { results } = await window.MCP.regChangeScan();
      const created = (results || []).filter(r => r.status === "proposal_created").length;
      setScanResult(`Scan complete — ${created} new proposal${created !== 1 ? "s" : ""} from ${results.length} feed(s).`);
      await load();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setScanning(false);
    }
  }

  function handleDecided(proposalId) {
    setProposals(ps => ps.filter(p => p.id !== proposalId));
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div className="kicker">{proposals.length} awaiting review</div>
        <button className="btn btn-sm" onClick={handleScan} disabled={scanning}>
          {scanning ? "Scanning…" : "Scan feeds now"}
        </button>
      </div>
      {scanResult && <div className="mono" style={{ fontSize: 10.5, color: "var(--acc-ink)", marginBottom: 10 }}>{scanResult}</div>}
      {error && <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 10 }}>{error}</div>}
      {loading && !proposals.length ? (
        <Empty>Loading…</Empty>
      ) : !proposals.length ? (
        <Empty icon="✓">
          Nothing awaiting review. Click "Scan feeds now" to check the EU AI Act, DORA, NIS2, and US state-privacy
          feeds for changes since the last scan.
        </Empty>
      ) : (
        proposals.map(p => <ProposalRow key={p.id} proposal={p} onDecided={handleDecided} />)
      )}
    </div>
  );
}

function ScanHistoryTab() {
  const [versions, setVersions] = React.useState([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    window.MCP.regChangeVersions()
      .then(d => setVersions(d.versions || []))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Empty>Loading…</Empty>;
  if (!versions.length) return <Empty>No feeds scanned yet — run a scan from the Review Queue tab.</Empty>;

  return (
    <div>
      {versions.map(v => (
        <div key={v.id} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--line)", fontSize: 11 }}>
          <div>
            <div style={{ fontWeight: 600 }}>{v.title || v.feed_id}</div>
            <div style={{ color: "var(--ink-4)", fontSize: 9.5, marginTop: 1 }}>{v.feed_id} · {v.source_url}</div>
          </div>
          <span className="mono" style={{ color: "var(--ink-3)", whiteSpace: "nowrap" }}>
            {v.fetched_at ? new Date(v.fetched_at).toLocaleString() : "—"}
          </span>
        </div>
      ))}
    </div>
  );
}

function RegulatoryChangeScreen() {
  const [tab, setTab] = React.useState("review");

  return (
    <div className="scope-screen" data-screen-label="Regulatory Change Management">
      <div className="panel-head">
        <div>
          <div className="kicker">Automation Intelligence · Horizon Scanning</div>
          <div className="panel-title mt-8">Regulatory Change Management</div>
          <div className="panel-sub">
            Watches the EU AI Act, DORA, NIS2, and US state-privacy monitoring feeds for material content changes,
            diffs each change against the last scan, and drafts a proposed control-register edit. Nothing reaches
            the Risk &amp; Control Ledger without an explicit approve/reject decision — the diff and the suggested
            edit are always reviewed together, never applied silently.
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
        {[["review", "Review Queue"], ["history", "Scan History"]].map(([v, label]) => (
          <button key={v} type="button" onClick={() => setTab(v)}
            style={{
              fontSize: 11, padding: "5px 12px", borderRadius: 5, cursor: "pointer",
              border: v === tab ? "1px solid var(--acc,#2563eb)" : "1px solid var(--line,#ddd)",
              background: v === tab ? "var(--acc,#2563eb)" : "transparent",
              color: v === tab ? "#fff" : "var(--ink-2,#555)",
              fontWeight: v === tab ? 600 : 400,
            }}>
            {label}
          </button>
        ))}
      </div>

      {tab === "review" && <ReviewQueueTab />}
      {tab === "history" && <ScanHistoryTab />}
    </div>
  );
}

Object.assign(window, { RegulatoryChangeScreen });
