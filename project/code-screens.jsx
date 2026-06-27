/* ============================================================
   Risk-as-Code & Policy-as-Code
   Shared split-view editor: declarative rules on the left, a live
   evaluation/diff against the current run on the right.
   ============================================================ */

function CodeEditorScreen({ kicker, title, sub, storageKey, defaultCode, fileLabel, renderEval }) {
  const [code, setCode] = useState(() => {
    try { return localStorage.getItem(storageKey) || defaultCode; } catch { return defaultCode; }
  });
  const [savedCode, setSavedCode] = useState(() => {
    try { return localStorage.getItem(storageKey) || ""; } catch { return ""; }
  });
  const [status, setStatus] = useState(null);   // { kind, msg }
  const [dbSaved, setDbSaved] = useState(false);
  const dirty = code !== savedCode;

  // Load from DB on mount — DB is authoritative; overwrites stale localStorage copy
  useEffect(() => {
    fetch(`/api/config/code-editor/${encodeURIComponent(storageKey)}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.content) {
          setCode(data.content);
          setSavedCode(data.content);
          try { localStorage.setItem(storageKey, data.content); } catch {}
          setDbSaved(true);
        }
      })
      .catch(() => {});
  }, [storageKey]);

  function validate() {
    // Lightweight structural check — non-empty, balanced-ish indentation.
    const lines = code.split("\n").filter(l => l.trim());
    if (!lines.length) { setStatus({ kind: "err", msg: "Document is empty." }); return false; }
    const tabs = code.includes("\t");
    if (tabs) { setStatus({ kind: "err", msg: "Use spaces, not tabs, for indentation." }); return false; }
    setStatus({ kind: "ok", msg: `Valid · ${lines.length} rule line(s) parsed.` });
    return true;
  }

  async function save() {
    if (!validate()) return;
    try { localStorage.setItem(storageKey, code); } catch {}
    setSavedCode(code);
    try {
      const r = await fetch(`/api/config/code-editor/${encodeURIComponent(storageKey)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: code }),
      });
      const data = r.ok ? await r.json() : null;
      if (data?.saved) {
        setDbSaved(true);
        setStatus({ kind: "ok", msg: "Saved to DB." });
      } else {
        setStatus({ kind: "ok", msg: "Saved locally." });
      }
    } catch {
      setStatus({ kind: "ok", msg: "Saved locally." });
    }
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
          {dbSaved && !dirty && (
            <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>DB ✓</span>
          )}
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
  red:   15.0
  amber: 9.0

adjustments:
  - when: category contains "macro"
    per_signal: +0.20          # each contractionary FRED signal
  - when: rss.linked
    add: velocity * 0.20       # signals linked to the risk
  - when: rss.industry.high_velocity   # velocity >= 3
    add: +0.125
    cap: +0.50

control_effectiveness:
  none: 4.5
  weak: 3.5
  adequate: 2.5
  strong: 1.5
`;

function risksToRaC(risks) {
  const now   = new Date().toISOString().split("T")[0];
  const red   = risks.filter(r => r.rag === "R").length;
  const amber = risks.filter(r => r.rag === "A").length;
  const green = risks.filter(r => r.rag === "G").length;

  const lines = [
    `# Risk Register — Risk-as-Code`,
    `# Generated: ${now}  ·  ${risks.length} risks  (${red} red · ${amber} amber · ${green} green)`,
    ``,
    `thresholds:`,
    `  red:   15.0`,
    `  amber:  9.0`,
    ``,
    `scoring:`,
    `  scale: 25    # impact (0-5) × likelihood (0-5)`,
    ``,
    `risks:`,
  ];

  const sorted = [...risks].sort((a, b) => b.score - a.score);
  for (const r of sorted) {
    const vel = r.velocity >= 0 ? `+${r.velocity}` : `${r.velocity}`;
    const name = (r.name || "").replace(/"/g, '\\"');
    lines.push(`  - id:                    ${r.id}`);
    lines.push(`    name:                  "${name}"`);
    lines.push(`    category:              ${r.category || "—"}`);
    lines.push(`    rag:                   ${r.rag}`);
    lines.push(`    score:                 ${Number(r.score).toFixed(1)}   # out of 25`);
    lines.push(`    likelihood:            ${Number(r.likelihood).toFixed(1)}`);
    lines.push(`    impact:                ${Number(r.impact).toFixed(1)}`);
    lines.push(`    velocity:              ${vel}`);
    lines.push(`    control_effectiveness: ${r.ce || "—"}`);
    lines.push(`    inherent_score:        ${Number(r.inherent ?? r.score).toFixed(1)}`);
    lines.push(`    residual_score:        ${Number(r.residual ?? r.score).toFixed(1)}`);
    lines.push(`    peer_vs_industry:      ${r.peer || "—"}`);
    lines.push(``);
  }

  return lines.join("\n").trimEnd();
}

function RiskAsCodeScreen({ risks, baseRisks }) {
  const STORAGE_KEY = "dendrai.riskcode";

  const [code, setCode] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY) || RISK_CODE_DEFAULT; } catch { return RISK_CODE_DEFAULT; }
  });
  const [savedCode, setSavedCode] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY) || ""; } catch { return ""; }
  });
  const [status, setStatus] = useState(null);
  const [dbSaved, setDbSaved] = useState(false);
  const dirty = code !== savedCode;

  useEffect(() => {
    fetch(`/api/config/code-editor/${encodeURIComponent(STORAGE_KEY)}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.content) {
          setCode(data.content);
          setSavedCode(data.content);
          try { localStorage.setItem(STORAGE_KEY, data.content); } catch {}
          setDbSaved(true);
        }
      })
      .catch(() => {});
  }, []);

  function validate() {
    const lines = code.split("\n").filter(l => l.trim());
    if (!lines.length) { setStatus({ kind: "err", msg: "Document is empty." }); return false; }
    if (code.includes("\t")) { setStatus({ kind: "err", msg: "Use spaces, not tabs, for indentation." }); return false; }
    setStatus({ kind: "ok", msg: `Valid · ${lines.length} line(s) parsed.` });
    return true;
  }

  async function save() {
    if (!validate()) return;
    try { localStorage.setItem(STORAGE_KEY, code); } catch {}
    setSavedCode(code);
    try {
      const r = await fetch(`/api/config/code-editor/${encodeURIComponent(STORAGE_KEY)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: code }),
      });
      const data = r.ok ? await r.json() : null;
      setDbSaved(!!data?.saved);
      setStatus({ kind: "ok", msg: data?.saved ? "Saved to DB." : "Saved locally." });
    } catch {
      setStatus({ kind: "ok", msg: "Saved locally." });
    }
  }

  function loadFromRegister() {
    if (!risks?.length) return;
    setCode(risksToRaC(risks));
    setStatus({ kind: "ok", msg: `Loaded ${risks.length} risks from the live register.` });
  }

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
    <div className="code-screen" data-screen-label="Risk-as-Code">
      <div className="panel-head">
        <div>
          <div className="kicker">Execution · Risk-as-Code</div>
          <div className="panel-title mt-8">Risk-as-Code</div>
          <div className="panel-sub">
            Load the live register as RaC or edit rules manually. Validate and save —
            evaluation previews scoring against the live register.
          </div>
        </div>
        <div className="code-actions">
          {dbSaved && !dirty && (
            <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>DB ✓</span>
          )}
          {status && <span className={"code-status " + status.kind}>{status.msg}</span>}
          <button className="btn btn-sm" onClick={validate}><Icon name="check" size={11}/> Validate</button>
          <button className="btn btn-sm btn-acc" onClick={save} disabled={!dirty}><Icon name="download" size={11}/> Save</button>
        </div>
      </div>

      <div className="code-split">
        {/* Pane 1 — Evaluation */}
        <div className="code-pane">
          <div className="code-pane-head mono">Evaluation · current run</div>
          <div className="code-eval">{renderEval()}</div>
        </div>

        {/* Pane 2 — RaC YAML editor */}
        <div className="code-pane">
          <div className="code-pane-head mono"
            style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <span>risk-rules.yaml{dirty ? " ●" : ""}</span>
            <button
              className="btn btn-sm"
              onClick={loadFromRegister}
              disabled={!risks?.length}
              title={!risks?.length ? "Run the loop first" : `Load all ${risks?.length} risks as RaC YAML`}
              style={{ padding: "2px 8px", fontSize: 10, flexShrink: 0 }}
            >
              <Icon name="spark" size={10}/> Load from Register
            </button>
          </div>
          <textarea
            className="code-editor mono"
            spellCheck={false}
            value={code}
            onChange={e => setCode(e.target.value)}
          />
        </div>
      </div>
    </div>
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

// ─────────────────────────────────────────────────────────────────────────────
// Risks-as-Code Live Screen
// Translates live pipeline risk signals into OSCAL (NIST) and COSO ERM /
// ISO 31000 artifacts. Subscribes to the backend SSE stream and regenerates
// on every Stage 2 completion. Artifacts can be downloaded as YAML files.
// ─────────────────────────────────────────────────────────────────────────────

const RAC_FRAMEWORKS = [
  {
    id: "oscal",
    label: "OSCAL",
    sub:   "NIST OSCAL 1.1.2 — Assessment Results",
    badge: "NIST",
    desc:  "Open Security Controls Assessment Language. Maps the Dendrai risk register to Assessment Results with findings, risks, remediations, and financial observations.",
    ext:   "yaml",
  },
  {
    id: "coso_erm",
    label: "COSO ERM / ISO 31000",
    sub:   "COSO ERM 2017 · ISO 31000:2018",
    badge: "ERM",
    desc:  "Enterprise Risk Management framework aligned to COSO's five components and ISO 31000's risk treatment clauses (6.4–6.5). Includes HITL-approved scores, MAPs, and governance statement.",
    ext:   "yaml",
  },
];

function RisksAsCodeLiveScreen({ risks, objectives, maps, signals, ratios, ticker, industry, period, runId }) {
  const [activeFramework, setActiveFramework] = useState("oscal");
  const [artifacts, setArtifacts]             = useState({});        // {framework: yaml_str}
  const [streamStatus, setStreamStatus]       = useState("idle");    // idle | connecting | live | done | error
  const [lastUpdated, setLastUpdated]         = useState(null);
  const [generating, setGenerating]           = useState(false);
  const [genError, setGenError]               = useState(null);
  const [riskCount, setRiskCount]             = useState(0);
  const esRef = useRef(null);

  // Close any open SSE connection on unmount
  useEffect(() => () => { esRef.current?.close(); }, []);

  // Auto-generate when risks arrive (no database required)
  useEffect(() => {
    if (risks?.length && !artifacts.oscal) {
      handleGenerate();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [risks?.length]);

  // Open SSE stream whenever a run_id becomes available
  useEffect(() => {
    if (!runId) return;
    esRef.current?.close();
    setStreamStatus("connecting");

    const es = new EventSource(`/api/risks-as-code/stream/${runId}`);
    esRef.current = es;

    es.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "connected") {
          setStreamStatus("live");
        } else if (msg.type === "update") {
          setArtifacts(msg.artifacts || {});
          setRiskCount(msg.risk_count || 0);
          setLastUpdated(new Date().toLocaleTimeString());
          if (msg.completed) { setStreamStatus("done"); es.close(); }
        } else if (msg.type === "done") {
          setStreamStatus("done"); es.close();
        } else if (msg.type === "timeout" || msg.type === "error") {
          setStreamStatus(msg.type === "error" ? "error" : "done"); es.close();
        }
      } catch {}
    };

    es.onerror = () => { setStreamStatus("error"); es.close(); };

    return () => es.close();
  }, [runId]);

  async function handleGenerate() {
    if (!risks?.length) return;
    setGenerating(true);
    setGenError(null);
    try {
      const res = await fetch("/api/risks-as-code/generate", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker:     ticker || "",
          run_id:     runId  || null,
          risks:      risks  || [],
          objectives: objectives || [],
          maps:       maps   || [],
          ratios:     ratios || {},
          signals:    signals || [],
          industry:   industry || "",
          period:     period   || "",
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const next = {};
      for (const [fw, val] of Object.entries(data.artifacts || {})) {
        next[fw] = val.content;
      }
      setArtifacts(next);
      setRiskCount(risks.length);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (err) {
      setGenError(err.message);
    } finally {
      setGenerating(false);
    }
  }

  function handleDownload(fw) {
    const content = artifacts[fw];
    if (!content) return;
    const meta = RAC_FRAMEWORKS.find(f => f.id === fw) || {};
    const blob = new Blob([content], { type: "application/x-yaml" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = `dendrai_${fw}_${ticker || "export"}_${runId || "run"}.${meta.ext || "yaml"}`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const activeArtifact = artifacts[activeFramework] || "";
  const noRisks  = !risks?.length;
  const noArtifact = !activeArtifact;

  const streamBadge = {
    idle:       null,
    connecting: { cls: "rac-stream-badge connecting", label: "Connecting…" },
    live:       { cls: "rac-stream-badge live",       label: `Live · ${riskCount} risks` },
    done:       { cls: "rac-stream-badge done",       label: "Up to date" },
    error:      { cls: "rac-stream-badge error",      label: "Stream error" },
  }[streamStatus];

  return (
    <div className="code-screen" data-screen-label="Risks as Code">
      {/* ── Header ── */}
      <div className="panel-head">
        <div>
          <div className="kicker">Execution · Risks as Code</div>
          <div className="panel-title mt-8">Risks as Code</div>
          <div className="panel-sub">
            Live pipeline signals translated into industry-standard artifacts —
            OSCAL (NIST) and COSO ERM / ISO 31000. Regenerates automatically after Stage 2.
          </div>
        </div>
        <div className="code-actions" style={{ alignItems: "center", gap: 8 }}>
          {streamBadge && <span className={streamBadge.cls}>{streamBadge.label}</span>}
          {lastUpdated && (
            <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
              Updated {lastUpdated}
            </span>
          )}
          {genError && <span className="code-status err">{genError}</span>}
          <button
            className={"btn btn-sm btn-acc" + (generating ? " loading" : "")}
            onClick={handleGenerate}
            disabled={noRisks || generating}
            title={noRisks ? "Run the loop first to load risk data" : "Regenerate artifacts from current pipeline state"}
          >
            <Icon name="spark" size={11} />
            {generating ? " Generating…" : " Generate"}
          </button>
        </div>
      </div>

      {/* ── Framework tabs ── */}
      <div className="rac-tabs">
        {RAC_FRAMEWORKS.map(fw => (
          <button
            key={fw.id}
            className={"rac-tab" + (activeFramework === fw.id ? " active" : "")}
            onClick={() => setActiveFramework(fw.id)}
          >
            <span className="rac-tab-badge">{fw.badge}</span>
            {fw.label}
            {artifacts[fw.id] && <span className="rac-tab-dot" />}
          </button>
        ))}
      </div>

      {/* ── Split: description + code ── */}
      <div className="code-split" style={{ flex: 1, minHeight: 0 }}>
        {/* Left: framework description + download */}
        <div className="code-pane" style={{ maxWidth: 260, minWidth: 200, flex: "0 0 240px" }}>
          {RAC_FRAMEWORKS.filter(f => f.id === activeFramework).map(fw => (
            <div key={fw.id} style={{ padding: "16px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)" }}>{fw.sub}</div>
              <div style={{ fontSize: 11, color: "var(--ink-2)", lineHeight: 1.6 }}>{fw.desc}</div>

              <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                <button
                  className="btn btn-sm"
                  onClick={() => handleDownload(fw.id)}
                  disabled={!artifacts[fw.id]}
                  style={{ justifyContent: "center" }}
                >
                  <Icon name="download" size={11} /> Download .yaml
                </button>

                {runId && db_enabled() && (
                  <a
                    className="btn btn-sm"
                    href={`/api/risks-as-code/export/${runId}/${fw.id}`}
                    target="_blank"
                    rel="noreferrer"
                    style={{ textDecoration: "none", display: "flex", alignItems: "center", gap: 4, justifyContent: "center" }}
                  >
                    <Icon name="doc" size={11} /> From DB
                  </a>
                )}
              </div>

              {activeArtifact && (
                <div className="rac-meta mono" style={{ marginTop: 6, fontSize: 9.5, color: "var(--ink-3)", lineHeight: 1.7 }}>
                  <div>{activeArtifact.split("\n").length} lines</div>
                  <div>{(new Blob([activeArtifact]).size / 1024).toFixed(1)} KB</div>
                  <div>Run #{runId || "—"}</div>
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Right: YAML output */}
        <div className="code-pane" style={{ flex: 1, minWidth: 0 }}>
          <div className="code-pane-head mono" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span>
              {RAC_FRAMEWORKS.find(f => f.id === activeFramework)?.id || activeFramework}.yaml
            </span>
            {activeArtifact && (
              <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--ink-3)" }}>
                {ticker} · {period}
              </span>
            )}
          </div>

          {noRisks && (
            <Empty style={{ padding: 32 }}>
              Run the pipeline to Stage 2 to generate Risks-as-Code artifacts.
            </Empty>
          )}

          {!noRisks && noArtifact && (
            <Empty style={{ padding: 32 }}>
              Click <b>Generate</b> to translate current pipeline risks into {activeFramework.toUpperCase()} format.
            </Empty>
          )}

          {activeArtifact && (
            <textarea
              className="code-editor mono"
              spellCheck={false}
              readOnly
              value={activeArtifact}
              style={{ resize: "none", color: "var(--ink)", caretColor: "transparent" }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function db_enabled() {
  // Heuristic: DB artifacts are available only when the backend is reachable.
  // We don't have a synchronous way to check, so we always show the link and
  // let the server return a 503 if the DB is not configured.
  return true;
}

Object.assign(window, { CodeEditorScreen, RiskAsCodeScreen, PolicyAsCodeScreen, RisksAsCodeLiveScreen });
