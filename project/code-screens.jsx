/* ============================================================
   Risk-as-Code & Policy-as-Code
   Shared split-view editor: declarative rules on the left, a live
   evaluation/diff against the current run on the right.
   ============================================================ */

const _CODE_API_KEY = import.meta.env.VITE_API_KEY || "";
const _codeAuthHeaders = (extra = {}) => ({
  "Content-Type": "application/json",
  ...(_CODE_API_KEY ? { "X-API-Key": _CODE_API_KEY } : {}),
  ...extra,
});

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
        headers: _codeAuthHeaders(),
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
        headers: _codeAuthHeaders(),
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

// ---------- POLICY-AS-CODE (Rego / OPA) ----------

// Fallback shown before GET /api/pac/processes resolves (or if it fails) —
// PolicyAsCodeScreen fetches the real, DB-backed list on mount and replaces
// this, since processes are no longer a fixed 5 (sync_github auto-registers
// new ones discovered in a synced repo; POST /api/pac/processes adds them
// manually). Keeping this as the initial state avoids a blank flash.
const _PAC_PROCESSES_FALLBACK = [
  { id:"itgc",             label:"ITGCs",            shortLabel:"ITGC", color:"#6366f1", bg:"rgba(99,102,241,0.12)",  icon:"🔒",
    desc:"IT General Controls — Oracle Fusion access provisioning, SOD, change management, audit logging via IDCS and Security Console." },
  { id:"order_to_cash",    label:"Order to Cash",    shortLabel:"O2C",  color:"#0ea5e9", bg:"rgba(14,165,233,0.12)",  icon:"💰",
    desc:"Order Management → AR Invoice → Revenue Recognition — Oracle OM, Configurator, AR, Revenue Management modules." },
  { id:"procure_to_pay",   label:"Procure to Pay",   shortLabel:"P2P",  color:"#f59e0b", bg:"rgba(245,158,11,0.12)",  icon:"📦",
    desc:"Requisition → PO → Receipt → Invoice → Payment — Oracle Purchasing, iProcurement, AP, Payment modules." },
  { id:"receive_to_ship",  label:"Receive to Ship",  shortLabel:"R2S",  color:"#10b981", bg:"rgba(16,185,129,0.12)",  icon:"🚢",
    desc:"Inbound Receipt → WMS Putaway → Pick/Pack/Ship → POD — Oracle WMS, Shipping Execution, Inventory modules." },
  { id:"record_to_report", label:"Record to Report", shortLabel:"R2R",  color:"#ef4444", bg:"rgba(239,68,68,0.12)",   icon:"📊",
    desc:"Journal Entry → Sub-ledger → GL Close → Financial Statements — Oracle GL, SLA, FAH, Financial Reporting modules." },
];

function _hexToRgba(hex, alpha) {
  const h = (hex || "#6366f1").replace("#", "");
  const n = h.length === 3 ? h.split("").map(c => c + c).join("") : h;
  const r = parseInt(n.slice(0, 2), 16) || 99, g = parseInt(n.slice(2, 4), 16) || 102, b = parseInt(n.slice(4, 6), 16) || 241;
  return `rgba(${r},${g},${b},${alpha})`;
}

// Maps GET /api/pac/processes' snake_case DB shape to what ProcessFlowMap /
// the tab bar already expect (shortLabel/desc/bg) — auto-discovered processes
// (sync_github) have no color/icon assigned by a human, so those get a
// reasonable default rather than rendering blank.
function _normalizeProcess(p) {
  const color = p.color || "#8b5cf6";
  return {
    id: p.id,
    label: p.label,
    shortLabel: p.short_label || p.id.toUpperCase().slice(0, 4),
    color,
    bg: _hexToRgba(color, 0.12),
    icon: p.icon || "📁",
    desc: p.description || "",
    source: p.source,
  };
}

const _PROC_CONTROLS = {
  itgc:            ["AC-01","AC-02","AC-03","SI-01","AU-01","CA-01","CM-01","IA-01"],
  order_to_cash:   ["OTC-01","OTC-02","OTC-03","OTC-04","OTC-05","OTC-06"],
  procure_to_pay:  ["P2P-01","P2P-02","P2P-03","P2P-04","P2P-05","P2P-06"],
  receive_to_ship: ["RTS-01","RTS-02","RTS-03","RTS-04","RTS-05"],
  record_to_report:["RTR-01","RTR-02","RTR-03","RTR-04","RTR-05","RTR-06"],
};

const _PAC_RULES = {
  itgc:            ["deny_access_event","deny_privileged_activity","deny_config_change","deny_audit_event"],
  order_to_cash:   ["deny_order_event","deny_billing_event","deny_revenue_event","deny_credit_event"],
  procure_to_pay:  ["deny_invoice_event","deny_payment_event","deny_vendor_event","deny_po_event"],
  receive_to_ship: ["deny_receiving_event","deny_shipment_event","deny_inventory_event"],
  record_to_report:["deny_journal_event","deny_period_close_event","deny_reconciliation_event","deny_financial_event"],
};

const _PROC_NARRATIVES = {
  itgc: `Oracle Fusion ITGCs govern the logical access and change management lifecycle across all ERP modules. User provisioning requests originate in Oracle IDCS (Identity Cloud Service) and must complete an approval workflow before the identity is granted roles in the Fusion Security Console. Privileged role assignments — including Data Role, Abstract Role, and Job Role combinations — are reviewed quarterly via Oracle Access Certification. Segregation of duties (SOD) conflicts are detected at provisioning time using Oracle Advanced Access Controls (OAAC) and raised as policy violations. Change management follows a formal RFC lifecycle: Development → SIT → UAT → Production, with each environment promotion gated by approvals captured in the Oracle Fusion Change Management module. Audit events are streamed to Oracle Audit Vault for retention and anomaly detection. This Rego module evaluates each incoming event against these controls and denies policy violations with structured messages including the Oracle module, user, and control reference.`,
  order_to_cash: `The Oracle Fusion Order-to-Cash process spans Oracle Order Management (OM), Configurator, Receivables (AR), and Revenue Management. Customer orders are created in OM and validated against the customer credit limit held in AR — orders exceeding the limit are automatically placed on credit hold. Order lines are fulfilled via shipping authorizations (SHIP_CONFIRM events) which are matched to booked order lines; unfulfilled lines trigger hold violations. AR invoices are auto-generated from the shipping interface and must match the original order price within a configurable tolerance. Revenue is recognized under ASC 606/IFRS 15 rules using Oracle Revenue Management's Performance Obligation framework — manual override of system-determined recognition schedules is a policy violation. Cash receipts are applied using the AR AutoApply engine; unapplied receipts aged beyond 15 days require resolution. This Rego module denies each class of violation with the Oracle transaction number, customer ID, and affected AR control.`,
  procure_to_pay: `Oracle Fusion Procure-to-Pay covers iProcurement → Oracle Purchasing (PO) → Inventory Receipt → Oracle Payables (AP) → Oracle Payments. Purchase requisitions must be approved per the Approval Management Engine (AME) hierarchy before conversion to a PO. All POs above the corporate threshold require dual approval. Receipt to invoice three-way matching (PO Qty × PO Price = Receipt Qty × Invoice Price within 2% tolerance) is enforced by the AP Matching process — invoices failing the match are held. Vendor master changes (banking details, address, status) require a separate approval chain with mandatory audit trail. Payment batches above $250,000 require a dual-control release from AP Supervisor and Treasury. SOD between PO creation and invoice approval roles is enforced via OAAC. This Rego module evaluates AP/PO events and issues structured denials referencing the PO number, supplier ID, and control reference.`,
  receive_to_ship: `Oracle Fusion Receive-to-Ship covers Oracle WMS (Warehouse Management), Shipping Execution, and Inventory Management. Inbound receipts are created against ASNs or POs and require inspection disposition before putaway; receipts with no corresponding source document are quarantined. WMS putaway directives route items to pre-defined locators — manual locator overrides are flagged. Outbound picking is driven by Oracle Shipping Execution pick release — unauthorized pick confirmations (no pick wave reference) are violations. Ship confirmations require a matching backorder-free delivery and a valid carrier booking; shipments above the declared weight limit trigger an alert. Proof of Delivery (POD) must be recorded within 48 hours of ship confirm for AR invoice to be released. Negative inventory adjustments above $10,000 require supervisor approval. This Rego module evaluates WMS/Shipping events and denies policy violations with shipment, delivery, and locator details.`,
  record_to_report: `Oracle Fusion Record-to-Report encompasses Oracle General Ledger (GL), Subledger Accounting (SLA), Fusion Accounting Hub (FAH), and Oracle Financial Reporting (FR). Manual journal entries above $50,000 require approval via AME and must include a business justification. Journal sources flagged as "Manual" with no supporting subledger event are high-risk and require controller sign-off. Subledger-to-GL reconciliations must be completed within 3 business days of period end; unreconciled differences above $1,000 are policy exceptions. Period close follows a structured close checklist in Oracle Close Monitor — out-of-sequence close steps are violations. Account reconciliations are certified in Oracle Account Reconciliations Cloud (ARCS) by the GL Accountant and reviewed by the Controller. Financial statements are generated from certified ledger balances; any post-certification adjustment requires CFO approval. This Rego module evaluates GL/SLA events against these controls and issues structured violations with journal ID, ledger, and period references.`,
};

// ── Animated Process Flow Map ─────────────────────────────────────────────
function ProcessFlowMap({ activeProcess, processes }) {
  const PAC_PROCESSES = processes && processes.length ? processes : _PAC_PROCESSES_FALLBACK;
  const [selected, setSelected] = useState(activeProcess || "itgc");
  useEffect(() => { setSelected(activeProcess || "itgc"); }, [activeProcess]);

  const W = 660, H = 320;
  const colW = W / PAC_PROCESSES.length;
  const cx = (i) => Math.round(colW * i + colW / 2);
  const ROW_Y    = [44, 136, 228, 299];
  const ROW_R    = [24,  18,  18,  12];
  const ROW_LBL  = ["Process","PaC (Rego)","CaC (Rego)","Outcome"];
  const ROW_ICON = ["⚙️","📜","🛡️","✅"];
  const STAGGER  = [0, 0.67, 1.33];

  const selCol = PAC_PROCESSES.find(c => c.id === selected) || PAC_PROCESSES[0];

  return (
    <div className="pac-flow-map">
      <div className="pac-flow-header">
        <span className="pac-flow-title">PROCESS FLOW MAP — PAC &amp; CaC</span>
        <span className="pac-flow-sub">Click a column to inspect policy rules and controls</span>
      </div>

      <div className="pac-flow-svg-wrap">
        {/* height:"auto" here doesn't reliably derive from viewBox for an inline
            <svg> — and even the width/height XML attrs below are overridden by
            this same inline style anyway (CSS wins over SVG presentation attrs).
            aspect-ratio is the one CSS property that actually locks the box to
            the viewBox's proportions regardless of that precedence quirk. */}
        <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H}
          style={{ width:"100%", aspectRatio:`${W} / ${H}`, display:"block", overflow:"visible" }}>
          {/* Row labels */}
          {ROW_LBL.map((lbl, ri) => (
            <text key={ri} x={3} y={ROW_Y[ri]} dominantBaseline="middle"
              fontSize={7.5} fill="var(--ink-3)" fontWeight={600}>
              {ROW_ICON[ri]} {lbl}
            </text>
          ))}

          {/* Selected column background */}
          {PAC_PROCESSES.map((col, ci) => (
            <rect key={col.id}
              x={cx(ci) - colW / 2 + 6} y={4}
              width={colW - 12} height={H - 8}
              rx={8}
              fill={selected === col.id ? col.bg : "transparent"}
              style={{ cursor:"pointer", transition:"fill 0.25s" }}
              onClick={() => setSelected(col.id)}
            />
          ))}

          {/* CaC horizontal link */}
          <line x1={cx(0)} y1={ROW_Y[2]} x2={cx(PAC_PROCESSES.length - 1)} y2={ROW_Y[2]}
            stroke="var(--line)" strokeWidth={1} strokeDasharray="3 5" opacity={0.4} />

          {/* Per-column edges + particles + nodes */}
          {PAC_PROCESSES.map((col, ci) => {
            const dim = selected === col.id ? 1 : 0.28;
            return (
              <g key={col.id} opacity={dim} style={{ transition:"opacity 0.3s", cursor:"pointer" }} onClick={() => setSelected(col.id)}>
                {/* Edges */}
                {[0,1,2].map(ri => (
                  <line key={ri}
                    x1={cx(ci)} y1={ROW_Y[ri] + ROW_R[ri]}
                    x2={cx(ci)} y2={ROW_Y[ri+1] - ROW_R[ri+1]}
                    stroke={col.color} strokeWidth={1.5} strokeDasharray="3 3" opacity={0.45}
                  />
                ))}

                {/* Animated particles on each edge */}
                {[0,1,2].map(ri =>
                  STAGGER.map((off, pi) => {
                    const y1 = ROW_Y[ri] + ROW_R[ri];
                    const y2 = ROW_Y[ri+1] - ROW_R[ri+1];
                    return (
                      <circle key={`p${ri}-${pi}`} r={selected === col.id ? 3.5 : 2} fill={col.color} opacity={0.9}>
                        <animateMotion path={`M ${cx(ci)},${y1} L ${cx(ci)},${y2}`}
                          dur={`${1.3 + ri * 0.25}s`} begin={`${off}s`} repeatCount="indefinite" />
                      </circle>
                    );
                  })
                )}

                {/* Process node (row 0) */}
                <rect x={cx(ci)-ROW_R[0]} y={ROW_Y[0]-ROW_R[0]} width={ROW_R[0]*2} height={ROW_R[0]*2}
                  rx={8} fill={col.color}
                  filter={selected === col.id ? `drop-shadow(0 0 7px ${col.color})` : "none"}
                  style={{ transition:"filter 0.3s" }} />
                <text x={cx(ci)} y={ROW_Y[0]} dominantBaseline="middle" textAnchor="middle"
                  fontSize={14} style={{ userSelect:"none" }}>{col.icon}</text>
                <text x={cx(ci)} y={ROW_Y[0]+ROW_R[0]+10} dominantBaseline="middle" textAnchor="middle"
                  fontSize={7.5} fontWeight={800} fill={col.color} letterSpacing={0.4}>{col.shortLabel}</text>

                {/* PaC node (row 1) */}
                <circle cx={cx(ci)} cy={ROW_Y[1]} r={ROW_R[1]}
                  fill={selected === col.id ? col.color : "var(--surface-2)"}
                  stroke={col.color} strokeWidth={1.5}
                  filter={selected === col.id ? `drop-shadow(0 0 5px ${col.color})` : "none"}
                  style={{ transition:"all 0.25s" }} />
                <text x={cx(ci)} y={ROW_Y[1]} dominantBaseline="middle" textAnchor="middle"
                  fontSize={7} fontWeight={800} fill={selected === col.id ? "#fff" : col.color}>PAC</text>
                <text x={cx(ci)} y={ROW_Y[1]+ROW_R[1]+9} dominantBaseline="middle" textAnchor="middle"
                  fontSize={6.5} fill="var(--ink-3)">{(_PAC_RULES[col.id]||[]).length} rules</text>

                {/* CaC node (row 2) */}
                <circle cx={cx(ci)} cy={ROW_Y[2]} r={ROW_R[2]}
                  fill={selected === col.id ? col.color : "var(--surface-2)"}
                  stroke={col.color} strokeWidth={1.5}
                  filter={selected === col.id ? `drop-shadow(0 0 5px ${col.color})` : "none"}
                  style={{ transition:"all 0.25s" }} />
                <text x={cx(ci)} y={ROW_Y[2]} dominantBaseline="middle" textAnchor="middle"
                  fontSize={7} fontWeight={800} fill={selected === col.id ? "#fff" : col.color}>CAC</text>
                <text x={cx(ci)} y={ROW_Y[2]+ROW_R[2]+9} dominantBaseline="middle" textAnchor="middle"
                  fontSize={6.5} fill="var(--ink-3)">{(_PROC_CONTROLS[col.id]||[]).length} controls</text>

                {/* Outcome node (row 3) */}
                <circle cx={cx(ci)} cy={ROW_Y[3]} r={ROW_R[3]}
                  fill={selected === col.id ? col.color : "var(--surface-2)"}
                  stroke={col.color} strokeWidth={1.5}
                  style={{ transition:"all 0.25s" }} />
                <text x={cx(ci)} y={ROW_Y[3]} dominantBaseline="middle" textAnchor="middle"
                  fontSize={6} fontWeight={800} fill={selected === col.id ? "#fff" : col.color}>✓</text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Detail panel */}
      <div className="pac-flow-detail" style={{ borderTop:`2px solid ${selCol.color}33` }}>
        <div style={{ marginBottom:10, display:"flex", alignItems:"center", gap:8 }}>
          <span style={{ fontSize:15 }}>{selCol.icon}</span>
          <span style={{ fontWeight:700, fontSize:12.5, color:selCol.color }}>{selCol.label}</span>
          <span style={{ fontSize:10, color:"var(--ink-3)", flex:1 }}>— {selCol.desc.slice(0,95)}…</span>
        </div>
        <div className="pac-detail-grid">
          <div>
            <div className="pac-detail-col-title" style={{ color:selCol.color }}>📜 PaC Deny Rules</div>
            {(_PAC_RULES[selected]||[]).map((r,i) => (
              <div key={i} className="pac-rule-chip">
                <span className="rule-dot" style={{ background:selCol.color }} />
                <span className="mono" style={{ fontSize:10.5 }}>{r}[msg]</span>
              </div>
            ))}
          </div>
          <div>
            <div className="pac-detail-col-title" style={{ color:selCol.color }}>🛡️ CaC Controls</div>
            <div style={{ display:"flex", flexWrap:"wrap" }}>
              {(_PROC_CONTROLS[selected]||[]).map((c,i) => (
                <span key={i} className="pac-ctrl-chip">
                  <span style={{ width:5, height:5, borderRadius:"50%", background:selCol.color, display:"inline-block", flexShrink:0 }} />
                  {c}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function CoverageStat({ label, value, sub, color }) {
  return (
    <div style={{ padding:"12px 18px", borderRadius:8, border:"1px solid var(--line)", minWidth:130 }}>
      <div style={{ fontSize:9.5, fontWeight:700, color:"var(--ink-4)", letterSpacing:"0.05em", textTransform:"uppercase", marginBottom:4 }}>{label}</div>
      <div style={{ display:"flex", alignItems:"baseline", gap:6 }}>
        <span style={{ fontSize:22, fontWeight:700, color: color || "var(--ink)" }}>{value}</span>
        {sub && <span className="mono" style={{ fontSize:11, color: color || "var(--ink-3)" }}>{sub}</span>}
      </div>
    </div>
  );
}

// ── PolicyAsCodeScreen ────────────────────────────────────────────────────
function PolicyAsCodeScreen({ events, maps, risks, appetiteThreshold = 7.5, initialProcess }) {
  const [activeProcess, setActiveProcess] = useState(initialProcess || "itgc");
  const [mainTab,       setMainTab]       = useState("editor");

  // Editor state
  const [rego,     setRego]     = useState("");
  const [origRego, setOrigRego] = useState("");
  const [saving,   setSaving]   = useState(false);
  const [saveMsg,  setSaveMsg]  = useState(null);
  const [modMeta,  setModMeta]  = useState(null);

  // Evaluate panel — real OPA when available, heuristic fallback (see /api/pac/evaluate)
  const [showEval,    setShowEval]    = useState(false);
  const [evalInput,   setEvalInput]   = useState('{\n  "event": {\n    "type": "user_provisioning",\n    "approved_by": null\n  }\n}');
  const [evaluating,  setEvaluating]  = useState(false);
  const [evalResult,  setEvalResult]  = useState(null);
  const [evalErr,     setEvalErr]     = useState(null);

  // Approver modal
  const [showApprove, setShowApprove] = useState(false);
  const [appName,     setAppName]     = useState("");
  const [appRole,     setAppRole]     = useState("");
  const [appErr,       setAppErr]     = useState(null);

  // External sources
  const [ghConfig,     setGhConfig]     = useState({ repo_url:"", branch:"main", path_filter:"", pat:"" });
  const [cfConfig,     setCfConfig]     = useState({ base_url:"", space_key:"", api_token:"" });
  const [hookMsg,      setHookMsg]      = useState({});
  const [ghSaved,      setGhSaved]      = useState(false);
  const [cfSaved,      setCfSaved]      = useState(false);
  const [ghSyncing,    setGhSyncing]    = useState(false);
  const [ghSyncResult, setGhSyncResult] = useState(null); // { imported:[], skipped:[], files_found } | { error }

  // #1b — AI-drafted Rego from whatever's currently in the editor (e.g. a
  // narrative just pulled in via Sync Now). Only replaces the local draft —
  // nothing is persisted until Save Version is clicked.
  const [drafting, setDrafting] = useState(false);
  const [draftErr, setDraftErr] = useState(null);
  const aiDraftAvailable = typeof window !== "undefined" && window.MCP?.aiDraftRego;
  async function handleDraftRego() {
    if (!aiDraftAvailable || !rego.trim()) return;
    setDrafting(true); setDraftErr(null);
    try {
      const result = await window.MCP.aiDraftRego(activeProcess, rego);
      setRego(result.rego_content || "");
    } catch (e) {
      setDraftErr(e.message || "AI unavailable");
    }
    setDrafting(false);
  }

  const dirty = rego !== origRego;

  // A failed load used to be swallowed entirely: non-ok -> null -> early
  // return, leaving the editor showing its initial "". So a 404 (e.g. the dev
  // proxy forwarding /api/pac/* to a path the backend doesn't serve) and a
  // process that genuinely has no module looked identical — an empty box with
  // no explanation. Surface the failure instead.
  const loadModule = useCallback((process) => {
    return fetch(`/api/pac/modules/${process}`, { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(
        r.status === 404
          ? "404 — the API did not recognise /api/pac/modules. If you're running `npm run dev`, restart it so the updated proxy config loads."
          : `HTTP ${r.status}`
      )))
      .then(data => {
        if (!data) return;
        setSaveMsg(null);
        setRego(data.rego_content || "");
        setOrigRego(data.rego_content || "");
        setModMeta({
          id:             data.id,
          version:        data.version || "1.0",
          last_revised_at:data.last_revised_at,
          module_name:    data.module_name || `controls.oracle_fusion.${process}`,
          approvers:      data.approvals || [],
          rule_coverage:  data.rule_coverage || null,
        });
      })
      .catch(e => setSaveMsg({ kind: "err", msg: `Could not load this module — ${e.message}` }));
  }, []);

  // Load module when process changes
  useEffect(() => {
    setSaveMsg(null);
    loadModule(activeProcess);
  }, [activeProcess, loadModule]);

  // Load hooks on mount
  useEffect(() => {
    fetch("/api/pac/hooks", { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        // GET /pac/hooks nests configs under "hooks" ({hooks: {github: {...}}}),
        // not at the top level — reading data.github directly here always saw
        // undefined, so ghSaved/cfSaved never got restored on page load/reload,
        // silently re-disabling "Sync Now" every time despite a real saved hook.
        const hooks = data?.hooks;
        if (!hooks) return;
        if (hooks.github)     { setGhConfig(c => ({ ...c, ...hooks.github }));     setGhSaved(true); }
        if (hooks.confluence) { setCfConfig(c => ({ ...c, ...hooks.confluence })); setCfSaved(true); }
      })
      .catch(() => {});
  }, []);

  // Business processes are DB-backed, not a fixed 5 — sync_github can
  // auto-register new ones (one per unmatched repo folder). Fetched on
  // mount, and re-fetched after every "Sync Now" so newly-discovered
  // processes show up as tabs immediately rather than needing a reload.
  // _PAC_PROCESSES_FALLBACK covers the gap before the first fetch resolves.
  const [processes, setProcesses] = useState(_PAC_PROCESSES_FALLBACK);
  const refreshProcesses = useCallback(() => {
    return fetch("/api/pac/processes", { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.processes?.length) setProcesses(data.processes.map(_normalizeProcess));
      })
      .catch(() => {});
  }, []);
  useEffect(() => { refreshProcesses(); }, [refreshProcesses]);

  async function handleSave() {
    if (!rego.trim()) return;
    setSaving(true); setSaveMsg(null);
    try {
      const r = await fetch(`/api/pac/modules/${activeProcess}`, {
        method: "PUT", headers: _codeAuthHeaders(),
        body: JSON.stringify({
          module_name:   modMeta?.module_name || `controls.oracle_fusion.${activeProcess}`,
          rego_content:  rego,
          version:       modMeta?.version || "1.0",
        }),
      });
      if (r.ok) {
        const d = await r.json();
        setOrigRego(rego);
        setModMeta(m => ({ ...m, id:d.id, last_revised_at:new Date().toISOString(), approvers:[] }));
        setSaveMsg({ kind:"ok", msg:"Saved as new version." });
      } else {
        setSaveMsg({ kind:"err", msg:"Save failed." });
      }
    } catch { setSaveMsg({ kind:"err", msg:"Network error." }); }
    setSaving(false);
  }

  async function handleApprove() {
    if (!appName.trim()) return;
    if (!modMeta?.id) {
      setAppErr("This module hasn't been saved yet — save a version before signing off.");
      return;
    }
    setAppErr(null);
    try {
      const r = await fetch(`/api/pac/modules/${activeProcess}/approve`, {
        method: "POST", headers: _codeAuthHeaders(),
        body: JSON.stringify({ module_id: modMeta.id, approver: appName.trim(), role: appRole.trim() || null }),
      });
      if (r.ok) {
        setModMeta(m => ({
          ...m,
          approvers: [...(m?.approvers||[]), { approver:appName.trim(), role:appRole.trim()||null, approved_at:new Date().toISOString() }],
        }));
        setAppName(""); setAppRole(""); setShowApprove(false);
      } else {
        let detail = r.statusText;
        try { detail = (await r.json()).detail || detail; } catch {}
        setAppErr(`Sign-off failed (${r.status}) — ${detail}`);
      }
    } catch (e) {
      setAppErr(`Network error — ${e.message}`);
    }
  }

  async function handleEvaluate() {
    setEvalErr(null); setEvalResult(null);
    if (!rego.trim()) { setEvalErr("Rego module is empty — nothing to evaluate."); return; }
    let inputEvent;
    try {
      inputEvent = JSON.parse(evalInput);
    } catch (e) {
      setEvalErr(`Input event is not valid JSON — ${e.message}`);
      return;
    }
    setEvaluating(true);
    try {
      const r = await fetch("/api/pac/evaluate", {
        method: "POST", headers: _codeAuthHeaders(),
        body: JSON.stringify({ rego_content: rego, input_event: inputEvent }),
      });
      const d = await r.json();
      if (r.ok) setEvalResult(d);
      else setEvalErr(d.detail || "Evaluation failed.");
    } catch (e) {
      setEvalErr(`Network error — ${e.message}`);
    }
    setEvaluating(false);
  }

  async function saveHook(type) {
    const config = type === "github" ? ghConfig : cfConfig;
    try {
      const r = await fetch(`/api/pac/hooks/${type}`, {
        method: "PUT", headers: _codeAuthHeaders(),
        body: JSON.stringify({ config }),
      });
      if (r.ok) {
        const d = await r.json().catch(() => ({}));
        if (d.saved === false) {
          setHookMsg(m => ({ ...m, [type]: d.note || "Save failed — database not configured" }));
          return;
        }
        if (type === "github") setGhSaved(true); else setCfSaved(true);
        setHookMsg(m => ({ ...m, [type]:"✓ Saved" }));
        setTimeout(() => setHookMsg(m => ({ ...m, [type]:null })), 2000);
      } else {
        const d = await r.json().catch(() => ({}));
        setHookMsg(m => ({ ...m, [type]: `Save failed (${r.status}) — ${d.detail || r.statusText}` }));
      }
    } catch (e) {
      setHookMsg(m => ({ ...m, [type]: `Save failed — ${e.message}` }));
    }
  }

  // Pull every .rego file out of the configured repo path and import it as a
  // module for the matching process, then refresh the currently open editor
  // in case the active process was one of the files just pulled in.
  async function syncGithubNow() {
    setGhSyncing(true); setGhSyncResult(null);
    try {
      const r = await fetch("/api/pac/hooks/github/sync", { method:"POST", headers:_codeAuthHeaders() });
      const d = await r.json();
      if (!r.ok) { setGhSyncResult({ error: d.detail || "Sync failed" }); return; }
      setGhSyncResult(d);
      // Newly-discovered processes (auto-registered from unmatched repo
      // folders) need the tab list refreshed, not just the active module —
      // otherwise a brand-new process only shows up after a page reload.
      await refreshProcesses();
      await loadModule(activeProcess);
    } catch (e) {
      setGhSyncResult({ error: e.message || "Network error" });
    } finally {
      setGhSyncing(false);
    }
  }

  const proc = processes.find(p => p.id === activeProcess) || processes[0];

  // The process tab bar has no fixed limit — sync_github auto-registers a
  // new tab per unmatched repo file, so it can grow past the visible width.
  // overflow-x:auto alone let it scroll but gave no visual cue that more
  // tabs existed off-screen (scrollbar deliberately hidden for a cleaner
  // look) and no way to scroll with a plain mouse wheel — a right-edge tab
  // like "Payroll" just looked clipped/missing. Chevron buttons + wheel
  // support fix both.
  const procBarRef = useRef(null);
  const [procScroll, setProcScroll] = useState({ left: false, right: false });
  const updateProcScroll = useCallback(() => {
    const el = procBarRef.current;
    if (!el) return;
    setProcScroll({
      left: el.scrollLeft > 2,
      right: el.scrollLeft < el.scrollWidth - el.clientWidth - 2,
    });
  }, []);
  useEffect(() => {
    updateProcScroll();
    window.addEventListener("resize", updateProcScroll);
    return () => window.removeEventListener("resize", updateProcScroll);
  }, [updateProcScroll, processes.length]);
  function scrollProcBar(dir) {
    procBarRef.current?.scrollBy({ left: dir * 220, behavior: "smooth" });
  }

  // ── Plain-Language Policies tab ──────────────────────────────────────────
  // Upload the prose policy the org actually wrote, keep it as the source of
  // record, let Claude draft the Rego, and gate that draft behind a human
  // review before it can ever become a live module (POST
  // /pac/conversions/{id}/decision is the only path that publishes). See
  // pac_policy_docs.py's module docstring for why this exists alongside the
  // fire-and-forget GitHub sync path.
  const pdAuth = window.useAuth ? window.useAuth() : null;
  const reviewerName = pdAuth?.user?.display_name || pdAuth?.user?.username || "";

  const [docs,        setDocs]        = useState([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [docsErr,     setDocsErr]     = useState(null);
  const [selDocId,    setSelDocId]    = useState(null);
  const [docDetail,   setDocDetail]   = useState(null);
  const [docLoading,  setDocLoading]  = useState(false);

  const [pdMode,      setPdMode]      = useState("file");     // "file" | "paste"
  const [pdFile,      setPdFile]      = useState(null);
  const [pdTitle,     setPdTitle]     = useState("");
  const [pdText,      setPdText]      = useState("");
  const [pdUploading, setPdUploading] = useState(false);
  const [pdMsg,       setPdMsg]       = useState(null);        // { kind, msg }

  const [pdGuidance,  setPdGuidance]  = useState("");
  const [converting,  setConverting]  = useState(false);

  // Review workspace for one conversion. draft is local until "Save Draft" —
  // the backend re-validates on every write, so what the queue reports always
  // describes the text actually stored.
  const [selConvId,   setSelConvId]   = useState(null);
  const [convDraft,   setConvDraft]   = useState("");
  const [convOrig,    setConvOrig]    = useState("");
  const [savingDraft, setSavingDraft] = useState(false);
  const [showGen,     setShowGen]     = useState(false);       // show untouched model output
  const [revNotes,    setRevNotes]    = useState("");
  const [revRole,     setRevRole]     = useState("");
  const [deciding,    setDeciding]    = useState(false);
  const [revMsg,      setRevMsg]      = useState(null);

  const pendingReviewCount = docs.reduce((n, d) => n + Number(d.pending_review_count || 0), 0);

  const loadDocs = useCallback((process) => {
    setDocsLoading(true); setDocsErr(null);
    return fetch(`/api/pac/policy-docs?process=${encodeURIComponent(process)}`, { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => setDocs(d.documents || []))
      .catch(e => { setDocs([]); setDocsErr(e.message || "Failed to load"); })
      .finally(() => setDocsLoading(false));
  }, []);

  const loadDocDetail = useCallback((docId) => {
    if (!docId) { setDocDetail(null); return Promise.resolve(); }
    setDocLoading(true);
    return fetch(`/api/pac/policy-docs/${docId}`, { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => {
        setDocDetail(d);
        // Open the conversion that actually needs a decision, falling back to
        // the newest one, so clicking a document lands on the work to be done.
        const convs = d.conversions || [];
        const open = convs.find(c => c.status === "pending_review" || c.status === "changes_requested") || convs[0];
        setSelConvId(open?.id || null);
        setConvDraft(open?.draft_rego || "");
        setConvOrig(open?.draft_rego || "");
      })
      .catch(() => setDocDetail(null))
      .finally(() => setDocLoading(false));
  }, []);

  // Documents are per-process, same as modules — switching the process tab
  // clears the selection rather than showing another process's document.
  useEffect(() => {
    if (mainTab !== "policydocs") return;
    setSelDocId(null); setDocDetail(null); setSelConvId(null);
    setPdMsg(null); setRevMsg(null);
    loadDocs(activeProcess);
  }, [mainTab, activeProcess, loadDocs]);

  useEffect(() => { loadDocDetail(selDocId); }, [selDocId, loadDocDetail]);

  const selConv = (docDetail?.conversions || []).find(c => c.id === selConvId) || null;
  const convDirty = selConv ? convDraft !== convOrig : false;
  const convOpen  = selConv ? ["pending_review", "changes_requested"].includes(selConv.status) : false;

  async function handleUploadDoc() {
    setPdMsg(null);
    const isFile = pdMode === "file";
    if (isFile && !pdFile) { setPdMsg({ kind:"err", msg:"Choose a file first." }); return; }
    if (!isFile && !pdText.trim()) { setPdMsg({ kind:"err", msg:"Paste the policy text first." }); return; }
    if (!isFile && !pdTitle.trim()) { setPdMsg({ kind:"err", msg:"Give the policy a title." }); return; }

    setPdUploading(true);
    try {
      let r;
      if (isFile) {
        // multipart — no Content-Type header, the browser must set the boundary
        const fd = new FormData();
        fd.append("file", pdFile);
        fd.append("process", activeProcess);
        if (pdTitle.trim())  fd.append("title", pdTitle.trim());
        if (reviewerName)    fd.append("uploaded_by", reviewerName);
        const { "Content-Type": _drop, ...authOnly } = _codeAuthHeaders();
        r = await fetch("/api/pac/policy-docs/upload", { method:"POST", headers: authOnly, body: fd });
      } else {
        r = await fetch("/api/pac/policy-docs", {
          method:"POST", headers:_codeAuthHeaders(),
          body: JSON.stringify({
            process: activeProcess, title: pdTitle.trim(), text: pdText,
            uploaded_by: reviewerName || null,
          }),
        });
      }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setPdMsg({ kind:"err", msg: d.detail || `Upload failed (${r.status})` }); return; }

      setPdMsg({
        kind: d.duplicate_of ? "warn" : "ok",
        msg: d.duplicate_of
          ? `Saved (${d.text_length.toLocaleString()} chars) — note: identical text was already uploaded as "${d.duplicate_of.title}".`
          : `Saved "${d.title}" — ${d.text_length.toLocaleString()} characters. Convert it when ready.`,
      });
      setPdFile(null); setPdText(""); setPdTitle("");
      await loadDocs(activeProcess);
      setSelDocId(d.document_id);
    } catch (e) {
      setPdMsg({ kind:"err", msg:`Network error — ${e.message}` });
    } finally {
      setPdUploading(false);
    }
  }

  async function handleConvertDoc(docId) {
    setConverting(true); setPdMsg(null); setRevMsg(null);
    try {
      const r = await fetch(`/api/pac/policy-docs/${docId}/convert`, {
        method:"POST", headers:_codeAuthHeaders(),
        body: JSON.stringify({ guidance: pdGuidance.trim() || null }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setPdMsg({ kind:"err", msg: d.detail || `Conversion failed (${r.status})` }); return; }
      setPdGuidance("");
      await loadDocs(activeProcess);
      await loadDocDetail(docId);
      setPdMsg({
        kind: d.syntax_valid ? "ok" : "warn",
        msg: d.syntax_valid
          ? `Draft ready for review — ${(d.control_ids||[]).length} control ID${(d.control_ids||[]).length === 1 ? "" : "s"} found.`
          : "Draft ready, but it does not pass Rego validation yet — fix it below before approving.",
      });
    } catch (e) {
      setPdMsg({ kind:"err", msg:`Network error — ${e.message}` });
    } finally {
      setConverting(false);
    }
  }

  async function handleSaveDraft() {
    if (!selConvId || !convDraft.trim()) return;
    setSavingDraft(true); setRevMsg(null);
    try {
      const r = await fetch(`/api/pac/conversions/${selConvId}/draft`, {
        method:"PUT", headers:_codeAuthHeaders(),
        body: JSON.stringify({ rego_content: convDraft }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setRevMsg({ kind:"err", msg: d.detail || `Save failed (${r.status})` }); return; }
      // Reload so the conversion row's stored syntax verdict / control IDs
      // (recomputed server-side on write) replace the pre-edit ones.
      await loadDocDetail(selDocId);
      setRevMsg({
        kind: d.syntax_valid ? "ok" : "warn",
        msg: d.syntax_valid ? "Draft saved — passes Rego validation."
                            : `Draft saved, but still invalid: ${(d.syntax_errors||[]).join("; ")}`,
      });
    } catch (e) {
      setRevMsg({ kind:"err", msg:`Network error — ${e.message}` });
    } finally {
      setSavingDraft(false);
    }
  }

  async function handleDecision(decision) {
    if (!selConvId) return;
    if (!reviewerName.trim()) {
      setRevMsg({ kind:"err", msg:"No signed-in user to attribute this review to." });
      return;
    }
    if (decision !== "approve" && !revNotes.trim()) {
      setRevMsg({ kind:"err", msg:"Say why — a rejection or change request without a reason isn't a review." });
      return;
    }
    if (convDirty) {
      setRevMsg({ kind:"err", msg:"Save your draft edits before deciding — otherwise you'd be approving different text." });
      return;
    }
    setDeciding(true); setRevMsg(null);
    try {
      const r = await fetch(`/api/pac/conversions/${selConvId}/decision`, {
        method:"POST", headers:_codeAuthHeaders(),
        body: JSON.stringify({
          decision, reviewer: reviewerName,
          reviewer_role: revRole.trim() || null,
          notes: revNotes.trim() || null,
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setRevMsg({ kind:"err", msg: d.detail || `Decision failed (${r.status})` }); return; }
      setRevNotes("");
      await loadDocs(activeProcess);
      await loadDocDetail(selDocId);
      if (d.published_module_id) {
        // The approved Rego IS the live module now — pull it into the editor
        // so the next screen the reviewer sees isn't stale.
        await loadModule(activeProcess);
        setRevMsg({ kind:"ok", msg:`Approved and published as v${d.published_version} — now live in the Rego Editor. It still needs its own sign-off there.` });
      } else {
        setRevMsg({ kind:"ok", msg: decision === "reject" ? "Rejected. The document can be re-converted with guidance." : "Changes requested — the draft stays editable." });
      }
    } catch (e) {
      setRevMsg({ kind:"err", msg:`Network error — ${e.message}` });
    } finally {
      setDeciding(false);
    }
  }

  async function handleDeleteDoc(docId) {
    if (!window.confirm("Delete this policy document and its conversion drafts? Any module already published from it stays live.")) return;
    try {
      const r = await fetch(`/api/pac/policy-docs/${docId}`, { method:"DELETE", headers:_codeAuthHeaders() });
      if (!r.ok) { setPdMsg({ kind:"err", msg:`Delete failed (${r.status})` }); return; }
      if (selDocId === docId) { setSelDocId(null); setDocDetail(null); }
      await loadDocs(activeProcess);
    } catch (e) {
      setPdMsg({ kind:"err", msg:`Network error — ${e.message}` });
    }
  }

  const MAIN_TABS = [
    { id:"editor",    label:"Rego Editor" },
    { id:"policydocs", label:"Plain-Language Policies", badge: pendingReviewCount || null },
    { id:"sources",   label:"External Sources" },
    { id:"narrative", label:"Narrative & Flow Map" },
    { id:"coverage",  label:"Control Coverage" },
    { id:"negtest",   label:"Negative Testing" },
  ];

  // Org-wide "how many of our controls actually have an enforceable Rego
  // rule behind them" — a different question from the per-module Control-ID
  // Coverage badge above (which measures whether one module's deny rules
  // are well-formed, not whether the org's controls are enforced at all).
  const [covData, setCovData] = useState(null);
  const [covLoading, setCovLoading] = useState(false);
  const [covError, setCovError] = useState(null);
  useEffect(() => {
    if (mainTab !== "coverage" || covData || covLoading) return;
    setCovLoading(true); setCovError(null);
    fetch("/api/pac/controls/coverage", { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(setCovData)
      .catch(e => setCovError(e.message || "Failed to load"))
      .finally(() => setCovLoading(false));
  }, [mainTab, covData, covLoading]);

  // Executive Compliance Scorecard — framework coverage (mapped vs. actually
  // verified, per P0's negative-testing assurance metadata). Separate fetch
  // per framework, re-triggered on selector change, cached per framework so
  // switching back and forth doesn't re-fetch.
  const SCORECARD_FRAMEWORKS = [
    { id: "soc2", label: "SOC 2" },
    { id: "nist_800_53", label: "NIST SP 800-53" },
    { id: "iso_27001", label: "ISO 27001" },
    { id: "coso", label: "COSO ERM" },
  ];
  const [scorecardFramework, setScorecardFramework] = useState("soc2");
  const [scorecardCache, setScorecardCache] = useState({});
  const [scorecardLoading, setScorecardLoading] = useState(false);
  const [scorecardError, setScorecardError] = useState(null);
  useEffect(() => {
    if (mainTab !== "coverage" || scorecardCache[scorecardFramework]) return;
    setScorecardLoading(true); setScorecardError(null);
    fetch(`/api/pac/compliance-scorecard?framework=${scorecardFramework}`, { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => setScorecardCache(prev => ({ ...prev, [scorecardFramework]: d })))
      .catch(e => setScorecardError(e.message || "Failed to load"))
      .finally(() => setScorecardLoading(false));
  }, [mainTab, scorecardFramework, scorecardCache]);
  const scorecardData = scorecardCache[scorecardFramework];

  // Approval/evaluation drift — whether the module actually being evaluated
  // in production (latest SAVE) matches the latest version a human actually
  // approved (see pac_approval_drift.py's module docstring: nothing today
  // gates evaluation on approval existing at all, so this is the one signal
  // that surfaces the gap). Fetched once per coverage-tab visit.
  const [driftData, setDriftData] = useState(null);
  const [driftLoading, setDriftLoading] = useState(false);
  useEffect(() => {
    if (mainTab !== "coverage" || driftData || driftLoading) return;
    setDriftLoading(true);
    fetch(`/api/pac/approval-drift`, { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(setDriftData)
      .catch(() => {})
      .finally(() => setDriftLoading(false));
  }, [mainTab, driftData, driftLoading]);
  const drifted = driftData ? Object.values(driftData.processes || {}).filter(p => p.drifted) : [];

  // DORA-style change-management metrics — real operational evidence for
  // SOC 2 CC8.1, shown alongside the scorecard when that framework is
  // selected (see dora_metrics.py's module docstring for the exact proxies).
  const [doraData, setDoraData] = useState(null);
  useEffect(() => {
    if (mainTab !== "coverage" || scorecardFramework !== "soc2" || doraData) return;
    fetch(`/api/evidence/dora-metrics?window_days=30`, { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(setDoraData)
      .catch(() => {});
  }, [mainTab, scorecardFramework, doraData]);

  // Negative Testing tab — schema-contract check + must-fire/must-not-fire
  // corpus (pac_contracts.py / pac_negative_tests.py), run against whatever
  // is CURRENTLY in the editor (including an unsaved draft) so a policy
  // author can catch a dead-by-construction rule before ever saving it.
  const [ntRunning,     setNtRunning]     = useState(false);
  const [ntResult,      setNtResult]      = useState(null);
  const [ntError,       setNtError]       = useState(null);
  const [ntHistory,     setNtHistory]     = useState(null);
  const [ntHistoryLoad, setNtHistoryLoad] = useState(false);
  const [assurance,     setAssurance]     = useState(null);
  const [assuranceLoad, setAssuranceLoad] = useState(false);

  const loadNtHistory = useCallback((process) => {
    setNtHistoryLoad(true);
    return fetch(`/api/pac/negative-tests/history/${process}`, { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => setNtHistory(d?.runs || []))
      .catch(() => setNtHistory([]))
      .finally(() => setNtHistoryLoad(false));
  }, []);

  const loadAssurance = useCallback((process) => {
    setAssuranceLoad(true);
    return fetch(`/api/pac/assurance?process=${process}`, { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(setAssurance)
      .catch(() => setAssurance(null))
      .finally(() => setAssuranceLoad(false));
  }, []);

  useEffect(() => {
    if (mainTab !== "negtest") return;
    setNtResult(null); setNtError(null);
    loadNtHistory(activeProcess);
    loadAssurance(activeProcess);
  }, [mainTab, activeProcess, loadNtHistory, loadAssurance]);

  async function handleRunNegativeTests() {
    if (!rego.trim()) return;
    setNtRunning(true); setNtError(null); setNtResult(null);
    try {
      const r = await fetch(`/api/pac/negative-tests/run/${activeProcess}`, {
        method: "POST", headers: _codeAuthHeaders(),
        body: JSON.stringify({ rego_content: rego, triggered_by: "manual" }),
      });
      if (r.ok) {
        const d = await r.json();
        setNtResult(d);
        loadNtHistory(activeProcess);
        loadAssurance(activeProcess);
      } else {
        setNtError(`HTTP ${r.status}`);
      }
    } catch (e) {
      setNtError(e.message || "Network error");
    }
    setNtRunning(false);
  }

  return (
    <div className="pac-shell">
      {/* Process selector tabs */}
      <div className="pac-process-bar-wrap">
        {procScroll.left && (
          <button type="button" className="pac-process-bar-scroll left"
            onClick={() => scrollProcBar(-1)} aria-label="Scroll tabs left">
            <svg width="12" height="12" viewBox="0 0 10 10" fill="none" stroke="currentColor"
              strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6.5 1.5 2.5 5l4 3.5"/>
            </svg>
          </button>
        )}
        <div className="pac-process-bar" ref={procBarRef}
          onScroll={updateProcScroll}
          onWheel={e => {
            if (e.deltaY === 0) return;
            e.currentTarget.scrollLeft += e.deltaY;
            e.preventDefault();
          }}>
          {processes.map(p => (
            <button key={p.id}
              className={"pac-proc-tab" + (activeProcess === p.id ? " active" : "")}
              onClick={() => setActiveProcess(p.id)}
              style={activeProcess === p.id ? { borderColor:`${p.color}55`, color:p.color } : {}}>
              <span className="pac-proc-dot" style={{ background:p.color }} />
              <span className="pac-proc-icon">{p.icon}</span>
              {p.label}
            </button>
          ))}
        </div>
        {procScroll.right && (
          <button type="button" className="pac-process-bar-scroll right"
            onClick={() => scrollProcBar(1)} aria-label="Scroll tabs right">
            <svg width="12" height="12" viewBox="0 0 10 10" fill="none" stroke="currentColor"
              strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3.5 1.5 7.5 5l-4 3.5"/>
            </svg>
          </button>
        )}
      </div>

      {/* Main sub-tabs */}
      <div className="pac-main-tabs">
        {MAIN_TABS.map(t => (
          <button key={t.id}
            className={"pac-main-tab" + (mainTab === t.id ? " active" : "")}
            onClick={() => setMainTab(t.id)}>
            {t.label}
            {t.badge ? (
              <span className="pac-tab-badge" title={`${t.badge} conversion${t.badge === 1 ? "" : "s"} awaiting human review`}>
                {t.badge}
              </span>
            ) : null}
          </button>
        ))}
      </div>

      {/* ── TAB 1: Rego Editor ── */}
      {mainTab === "editor" && (
        <>
          <div className="pac-editor-split">
            {/* Meta sidebar */}
            <div className="pac-meta-panel">
              <div className="pac-meta-label">Process</div>
              <div style={{ fontWeight:700, fontSize:12, color:proc.color, marginBottom:12 }}>
                {proc.icon} {proc.label}
              </div>

              <div className="pac-meta-label">Module</div>
              <div className="pac-meta-val mono" style={{ fontSize:9.5, wordBreak:"break-all" }}>
                {modMeta?.module_name || `controls.oracle_fusion.${activeProcess}`}
              </div>

              <div className="pac-meta-sep" />

              <div className="pac-meta-label">Version</div>
              <div style={{ marginBottom:12 }}>
                <span className="pac-version-badge">{modMeta?.version || "—"}</span>
              </div>

              <div className="pac-meta-label">Last Revised</div>
              <div className="pac-meta-val">
                {modMeta?.last_revised_at
                  ? new Date(modMeta.last_revised_at).toLocaleDateString("en-US",{ month:"short", day:"numeric", year:"numeric" })
                  : "—"}
              </div>

              {modMeta?.rule_coverage && modMeta.rule_coverage.total > 0 && (
                <>
                  <div className="pac-meta-label">Control-ID Coverage</div>
                  <div className="pac-meta-val" style={{ marginBottom:12 }}>
                    <span className={"pac-coverage-badge" +
                      (modMeta.rule_coverage.with_control_id === modMeta.rule_coverage.total ? " full" : " partial")}
                      title="Deny rules whose sprintf message has an extractable <CONTROL-ID>: prefix — used to link this Rego module to Controls-as-Code and adjudication citations.">
                      {modMeta.rule_coverage.with_control_id}/{modMeta.rule_coverage.total} rules
                    </span>
                  </div>
                </>
              )}

              <div className="pac-meta-sep" />

              <div className="pac-meta-label">Sign-offs</div>
              <div style={{ marginBottom:8, display:"flex", flexWrap:"wrap", gap:2 }}>
                {(modMeta?.approvers||[]).length === 0
                  ? <span style={{ fontSize:10, color:"var(--ink-3)" }}>None yet</span>
                  : (modMeta?.approvers||[]).map((a,i) => (
                      <span key={i} className="pac-approver-chip"
                        title={a.approved_at ? new Date(a.approved_at).toLocaleString() : ""}>
                        <span className="dot" />
                        {a.approver}{a.role ? ` · ${a.role}` : ""}
                      </span>
                    ))
                }
              </div>
              <button className="btn btn-sm"
                style={{ width:"100%", justifyContent:"center", marginBottom:12, fontSize:10 }}
                onClick={() => { setAppErr(null); setShowApprove(true); }}
                disabled={!modMeta?.id || dirty}
                title={dirty ? "Save first before signing off" : "Add a sign-off"}>
                + Sign Off
              </button>

              <div className="pac-meta-sep" />

              <div className="pac-meta-label">Description</div>
              <div style={{ fontSize:10, color:"var(--ink-3)", lineHeight:1.55 }}>{proc.desc}</div>

              {saveMsg && (
                <div className={"code-status " + saveMsg.kind} style={{ fontSize:10, marginTop:10 }}>
                  {saveMsg.msg}
                </div>
              )}
            </div>

            {/* Rego editor */}
            <div className="pac-rego-pane">
              <div className="pac-rego-head">
                <span style={{ color:proc.color }}>■</span>
                <span className="mono">{`package controls.oracle_fusion.${activeProcess}`}</span>
                {dirty && <span className="dirty-dot" title="Unsaved changes">●</span>}
                {modMeta?.version && (
                  <span style={{ marginLeft:"auto", fontSize:9, color:"var(--ink-3)" }}>v{modMeta.version}</span>
                )}
              </div>
              <textarea
                className="code-editor mono"
                spellCheck={false}
                value={rego}
                onChange={e => setRego(e.target.value)}
                style={{ flex:1, resize:"none", fontSize:11.5, lineHeight:1.65, padding:"12px 16px" }}
                placeholder={`# Rego policy for ${proc.label}\npackage controls.oracle_fusion.${activeProcess}\n\nimport future.keywords.in\nimport future.keywords.if\n\n# deny_*[msg] rules go here...`}
              />
            </div>
          </div>

          {/* Action bar */}
          <div className="pac-actions-bar">
            <button className="btn btn-sm btn-acc" onClick={handleSave} disabled={!dirty || saving}>
              {saving ? "Saving…" : "Save Version"}
            </button>
            <button className="btn btn-sm" onClick={() => setShowEval(v => !v)}>
              {showEval ? "▾ Evaluate" : "▸ Evaluate"}
            </button>
            {aiDraftAvailable && (
              <button className="btn btn-sm" onClick={handleDraftRego} disabled={drafting || !rego.trim()}
                title="Convert the current editor content (e.g. a narrative just pulled in via Sync Now) into an actual Rego module">
                <Icon name="spark" size={11}/> {drafting ? "Drafting…" : "Draft Rego with AI"}
              </button>
            )}
            {draftErr && <span className="mono" style={{ fontSize:10, color:"var(--red-ink)" }}>AI draft failed: {draftErr}</span>}
            <span style={{ flex:1 }} />
            {dirty && <span style={{ fontSize:10, color:"#f59e0b" }}>● Unsaved changes</span>}
          </div>

          {/* Evaluate panel — real OPA when installed, heuristic fallback otherwise */}
          {showEval && (
            <div className="pac-eval-panel" style={{ display:"flex", gap:12, padding:"10px 16px", borderTop:"1px solid var(--line,#eee)" }}>
              <div style={{ flex:"0 0 320px", display:"flex", flexDirection:"column", gap:6 }}>
                <div style={{ fontSize:9.5, fontWeight:700, color:"var(--ink-3,#888)", letterSpacing:".04em", textTransform:"uppercase" }}>
                  Sample input event
                </div>
                <textarea
                  className="code-editor mono"
                  spellCheck={false}
                  value={evalInput}
                  onChange={e => setEvalInput(e.target.value)}
                  style={{ height:120, resize:"vertical", fontSize:10.5, lineHeight:1.5, padding:8 }}
                />
                <button className="btn btn-sm btn-acc" onClick={handleEvaluate} disabled={evaluating} style={{ alignSelf:"flex-start" }}>
                  {evaluating ? "Evaluating…" : "▶ Run"}
                </button>
                {evalErr && <div className="code-status err" style={{ fontSize:10 }}>{evalErr}</div>}
              </div>

              <div style={{ flex:1, minWidth:0 }}>
                {!evalResult ? (
                  <div style={{ fontSize:11, color:"var(--ink-3,#888)", padding:"8px 0" }}>
                    Run against the current (unsaved edits included) Rego module. Uses the real <code>opa</code> binary when installed — set <code>OPA_BINARY</code> or install <code>opa</code> on PATH — otherwise falls back to a labelled heuristic simulation.
                  </div>
                ) : (
                  <>
                    <div style={{ display:"flex", alignItems:"center", gap:8, marginBottom:8 }}>
                      <span style={{
                        fontSize:9.5, fontWeight:700, padding:"2px 8px", borderRadius:999,
                        background: evalResult.evaluation?.startsWith("opa eval") ? "var(--green-soft,#e8f5e9)" : "var(--amber-soft,#fff8e1)",
                        color:      evalResult.evaluation?.startsWith("opa eval") ? "var(--green-ink,#166534)" : "var(--amber-ink,#b45309)",
                      }}>
                        {evalResult.evaluation?.startsWith("opa eval") ? `✓ OPA ${evalResult.opa_version || ""}` : "⚠ Heuristic (not authoritative)"}
                      </span>
                      {evalResult.opa_unavailable_reason && (
                        <span style={{ fontSize:9.5, color:"var(--ink-3)" }} title={evalResult.opa_unavailable_reason}>
                          {evalResult.opa_unavailable_reason}
                        </span>
                      )}
                    </div>
                    <div style={{ display:"flex", gap:16 }}>
                      <div style={{ flex:1 }}>
                        <div style={{ fontSize:9.5, fontWeight:700, color:"var(--red-ink,#b91c1c)", marginBottom:4 }}>
                          Fired ({(evalResult.rules_fired||[]).length})
                        </div>
                        {(evalResult.rules_fired||[]).length === 0
                          ? <div style={{ fontSize:10.5, color:"var(--ink-3)" }}>None</div>
                          : evalResult.rules_fired.map((r,i) => (
                              <div key={i} className="mono" style={{ fontSize:10, padding:"3px 0", borderBottom:"1px solid var(--line,#eee)" }}>
                                {r.rule}{r.confidence != null ? ` (${(r.confidence*100).toFixed(0)}%)` : ""}
                              </div>
                            ))}
                      </div>
                      <div style={{ flex:1 }}>
                        <div style={{ fontSize:9.5, fontWeight:700, color:"var(--ink-3)", marginBottom:4 }}>
                          Passed ({(evalResult.rules_passed||[]).length})
                        </div>
                        {(evalResult.rules_passed||[]).length === 0
                          ? <div style={{ fontSize:10.5, color:"var(--ink-3)" }}>None</div>
                          : evalResult.rules_passed.map((r,i) => (
                              <div key={i} className="mono" style={{ fontSize:10, padding:"3px 0", borderBottom:"1px solid var(--line,#eee)" }}>
                                {r.rule}{r.confidence != null ? ` (${(r.confidence*100).toFixed(0)}%)` : ""}
                              </div>
                            ))}
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
        </>
      )}

      {/* ── TAB 2: Plain-Language Policies (upload → convert → HITL review) ── */}
      {mainTab === "policydocs" && (
        <div className="pac-docs-wrap">
          {/* Left rail: intake + document list */}
          <div className="pac-docs-rail">
            <div className="pac-hook-card">
              <div className="pac-hook-title">
                Add a policy
                <span className="pac-hook-badge github">{proc.shortLabel || proc.label}</span>
              </div>

              <div className="pac-docs-modeswitch">
                {[{ id:"file", label:"Upload file" }, { id:"paste", label:"Paste text" }].map(m => (
                  <button key={m.id}
                    className={"btn btn-sm" + (pdMode === m.id ? " btn-acc" : "")}
                    onClick={() => { setPdMode(m.id); setPdMsg(null); }}>
                    {m.label}
                  </button>
                ))}
              </div>

              {pdMode === "file" ? (
                <>
                  <div className="pac-hook-input-row">
                    <label>Policy file</label>
                    <input type="file" className="code-input"
                      accept=".md,.markdown,.txt,.text,.rst,.rego,.json,.yaml,.yml,.csv,.pdf,.docx"
                      onChange={e => { setPdFile(e.target.files?.[0] || null); setPdMsg(null); }} />
                  </div>
                  <div className="pac-hook-input-row">
                    <label>Title <span style={{ fontWeight:400, color:"var(--ink-3)" }}>(optional)</span></label>
                    <input className="code-input" value={pdTitle} placeholder="Defaults to the filename"
                      onChange={e => setPdTitle(e.target.value)} />
                  </div>
                </>
              ) : (
                <>
                  <div className="pac-hook-input-row">
                    <label>Title</label>
                    <input className="code-input" value={pdTitle} placeholder="Segregation of Duties Standard"
                      onChange={e => setPdTitle(e.target.value)} />
                  </div>
                  <div className="pac-hook-input-row">
                    <label>Policy text</label>
                    <textarea className="code-editor mono" spellCheck={true} value={pdText}
                      onChange={e => setPdText(e.target.value)}
                      placeholder={"Paste the policy as it is written, in plain language.\n\ne.g. \"No single user may both create a supplier and approve a payment to that supplier. Any exception requires documented CFO approval and must be reviewed within 5 business days.\""}
                      style={{ minHeight:150, resize:"vertical", fontSize:11, lineHeight:1.6, padding:8 }} />
                  </div>
                </>
              )}

              <div className="pac-hook-actions">
                <button className="btn btn-sm btn-acc" onClick={handleUploadDoc} disabled={pdUploading}>
                  {pdUploading ? "Saving…" : "Save Policy"}
                </button>
              </div>

              {pdMsg && (
                <div className={"code-status " + (pdMsg.kind === "ok" ? "ok" : pdMsg.kind === "warn" ? "warn" : "err")}
                  style={{ fontSize:10, lineHeight:1.55 }}>
                  {pdMsg.msg}
                </div>
              )}

              <p style={{ fontSize:10, color:"var(--ink-3)", lineHeight:1.6, margin:0 }}>
                Stored verbatim as the source of record — nothing is converted or published until you ask.
                Accepts <code>.md</code>, <code>.txt</code>, <code>.docx</code>, and text-layer <code>.pdf</code>
                {" "}(scanned image PDFs have no extractable text and are rejected rather than stored blank).
              </p>
            </div>

            <div className="pac-docs-list">
              <div className="pac-meta-label" style={{ display:"flex", alignItems:"center", gap:6 }}>
                {proc.label} policies
                <span style={{ marginLeft:"auto", fontWeight:400, textTransform:"none", letterSpacing:0 }}>
                  {docsLoading ? "loading…" : `${docs.length}`}
                </span>
              </div>

              {docsErr && <div className="code-status err" style={{ fontSize:10 }}>{docsErr}</div>}
              {!docsLoading && !docsErr && docs.length === 0 && (
                <div style={{ fontSize:10.5, color:"var(--ink-3)", lineHeight:1.6, padding:"6px 2px" }}>
                  No policy documents for {proc.label} yet. Upload the written policy above and it becomes the
                  traceable source behind this process's Rego.
                </div>
              )}

              {docs.map(d => (
                <button key={d.id}
                  className={"pac-doc-row" + (selDocId === d.id ? " active" : "")}
                  onClick={() => setSelDocId(d.id)}>
                  <div className="pac-doc-row-head">
                    <span className="pac-doc-title">{d.title}</span>
                    <span className={"pac-doc-status " + d.status}>{d.status.replace("_", " ")}</span>
                  </div>
                  <div className="pac-doc-row-meta mono">
                    {d.filename || "pasted"} · {Number(d.text_length || 0).toLocaleString()} chars
                    {d.created_at ? ` · ${new Date(d.created_at).toLocaleDateString()}` : ""}
                  </div>
                  {Number(d.pending_review_count) > 0 && (
                    <div className="pac-doc-pending">● {d.pending_review_count} awaiting review</div>
                  )}
                </button>
              ))}
            </div>
          </div>

          {/* Right pane: source prose + the HITL review workspace */}
          <div className="pac-docs-detail">
            {!selDocId ? (
              <div className="pac-docs-empty">
                <div style={{ fontWeight:700, fontSize:12, marginBottom:6 }}>Plain language in, reviewed policy out</div>
                <div style={{ fontSize:11, lineHeight:1.7, color:"var(--ink-3)", maxWidth:520 }}>
                  Upload the policy your organisation actually wrote. Claude drafts the equivalent Rego, but that
                  draft never becomes live policy on its own — it waits here until a person reads it against the
                  source text, edits it, and approves or rejects it. The approval is what publishes a new module
                  version, and both the original prose and the untouched model output are kept as evidence.
                </div>
              </div>
            ) : docLoading ? (
              <div className="pac-docs-empty">Loading…</div>
            ) : !docDetail ? (
              <div className="pac-docs-empty">Could not load this document.</div>
            ) : (
              <>
                <div className="pac-docs-detail-head">
                  <div style={{ minWidth:0 }}>
                    <div style={{ fontWeight:700, fontSize:13 }}>{docDetail.title}</div>
                    <div className="mono" style={{ fontSize:9.5, color:"var(--ink-3)", marginTop:2 }}>
                      {docDetail.filename || "pasted text"} · {docDetail.doc_text.length.toLocaleString()} chars
                      {docDetail.uploaded_by ? ` · by ${docDetail.uploaded_by}` : ""}
                      {docDetail.created_at ? ` · ${new Date(docDetail.created_at).toLocaleString()}` : ""}
                    </div>
                  </div>
                  <span style={{ flex:1 }} />
                  <button className="btn btn-sm btn-acc" onClick={() => handleConvertDoc(docDetail.id)} disabled={converting}>
                    <Icon name="spark" size={11}/> {converting ? "Converting…" : (docDetail.conversions?.length ? "Re-convert" : "Convert to Rego")}
                  </button>
                  <button className="btn btn-sm" onClick={() => handleDeleteDoc(docDetail.id)}>Delete</button>
                </div>

                <div className="pac-hook-input-row" style={{ padding:"0 16px 8px" }}>
                  <label>Conversion guidance <span style={{ fontWeight:400, color:"var(--ink-3)" }}>(optional — steers the draft)</span></label>
                  <input className="code-input" value={pdGuidance}
                    placeholder="e.g. only the segregation-of-duties section; events come from the Oracle AP feed"
                    onChange={e => setPdGuidance(e.target.value)} />
                </div>

                <div className="pac-docs-split">
                  {/* Source of record */}
                  <div className="pac-docs-source">
                    <div className="pac-rego-head">
                      <span style={{ color:proc.color }}>■</span> Source policy (as written)
                    </div>
                    <pre className="pac-docs-prose">{docDetail.doc_text}</pre>
                  </div>

                  {/* Review workspace */}
                  <div className="pac-docs-review">
                    {(docDetail.conversions || []).length === 0 ? (
                      <div className="pac-docs-empty" style={{ padding:24 }}>
                        <div style={{ fontSize:11, lineHeight:1.7, color:"var(--ink-3)", maxWidth:420 }}>
                          No draft yet. "Convert to Rego" asks Claude to translate this document into
                          <code> deny_*[msg]</code> rules using this process's <code>{proc.shortLabel || activeProcess}</code>
                          {" "}control-ID prefix. The result lands here for review — it is not written to the module.
                        </div>
                      </div>
                    ) : (
                      <>
                        {docDetail.conversions.length > 1 && (
                          <div className="pac-conv-tabs">
                            {docDetail.conversions.map((c, i) => (
                              <button key={c.id}
                                className={"pac-conv-tab" + (selConvId === c.id ? " active" : "")}
                                onClick={() => { setSelConvId(c.id); setConvDraft(c.draft_rego || ""); setConvOrig(c.draft_rego || ""); setRevMsg(null); }}>
                                Draft {docDetail.conversions.length - i}
                                <span className={"pac-conv-status " + c.status}>{c.status.replace("_", " ")}</span>
                              </button>
                            ))}
                          </div>
                        )}

                        {selConv && (
                          <>
                            <div className="pac-rego-head">
                              <span className={"pac-syntax-badge " + (selConv.syntax_valid ? "ok" : "bad")}
                                title={selConv.syntax_valid ? "Passes Rego validation" : (selConv.syntax_errors || []).join("; ")}>
                                {selConv.syntax_valid ? "✓ Valid Rego" : "✗ Invalid Rego"}
                              </span>
                              {(selConv.control_ids || []).length > 0 && (
                                <span className="mono" style={{ fontSize:9.5, color:"var(--ink-3)" }}>
                                  {selConv.control_ids.join(", ")}
                                </span>
                              )}
                              {convDirty && <span className="dirty-dot" title="Unsaved edits">●</span>}
                              <span style={{ flex:1 }} />
                              {selConv.model && <span className="mono" style={{ fontSize:9, color:"var(--ink-3)" }}>{selConv.model}</span>}
                              <button className="btn btn-sm" style={{ fontSize:9.5 }} onClick={() => setShowGen(v => !v)}
                                title="The untouched model output, kept for audit — your edits are diffed against it">
                                {showGen ? "Hide original" : "Original output"}
                              </button>
                            </div>

                            {!selConv.syntax_valid && (selConv.syntax_errors || []).length > 0 && (
                              <div className="code-status err mono" style={{ fontSize:9.5, margin:"0 12px", lineHeight:1.5 }}>
                                {selConv.syntax_errors.join("; ")}
                              </div>
                            )}

                            {showGen ? (
                              <pre className="pac-docs-prose mono" style={{ fontSize:10.5 }}>{selConv.generated_rego}</pre>
                            ) : (
                              <textarea className="code-editor mono" spellCheck={false}
                                value={convDraft} onChange={e => setConvDraft(e.target.value)}
                                readOnly={!convOpen}
                                title={convOpen ? "" : "This conversion is closed — its text is frozen as reviewed"}
                                style={{ flex:1, resize:"none", fontSize:11, lineHeight:1.6, padding:"10px 14px",
                                         opacity: convOpen ? 1 : 0.75 }} />
                            )}

                            {convOpen ? (
                              <div className="pac-docs-decision">
                                <div className="pac-docs-decision-row">
                                  <input className="code-input" value={revRole} placeholder="Your role (e.g. Control Owner)"
                                    onChange={e => setRevRole(e.target.value)} style={{ maxWidth:200 }} />
                                  <input className="code-input" value={revNotes} placeholder="Review notes — required to reject or request changes"
                                    onChange={e => setRevNotes(e.target.value)} style={{ flex:1 }} />
                                </div>
                                <div className="pac-docs-decision-row">
                                  <button className="btn btn-sm" onClick={handleSaveDraft} disabled={!convDirty || savingDraft}>
                                    {savingDraft ? "Saving…" : "Save Draft"}
                                  </button>
                                  <span style={{ flex:1 }} />
                                  <button className="btn btn-sm" onClick={() => handleDecision("request_changes")} disabled={deciding}>
                                    Request Changes
                                  </button>
                                  <button className="btn btn-sm btn-danger" onClick={() => handleDecision("reject")} disabled={deciding}>
                                    Reject
                                  </button>
                                  <button className="btn btn-sm btn-acc" onClick={() => handleDecision("approve")}
                                    disabled={deciding || !selConv.syntax_valid || convDirty}
                                    title={!selConv.syntax_valid ? "Invalid Rego cannot be published — fix and save the draft first"
                                          : convDirty ? "Save your edits first" : "Publish as a new module version"}>
                                    {deciding ? "Working…" : "Approve & Publish"}
                                  </button>
                                </div>
                                <div style={{ fontSize:9.5, color:"var(--ink-3)", lineHeight:1.6 }}>
                                  Signing as <strong>{reviewerName || "— not signed in —"}</strong>. Approving publishes this
                                  text as a new version of <code className="mono">controls.oracle_fusion.{activeProcess}</code>.
                                  That attests the Rego faithfully implements the policy above — the module still needs its
                                  own sign-off in the Rego Editor.
                                </div>
                              </div>
                            ) : (
                              <div className="pac-docs-decision">
                                <div style={{ fontSize:10.5, lineHeight:1.7 }}>
                                  <strong>{selConv.status === "approved" ? "Approved" : "Rejected"}</strong>
                                  {selConv.reviewer ? ` by ${selConv.reviewer}` : ""}
                                  {selConv.reviewer_role ? ` (${selConv.reviewer_role})` : ""}
                                  {selConv.reviewed_at ? ` on ${new Date(selConv.reviewed_at).toLocaleString()}` : ""}
                                  {selConv.published_module_id ? ` · published as module #${selConv.published_module_id}` : ""}
                                  {selConv.review_notes && (
                                    <div style={{ color:"var(--ink-3)", marginTop:4 }}>“{selConv.review_notes}”</div>
                                  )}
                                </div>
                              </div>
                            )}

                            {revMsg && (
                              <div className={"code-status " + (revMsg.kind === "ok" ? "ok" : revMsg.kind === "warn" ? "warn" : "err")}
                                style={{ fontSize:10, margin:"0 12px 12px", lineHeight:1.55 }}>
                                {revMsg.msg}
                              </div>
                            )}
                          </>
                        )}
                      </>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* ── TAB 3: External Sources ── */}
      {mainTab === "sources" && (
        <div className="pac-sources-grid">
          {/* GitHub */}
          <div className="pac-hook-card">
            <div className="pac-hook-title">
              <svg width={16} height={16} viewBox="0 0 24 24" fill="currentColor" style={{ flexShrink:0 }}>
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
              </svg>
              GitHub
              <span className="pac-hook-badge github">GIT</span>
              {ghSaved && <span className="pac-hook-saved">✓ Connected</span>}
            </div>
            {[
              { label:"Repository URL", key:"repo_url", ph:"https://github.com/org/policies" },
              { label:"Branch",         key:"branch",   ph:"main" },
              { label:"Path Filter",    key:"path_filter", ph:"policies/oracle/" },
              { label:"Personal Access Token", key:"pat", ph:"ghp_••••••••••••••••", type:"password" },
            ].map(({ label, key, ph, type }) => (
              <div key={key} className="pac-hook-input-row">
                <label>{label}</label>
                <input className="code-input mono" type={type||"text"}
                  value={ghConfig[key]||""} placeholder={ph}
                  onChange={e => setGhConfig(c => ({ ...c, [key]:e.target.value }))} />
              </div>
            ))}
            <div className="pac-hook-actions">
              <button className="btn btn-sm btn-acc" onClick={() => saveHook("github")}>Save &amp; Connect</button>
              <button className="btn btn-sm" disabled={!ghSaved || ghSyncing} onClick={syncGithubNow}
                title={ghSaved ? "Pull .rego files from the configured repo path" : "Save the connection first"}>
                {ghSyncing ? "Syncing…" : "Sync Now"}
              </button>
              {hookMsg.github && (
                <span style={{ fontSize:10, alignSelf:"center",
                  color:hookMsg.github.startsWith("✓") ? "var(--acc)" : "var(--red)" }}>
                  {hookMsg.github}
                </span>
              )}
            </div>
            {ghSyncResult && (
              <div className="mono" style={{
                fontSize:10, lineHeight:1.6, padding:"8px 10px", borderRadius:6,
                background: ghSyncResult.error ? "var(--red-soft, rgba(239,68,68,0.08))" : "var(--surface-2, var(--surface))",
                border:"1px solid var(--line)", color: ghSyncResult.error ? "var(--red)" : "var(--ink-2)",
              }}>
                {ghSyncResult.error ? (
                  <>Sync failed: {ghSyncResult.error}</>
                ) : (
                  <>
                    Found {ghSyncResult.files_found} file{ghSyncResult.files_found === 1 ? "" : "s"} (.rego/.md/.txt) in {ghSyncResult.repo}@{ghSyncResult.branch}:{ghSyncResult.path}
                    {ghSyncResult.imported?.length > 0 && (
                      <div>✓ Imported: {ghSyncResult.imported.map(m => `${m.process}${ghSyncResult.newly_registered?.includes(m.process) ? " (new tab)" : ""} (${m.file_count} file${m.file_count === 1 ? "" : "s"})`).join(", ")}</div>
                    )}
                    {ghSyncResult.newly_registered?.length > 0 && (
                      <div style={{ color: "var(--acc-ink)" }}>
                        + {ghSyncResult.newly_registered.length} new process tab{ghSyncResult.newly_registered.length === 1 ? "" : "s"} added: {ghSyncResult.newly_registered.join(", ")}
                      </div>
                    )}
                    {ghSyncResult.skipped?.length > 0 && (
                      <div style={{ color:"var(--amber-ink, #b45309)" }}>
                        Skipped: {ghSyncResult.skipped.map(s => `${s.name} (${s.reason})`).join("; ")}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
            <p style={{ fontSize:10, color:"var(--ink-3)", lineHeight:1.6, margin:0 }}>
              Syncs plain text, Markdown, or Rego policy files from the specified GitHub repo path. Token is stored server-side and persists until updated.
              "Sync Now" recursively scans the repo path for <code>.rego</code>/<code>.md</code>/<code>.txt</code> files and matches each one to a process by
              filename or containing folder (e.g. <code>itgc.rego</code> or <code>ITGC/access-management.md</code> both resolve to the ITGC process).
              Multiple matching files are combined into that process's module.
            </p>
          </div>

          {/* Confluence */}
          <div className="pac-hook-card">
            <div className="pac-hook-title">
              <svg width={16} height={16} viewBox="0 0 24 24" fill="#0052CC" style={{ flexShrink:0 }}>
                <path d="M.89 17.27c-.3.48-.65 1.12-.89 1.52a.41.41 0 00.14.56l3.54 2.17a.41.41 0 00.56-.14c.22-.36.56-.93.93-1.53C7.15 17.29 8.37 17 9.5 17.5l6.54 3.08a.41.41 0 00.54-.2l1.76-3.74a.41.41 0 00-.2-.54l-6.61-3.11C8.24 11.6 3.58 12.37.89 17.27zm22.22-9.54c.3-.48.65-1.12.89-1.52a.41.41 0 00-.14-.56L20.32 3.48a.41.41 0 00-.56.14c-.22.36-.56.93-.93 1.53-2.01 2.56-3.23 2.29-4.36 1.79L8.93 3.86a.41.41 0 00-.54.2L6.63 7.8a.41.41 0 00.2.54l6.61 3.11c3.29 1.39 7.95.62 10.67-4.28z"/>
              </svg>
              Confluence
              <span className="pac-hook-badge confluence">WIKI</span>
              {cfSaved && <span className="pac-hook-saved">✓ Connected</span>}
            </div>
            {[
              { label:"Base URL",   key:"base_url",  ph:"https://yourorg.atlassian.net/wiki" },
              { label:"Space Key",  key:"space_key", ph:"RISK" },
              { label:"API Token",  key:"api_token", ph:"••••••••••••••••", type:"password" },
            ].map(({ label, key, ph, type }) => (
              <div key={key} className="pac-hook-input-row">
                <label>{label}</label>
                <input className="code-input mono" type={type||"text"}
                  value={cfConfig[key]||""} placeholder={ph}
                  onChange={e => setCfConfig(c => ({ ...c, [key]:e.target.value }))} />
              </div>
            ))}
            <div className="pac-hook-actions">
              <button className="btn btn-sm btn-acc" onClick={() => saveHook("confluence")}>Save &amp; Connect</button>
              {hookMsg.confluence && (
                <span style={{ fontSize:10, alignSelf:"center",
                  color:hookMsg.confluence.startsWith("✓") ? "var(--acc)" : "var(--red)" }}>
                  {hookMsg.confluence}
                </span>
              )}
            </div>
            <p style={{ fontSize:10, color:"var(--ink-3)", lineHeight:1.6, margin:0 }}>
              Pulls plain text or Markdown pages from the given Confluence space. Token is stored server-side and persists until updated.
            </p>
          </div>
        </div>
      )}

      {/* ── TAB 4: Narrative & Flow Map ── */}
      {mainTab === "narrative" && (
        <div className="pac-narrative-wrap">
          <div className="pac-narrative-prose">
            <h3 style={{ color:proc.color }}>{proc.icon} {proc.label} — Policy Narrative</h3>
            <div style={{ borderLeft:`3px solid ${proc.color}`, paddingLeft:16, marginBottom:14, color:"var(--ink-2)" }}>
              {_PROC_NARRATIVES[activeProcess] || "No narrative available."}
            </div>
            <div style={{ display:"flex", flexWrap:"wrap", gap:0 }}>
              {(_PROC_CONTROLS[activeProcess]||[]).map(c => (
                <span key={c} className="pac-ctrl-chip" style={{ borderColor:`${proc.color}44`, color:proc.color }}>
                  <span style={{ width:5, height:5, borderRadius:"50%", background:proc.color, display:"inline-block" }} />
                  {c}
                </span>
              ))}
            </div>
          </div>
          <ProcessFlowMap activeProcess={activeProcess} processes={processes} />
        </div>
      )}

      {/* ── TAB 5: Control Coverage ── */}
      {mainTab === "coverage" && (
        <div style={{ padding:"18px 20px", overflow:"auto" }}>
          {covLoading && <div style={{ fontSize:12, color:"var(--ink-3)" }}>Loading coverage…</div>}
          {covError && <div className="code-status err" style={{ fontSize:11 }}>Failed to load coverage — {covError}</div>}
          {covData && (
            <>
              <div style={{ display:"flex", gap:12, marginBottom:20, flexWrap:"wrap" }}>
                <CoverageStat label="Total Controls" value={covData.total} />
                <CoverageStat label="Policy-Enforced" value={covData.policy_enforced} color="var(--green-ink, #166534)"
                  sub={covData.total ? `${Math.round(100 * covData.policy_enforced / covData.total)}%` : "—"} />
                <CoverageStat label="Manual Only" value={covData.manual_only} color="var(--amber-ink, #b45309)"
                  sub={covData.total ? `${Math.round(100 * covData.manual_only / covData.total)}%` : "—"} />
              </div>

              <div style={{ fontSize:9.5, fontWeight:700, color:"var(--ink-4)", letterSpacing:"0.05em", textTransform:"uppercase", marginBottom:8 }}>
                By Process
              </div>
              <table className="rep-table" style={{ marginBottom:20 }}>
                <thead><tr><th>Process</th><th>Total</th><th>Policy-Enforced</th><th>Coverage</th></tr></thead>
                <tbody>
                  {covData.by_process.map(b => (
                    <tr key={b.process}>
                      <td className="mono" style={{ fontSize:11 }}>{b.process}</td>
                      <td>{b.total}</td>
                      <td>{b.policy_enforced}</td>
                      <td className="mono" style={{ fontSize:11 }}>{b.total ? `${Math.round(100 * b.policy_enforced / b.total)}%` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div style={{ fontSize:9.5, fontWeight:700, color:"var(--ink-4)", letterSpacing:"0.05em", textTransform:"uppercase", marginBottom:8 }}>
                All Controls · {covData.controls.length}
              </div>
              <table className="rep-table">
                <thead><tr><th>Control ID</th><th>Name</th><th>Process</th><th>Source</th></tr></thead>
                <tbody>
                  {covData.controls.map(c => (
                    <tr key={c.control_id}>
                      <td className="mono" style={{ fontSize:10.5 }}>{c.control_id}</td>
                      <td style={{ fontSize:11.5 }}>{c.name}</td>
                      <td className="mono" style={{ fontSize:10.5, color:"var(--ink-3)" }}>{c.process || "—"}</td>
                      <td>
                        <span className="mono" style={{
                          fontSize:9.5, padding:"1px 6px", borderRadius:4,
                          background: c.source === "pac_rego" ? "var(--green-soft)" : "var(--amber-soft)",
                          color: c.source === "pac_rego" ? "var(--green-ink)" : "var(--amber-ink)",
                        }}>
                          {c.source === "pac_rego" ? "policy-enforced" : "manual"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {drifted.length > 0 && (
                <div style={{
                  marginTop: 20, padding: "10px 14px", borderRadius: 6, fontSize: 11.5, lineHeight: 1.5,
                  background: "var(--red-soft, rgba(239,68,68,0.08))", color: "var(--red-ink, #991b1b)",
                  border: "1px solid var(--red-ink, #991b1b)",
                }}>
                  <strong>Approval drift detected</strong> — the module currently evaluating real events differs
                  from the last approved version for: {drifted.map(d => d.process).join(", ")}.
                  {" "}Saving a draft goes live immediately; approval doesn't gate evaluation today (see the
                  Policy-as-Code editor's approval workflow). Review and re-approve before relying on these processes.
                </div>
              )}

              <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", marginTop:28, marginBottom:8 }}>
                <div style={{ fontSize:9.5, fontWeight:700, color:"var(--ink-4)", letterSpacing:"0.05em", textTransform:"uppercase" }}>
                  Executive Compliance Scorecard
                </div>
                <select className="code-input" value={scorecardFramework}
                  onChange={e => setScorecardFramework(e.target.value)}
                  style={{ fontSize:11, padding:"3px 8px", width:"auto" }}>
                  {SCORECARD_FRAMEWORKS.map(f => <option key={f.id} value={f.id}>{f.label}</option>)}
                </select>
              </div>
              <div style={{ fontSize:11, color:"var(--ink-3)", marginBottom:10, lineHeight:1.5 }}>
                Curated framework crosswalk (never auto-generated — see <code>framework_mappings.py</code>), against
                the same negative-testing assurance metadata as the Negative Testing tab. "Mapped" and "verified" are
                deliberately separate numbers: a criterion can be fully mapped and still show 0% verified if nothing
                currently proves those controls work.
              </div>
              {scorecardFramework === "soc2" && doraData && (
                <div style={{
                  display: "flex", gap: 20, flexWrap: "wrap", marginBottom: 14,
                  padding: "10px 14px", borderRadius: 6, border: "1px solid var(--line)",
                }}>
                  <div style={{ fontSize: 9.5, fontWeight: 700, color: "var(--ink-4)", letterSpacing: "0.05em",
                                 textTransform: "uppercase", width: "100%" }}>
                    Change Management Evidence (DORA, trailing {doraData.window_days}d) — CC8.1
                  </div>
                  <div>
                    <div style={{ fontSize: 9.5, color: "var(--ink-4)" }}>Deploy Frequency</div>
                    <div style={{ fontSize: 15, fontFamily: "var(--mono)" }}>
                      {doraData.deployment_frequency_per_day != null ? `${doraData.deployment_frequency_per_day}/day` : "—"}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 9.5, color: "var(--ink-4)" }}>Change Failure Rate</div>
                    <div style={{ fontSize: 15, fontFamily: "var(--mono)" }}>
                      {doraData.change_failure_rate != null ? `${Math.round(doraData.change_failure_rate * 100)}%` : "—"}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 9.5, color: "var(--ink-4)" }}>MTTR</div>
                    <div style={{ fontSize: 15, fontFamily: "var(--mono)" }}>
                      {doraData.mttr_hours != null ? `${doraData.mttr_hours}h` : "—"}
                    </div>
                  </div>
                </div>
              )}

              {scorecardLoading && <div style={{ fontSize:12, color:"var(--ink-3)" }}>Loading scorecard…</div>}
              {scorecardError && <div className="code-status err" style={{ fontSize:11 }}>Failed to load scorecard — {scorecardError}</div>}
              {scorecardData && !scorecardData.criteria?.length && !scorecardLoading && (
                <div style={{ fontSize:11.5, color:"var(--ink-3)" }}>No controls currently mapped to this framework.</div>
              )}
              {scorecardData && scorecardData.criteria?.length > 0 && (
                <>
                  <div style={{ display:"flex", gap:12, marginBottom:14, flexWrap:"wrap" }}>
                    <CoverageStat label="Criteria Covered" value={scorecardData.total_criteria} />
                    <CoverageStat label="Fully Verified" value={scorecardData.fully_verified_criteria} color="var(--green-ink, #166534)"
                      sub={scorecardData.total_criteria ? `${Math.round(100 * scorecardData.fully_verified_criteria / scorecardData.total_criteria)}%` : "—"} />
                  </div>
                  <table className="rep-table">
                    <thead><tr><th>Criterion</th><th>Mapped Controls</th><th>Verified</th><th>Coverage</th></tr></thead>
                    <tbody>
                      {scorecardData.criteria.map(c => (
                        <tr key={c.criterion}>
                          <td className="mono" style={{ fontSize:11 }}>{c.criterion}</td>
                          <td className="mono" style={{ fontSize:10.5 }}>{c.control_ids.join(", ")}</td>
                          <td>{c.verified_controls} / {c.total_controls}</td>
                          <td>
                            <span className="mono" style={{
                              fontSize:9.5, padding:"1px 6px", borderRadius:4, fontWeight:700,
                              background: c.verified_controls === c.total_controls ? "var(--green-soft)" : "var(--amber-soft)",
                              color: c.verified_controls === c.total_controls ? "var(--green-ink)" : "var(--amber-ink)",
                            }}>
                              {c.total_controls ? `${Math.round(100 * c.verified_controls / c.total_controls)}%` : "—"}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </>
          )}
        </div>
      )}

      {/* ── TAB 6: Negative Testing ── */}
      {mainTab === "negtest" && (
        <div style={{ padding:"18px 20px", overflow:"auto" }}>
          <div style={{ display:"flex", alignItems:"flex-start", justifyContent:"space-between", gap:12, marginBottom:16 }}>
            <div style={{ fontSize:11.5, color:"var(--ink-3)", maxWidth:640, lineHeight:1.5 }}>
              Runs two checks against the Rego currently in the editor (including an unsaved draft):
              a schema-contract check — does every field/event-type it references correspond to
              something the real adjudication pipeline actually produces — and a must-fire/must-not-fire
              fixture corpus, evaluated through the real OPA engine when available. A rule that reads as
              enforcing something but can never fire is worse than no rule at all.
            </div>
            <button className="btn btn-sm btn-acc" onClick={handleRunNegativeTests} disabled={ntRunning || !rego.trim()}>
              {ntRunning ? "Running…" : "Run Negative Tests"}
            </button>
          </div>

          {ntError && <div className="code-status err" style={{ fontSize:11, marginBottom:12 }}>Test run failed — {ntError}</div>}

          {ntResult && (
            <div style={{ marginBottom:24 }}>
              <div style={{ display:"flex", gap:12, marginBottom:14, flexWrap:"wrap" }}>
                <CoverageStat label="Overall" value={ntResult.ok ? "PASS" : "FAIL"}
                  color={ntResult.ok ? "var(--green-ink, #166534)" : "var(--red-ink, #b91c1c)"} />
                <CoverageStat label="Schema Contract" value={ntResult.contract?.ok ? "OK" : "FAILED"}
                  color={ntResult.contract?.ok ? "var(--green-ink, #166534)" : "var(--red-ink, #b91c1c)"} />
                <CoverageStat label="Corpus" value={
                  ntResult.corpus?.ok === null ? "no corpus" : `${ntResult.corpus?.passed ?? 0} / ${ntResult.corpus?.total ?? 0}`
                } color={ntResult.corpus?.ok === false ? "var(--red-ink, #b91c1c)" : undefined} />
              </div>

              {ntResult.contract && ntResult.contract.findings?.length > 0 && (
                <>
                  <div style={{ fontSize:9.5, fontWeight:700, color:"var(--ink-4)", letterSpacing:"0.05em", textTransform:"uppercase", marginBottom:8 }}>
                    Schema-Contract Findings
                  </div>
                  <ul style={{ fontSize:11, color:"var(--red-ink, #b91c1c)", marginBottom:18, paddingLeft:18, lineHeight:1.6 }}>
                    {ntResult.contract.findings.map((f, i) => <li key={i}>{f}</li>)}
                  </ul>
                </>
              )}

              {ntResult.corpus?.results?.length > 0 && (
                <>
                  <div style={{ fontSize:9.5, fontWeight:700, color:"var(--ink-4)", letterSpacing:"0.05em", textTransform:"uppercase", marginBottom:8 }}>
                    Fixture Results
                  </div>
                  <table className="rep-table" style={{ marginBottom:18 }}>
                    <thead><tr><th>Fixture</th><th>Expect</th><th>Result</th><th>Fired Control(s)</th></tr></thead>
                    <tbody>
                      {ntResult.corpus.results.map(r => (
                        <tr key={r.name}>
                          <td style={{ fontSize:11 }}>{r.name}</td>
                          <td className="mono" style={{ fontSize:10.5, color:"var(--ink-3)" }}>{r.expect}{r.expected_control_id ? ` (${r.expected_control_id})` : ""}</td>
                          <td>
                            <span className="mono" style={{
                              fontSize:9.5, padding:"1px 6px", borderRadius:4, fontWeight:700,
                              background: r.passed ? "var(--green-soft)" : "var(--red-soft, #fee2e2)",
                              color: r.passed ? "var(--green-ink)" : "var(--red-ink, #b91c1c)",
                            }}>
                              {r.passed ? "PASS" : "FAIL"}
                            </span>
                          </td>
                          <td className="mono" style={{ fontSize:10.5 }}>{r.fired_control_ids?.join(", ") || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
              {ntResult.corpus?.note && (
                <div style={{ fontSize:11, color:"var(--ink-3)", marginBottom:18 }}>{ntResult.corpus.note}</div>
              )}
            </div>
          )}

          <div style={{ fontSize:9.5, fontWeight:700, color:"var(--ink-4)", letterSpacing:"0.05em", textTransform:"uppercase", marginBottom:8 }}>
            Unverified Controls — {proc.label}
          </div>
          <div style={{ fontSize:11, color:"var(--ink-3)", marginBottom:10 }}>
            Policy-enforced controls with neither a recent real production fire nor a passing
            negative-control test — nothing currently proves these work.
          </div>
          {assuranceLoad && <div style={{ fontSize:12, color:"var(--ink-3)" }}>Loading…</div>}
          {assurance && !assurance.unverified?.length && !assuranceLoad && (
            <div style={{ fontSize:11.5, color:"var(--green-ink, #166534)", marginBottom:20 }}>
              Every policy-enforced control for this process has recent evidence it works.
            </div>
          )}
          {assurance && assurance.unverified?.length > 0 && (
            <table className="rep-table" style={{ marginBottom:20 }}>
              <thead><tr><th>Control ID</th><th>Name</th><th>Last Fired</th><th>Last Verified</th></tr></thead>
              <tbody>
                {assurance.unverified.map(c => (
                  <tr key={c.control_id}>
                    <td className="mono" style={{ fontSize:10.5 }}>{c.control_id}</td>
                    <td style={{ fontSize:11.5 }}>{c.name}</td>
                    <td className="mono" style={{ fontSize:10.5, color:"var(--ink-3)" }}>{c.last_fired_at ? new Date(c.last_fired_at).toLocaleDateString() : "never"}</td>
                    <td className="mono" style={{ fontSize:10.5, color:"var(--ink-3)" }}>{c.last_verified_at ? new Date(c.last_verified_at).toLocaleDateString() : "never"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div style={{ fontSize:9.5, fontWeight:700, color:"var(--ink-4)", letterSpacing:"0.05em", textTransform:"uppercase", marginBottom:8 }}>
            Test Run History
          </div>
          {ntHistoryLoad && <div style={{ fontSize:12, color:"var(--ink-3)" }}>Loading…</div>}
          {ntHistory && !ntHistory.length && !ntHistoryLoad && <div style={{ fontSize:11.5, color:"var(--ink-3)" }}>No negative-control tests run yet for this process.</div>}
          {ntHistory && ntHistory.length > 0 && (
            <table className="rep-table">
              <thead><tr><th>Run At</th><th>Triggered By</th><th>Contract</th><th>Corpus</th></tr></thead>
              <tbody>
                {ntHistory.map(run => (
                  <tr key={run.id}>
                    <td className="mono" style={{ fontSize:10.5, color:"var(--ink-3)" }}>{new Date(run.run_at).toLocaleString()}</td>
                    <td style={{ fontSize:11 }}>{run.triggered_by}{run.triggered_by_user ? ` (${run.triggered_by_user})` : ""}</td>
                    <td>
                      <span className="mono" style={{
                        fontSize:9.5, padding:"1px 6px", borderRadius:4, fontWeight:700,
                        background: run.contract_ok ? "var(--green-soft)" : "var(--red-soft, #fee2e2)",
                        color: run.contract_ok ? "var(--green-ink)" : "var(--red-ink, #b91c1c)",
                      }}>
                        {run.contract_ok ? "OK" : "FAILED"}
                      </span>
                    </td>
                    <td className="mono" style={{ fontSize:10.5 }}>{run.passed} / {run.total}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Approver modal */}
      {showApprove && (
        <div className="pac-modal-overlay" onClick={() => { setShowApprove(false); setAppErr(null); }}>
          <div className="pac-modal" onClick={e => e.stopPropagation()}>
            <h4>Sign Off — {proc.label}</h4>
            <div className="pac-modal-row">
              <label>Approver Name *</label>
              <input className="code-input" value={appName} placeholder="Jane Smith" autoFocus
                onChange={e => setAppName(e.target.value)} />
            </div>
            <div className="pac-modal-row">
              <label>Role</label>
              <input className="code-input" value={appRole} placeholder="IT Audit Manager"
                onChange={e => setAppRole(e.target.value)} />
            </div>
            {appErr && <div className="pac-modal-err">{appErr}</div>}
            <div className="pac-modal-actions">
              <button className="btn btn-sm" onClick={() => { setShowApprove(false); setAppErr(null); }}>Cancel</button>
              <button className="btn btn-sm btn-acc" onClick={handleApprove} disabled={!appName.trim()}>
                Confirm Sign-Off
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { CodeEditorScreen, RiskAsCodeScreen, PolicyAsCodeScreen });
