/* ============================================================
   Concept Link Review — reviews the ontology's Stage 3 entity-linking
   proposals (concept_linking.py): free-text risks/controls the ANN linker
   matched to a concept, awaiting a human confirm/reject before that link
   can affect anything downstream (Stage 4's hybrid retrieval re-rank).
   Deliberately its own screen, not folded into Approval Inbox — a
   concept_link is system-proposed, not submitted by a preparer for their
   manager to check, so it doesn't fit that preparer -> manager lifecycle
   (see concept_linking.py's module docstring). Same "approvals" backend
   permission, though — see app.jsx's ScreenAccessGate for this screen.
   ============================================================ */

const SCHEME_LABEL = {
  risk_category: "Risk Category",
  enterprise_domain: "Enterprise Domain",
  coso_erm: "COSO ERM 2017",
  coso_icif: "COSO IC-IF 2013",
  soc2: "SOC 2",
  nist_800_53: "NIST 800-53",
  iso_27001: "ISO 27001",
  sox_risk_category: "SOX Risk Category",
};

const METHOD_LABEL = {
  ann: "ANN match",
  llm_domain: "LLM domain",
};

function ConceptLinkItem({ item, onDecide }) {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);

  async function decide(decision) {
    setBusy(true); setErr(null);
    try {
      await onDecide(item.id, decision);
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  }

  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "14px 16px", marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 240 }}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.07em", marginBottom: 4 }}>
            {SCHEME_LABEL[item.scheme] || item.scheme} · {METHOD_LABEL[item.method] || item.method}
          </div>
          <div className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)", marginBottom: 4 }}>
            {item.source_table}:{item.source_id}
          </div>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)" }}>
            → {item.pref_label} <span style={{ fontWeight: 400, color: "var(--ink-3)", fontSize: 11.5 }}>({item.confidence?.toFixed(3)} confidence)</span>
          </div>
          {item.runner_up_pref_label && (
            <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}>
              Runner-up: <b style={{ color: "var(--ink-2)" }}>{item.runner_up_pref_label}</b> — this match was ambiguous, review carefully.
            </div>
          )}
        </div>
      </div>

      {err && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginTop: 8 }}>{err}</div>}

      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button className="btn btn-sm btn-approve" disabled={busy} onClick={() => decide("confirmed")}>
          <Icon name="check" size={11} /> Confirm
        </button>
        <button className="btn btn-sm" disabled={busy} onClick={() => decide("rejected")}
          style={{ color: "var(--red-ink)" }}>
          <Icon name="x" size={11} /> Reject
        </button>
      </div>
    </div>
  );
}

function ConceptLinkReviewScreen() {
  const [items, setItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const hasLoadedRef = React.useRef(false);

  const reload = React.useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const res = await fetch("/api/ontology/links/pending", { credentials: "include" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setItems(data.links || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
      hasLoadedRef.current = true;
    }
  }, []);

  React.useEffect(() => { reload(); }, [reload]);

  async function handleDecide(linkId, decision) {
    const res = await fetch(`/api/ontology/links/${linkId}/decide`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    if (!res.ok) throw new Error(await res.text());
    setItems(prev => prev.filter(i => i.id !== linkId));
  }

  return (
    <div className="scope-screen" data-screen-label="Concept Link Review">
      <div className="panel-head">
        <div>
          <div className="kicker">Ontology · Entity Linking</div>
          <div className="panel-title mt-8">Concept Link Review</div>
          <div className="panel-sub">
            Risks and controls the ontology's entity linker matched to a concept by embedding similarity, awaiting
            confirmation. A link only affects retrieval re-ranking once confirmed here — proposed and rejected
            links are never treated as authoritative.
          </div>
        </div>
        <button className="btn btn-sm" onClick={reload} disabled={loading}>
          <Icon name="reset" size={11} /> Refresh
        </button>
      </div>

      {error && (
        <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", background: "var(--red-soft)", padding: "6px 10px", borderRadius: 4, marginBottom: 12 }}>{error}</div>
      )}

      {loading && !hasLoadedRef.current ? (
        <div style={{ padding: "24px 0", textAlign: "center", color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>
      ) : items.length === 0 ? (
        <Empty>Nothing awaiting review right now.</Empty>
      ) : (
        <div>
          {items.map(item => <ConceptLinkItem key={item.id} item={item} onDecide={handleDecide} />)}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { ConceptLinkReviewScreen });
