/* ============================================================
   Risk-as-Code & Policy-as-Code
   Shared split-view editor: declarative rules on the left, a live
   evaluation/diff against the current run on the right.
   ============================================================ */

function CodeEditorScreen({ kicker, title, sub, storageKey, defaultCode, fileLabel, renderEval }) {
  const [code, setCode] = useState(() => {
    try { return localStorage.getItem(storageKey) || defaultCode; } catch { return defaultCode; }
  });
  const [savedCode, setSavedCode] = useState(code);
  const [status, setStatus] = useState(null);   // { kind, msg }
  const dirty = code !== savedCode;

  function validate() {
    // Lightweight structural check — non-empty, balanced-ish indentation.
    const lines = code.split("\n").filter(l => l.trim());
    if (!lines.length) { setStatus({ kind: "err", msg: "Document is empty." }); return false; }
    const tabs = code.includes("\t");
    if (tabs) { setStatus({ kind: "err", msg: "Use spaces, not tabs, for indentation." }); return false; }
    setStatus({ kind: "ok", msg: `Valid · ${lines.length} rule line(s) parsed.` });
    return true;
  }

  function save() {
    if (!validate()) return;
    try { localStorage.setItem(storageKey, code); } catch {}
    setSavedCode(code);
    setStatus({ kind: "ok", msg: "Saved." });
  }

  return (
    <div className="code-screen" data-screen-label={title}>
      <div className="panel-head">
        <div>
          <div className="kicker">{kicker}</div>
          <div className="panel-title mt-8">{title}</div>
          <div className="panel-sub">{sub}</div>
        </div>
        <div className="code-actions">
          {status && <span className={"code-status " + status.kind}>{status.msg}</span>}
          <button className="btn btn-sm" onClick={validate}><Icon name="check" size={11}/> Validate</button>
          <button className="btn btn-sm btn-acc" onClick={save} disabled={!dirty}><Icon name="download" size={11}/> Save</button>
        </div>
      </div>

      <div className="code-split">
        <div className="code-pane">
          <div className="code-pane-head mono">{fileLabel}{dirty ? " ●" : ""}</div>
          <textarea className="code-editor mono" spellCheck={false}
            value={code} onChange={e => setCode(e.target.value)} />
        </div>
        <div className="code-pane">
          <div className="code-pane-head mono">Evaluation · current run</div>
          <div className="code-eval">{renderEval()}</div>
        </div>
      </div>
    </div>
  );
}

// ---------- RISK-AS-CODE ----------
const RISK_CODE_DEFAULT = `# Risk scoring rules — applied at Stage 2
thresholds:
  red:   7.5
  amber: 5.0

adjustments:
  - when: category contains "macro"
    per_signal: +0.08          # each contractionary FRED signal
  - when: rss.linked
    add: velocity * 0.08       # signals linked to the risk
  - when: rss.industry.high_velocity   # velocity >= 3
    add: +0.05
    cap: +0.20

control_effectiveness:
  none: 9
  weak: 7
  adequate: 5
  strong: 3
`;

function RiskAsCodeScreen({ risks, baseRisks }) {
  const renderEval = () => {
    if (!risks?.length) return <Empty>Run the loop to evaluate rules against the live register.</Empty>;
    const baseById = Object.fromEntries((baseRisks || []).map(r => [r.id, r]));
    let changed = 0;
    const rows = risks.map(r => {
      const b = baseById[r.id];
      const base = b?.score ?? r.score;
      const baseRag = b?.rag ?? r.rag;
      const ragChanged = baseRag !== r.rag;
      if (Math.abs(base - r.score) >= 0.05 || ragChanged) changed++;
      return { r, base, baseRag, ragChanged };
    });
    return (
      <>
        <div className="code-eval-summary mono">
          {changed} of {risks.length} risks change under these rules
        </div>
        {rows.map(({ r, base, baseRag, ragChanged }) => (
          <div className="code-eval-row" key={r.id}>
            <span className={`rag-dot ${r.rag}`} />
            <div className="code-eval-name">
              <b>{r.name}</b>
              <span className="mono code-eval-id">{r.id}</span>
            </div>
            <div className="mono code-eval-delta">
              {base.toFixed(1)} → <b style={{color: scoreColorInk(r.score)}}>{r.score.toFixed(1)}</b>
              {ragChanged && <span className="code-eval-tag">{baseRag}→{r.rag}</span>}
            </div>
          </div>
        ))}
      </>
    );
  };

  return (
    <CodeEditorScreen
      kicker="Execution · Risk-as-Code"
      title="Risk-as-Code"
      sub="Express the risk-scoring model as versioned rules. Edits preview against the live register before save."
      storageKey="dendrai.riskcode"
      fileLabel="risk-rules.yaml"
      defaultCode={RISK_CODE_DEFAULT}
      renderEval={renderEval}
    />
  );
}

// ---------- POLICY-AS-CODE ----------
const POLICY_CODE_DEFAULT = `# Control & governance policies — evaluated against signals
policy "P1 cascade":
  when: event.severity == "P1"
  notify: [owner, mgmt, cae, cfo, board]
  sla_hours: 24

policy "P2 cascade":
  when: event.severity == "P2"
  notify: [owner, mgmt, cae]
  sla_hours: 72

policy "MAP SLA":
  when: map.status == "open" and map.overdue
  action: alert(owner)

policy "Appetite gate":
  when: risk.score >= appetite.threshold
  action: require_hitl(gate=1)
`;

function PolicyAsCodeScreen({ events, maps, risks, appetiteThreshold = 7.5 }) {
  const renderEval = () => {
    const p1 = (events || []).filter(e => e.severity === "P1").length;
    const p2 = (events || []).filter(e => e.severity === "P2").length;
    const openMaps = (maps || []).filter(m => (m.completion_pct || 0) < 100).length;
    const overRisks = (risks || []).filter(r => r.score >= appetiteThreshold);

    const checks = [
      { ok: true,  name: "P1 cascade", detail: `matched ${p1} event${p1 !== 1 ? "s" : ""}` },
      { ok: true,  name: "P2 cascade", detail: `matched ${p2} event${p2 !== 1 ? "s" : ""}` },
      { ok: openMaps === 0, name: "MAP SLA", detail: openMaps === 0 ? "no open MAPs" : `${openMaps} open MAP(s) to monitor` },
      { ok: overRisks.length === 0, name: "Appetite gate", detail: overRisks.length === 0 ? "all risks within tolerance" : `${overRisks.length} risk(s) ≥ ${appetiteThreshold}` },
    ];

    return (
      <>
        <div className="code-eval-summary mono">{checks.length} policies active</div>
        {checks.map((c, i) => (
          <div className="code-eval-row" key={i}>
            <span className={"code-eval-check " + (c.ok ? "ok" : "warn")}>{c.ok ? <Icon name="check" size={10}/> : "!"}</span>
            <div className="code-eval-name"><b>{c.name}</b></div>
            <div className="mono code-eval-detail">{c.detail}</div>
          </div>
        ))}
        {overRisks.length > 0 && (
          <div className="code-eval-note mono">
            Triggered HITL Gate 1 for: {overRisks.map(r => r.id).join(", ")}
          </div>
        )}
      </>
    );
  };

  return (
    <CodeEditorScreen
      kicker="Execution · Policy-as-Code"
      title="Policy-as-Code"
      sub="Codify control-monitoring and governance policies. Evaluation runs against the current events, MAPs, and register."
      storageKey="dendrai.policycode"
      fileLabel="policies.yaml"
      defaultCode={POLICY_CODE_DEFAULT}
      renderEval={renderEval}
    />
  );
}

Object.assign(window, { CodeEditorScreen, RiskAsCodeScreen, PolicyAsCodeScreen });
