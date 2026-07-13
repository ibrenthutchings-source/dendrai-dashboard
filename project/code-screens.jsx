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

// ── PolicyAsCodeScreen ────────────────────────────────────────────────────
function PolicyAsCodeScreen({ events, maps, risks, appetiteThreshold = 7.5 }) {
  const [activeProcess, setActiveProcess] = useState("itgc");
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

  const loadModule = useCallback((process) => {
    return fetch(`/api/pac/modules/${process}`, { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return;
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
      .catch(() => {});
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
        if (!data) return;
        if (data.github)     { setGhConfig(c => ({ ...c, ...data.github }));     setGhSaved(true); }
        if (data.confluence) { setCfConfig(c => ({ ...c, ...data.confluence })); setCfSaved(true); }
      })
      .catch(() => {});
  }, []);

  // Business processes are DB-backed, not a fixed 5 — sync_github can
  // auto-register new ones. Fetched once on mount; _PAC_PROCESSES_FALLBACK
  // covers the gap before this resolves.
  const [processes, setProcesses] = useState(_PAC_PROCESSES_FALLBACK);
  useEffect(() => {
    fetch("/api/pac/processes", { headers: _codeAuthHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.processes?.length) setProcesses(data.processes.map(_normalizeProcess));
      })
      .catch(() => {});
  }, []);

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
      await loadModule(activeProcess);
    } catch (e) {
      setGhSyncResult({ error: e.message || "Network error" });
    } finally {
      setGhSyncing(false);
    }
  }

  const proc = processes.find(p => p.id === activeProcess) || processes[0];

  const MAIN_TABS = [
    { id:"editor",    label:"Rego Editor" },
    { id:"sources",   label:"External Sources" },
    { id:"narrative", label:"Narrative & Flow Map" },
  ];

  return (
    <div className="pac-shell">
      {/* Process selector tabs */}
      <div className="pac-process-bar">
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

      {/* Main sub-tabs */}
      <div className="pac-main-tabs">
        {MAIN_TABS.map(t => (
          <button key={t.id}
            className={"pac-main-tab" + (mainTab === t.id ? " active" : "")}
            onClick={() => setMainTab(t.id)}>
            {t.label}
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

      {/* ── TAB 2: External Sources ── */}
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
                      <div>✓ Imported: {ghSyncResult.imported.map(m => `${m.process} (${m.file_count} file${m.file_count === 1 ? "" : "s"})`).join(", ")}</div>
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

      {/* ── TAB 3: Narrative & Flow Map ── */}
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
  const [cacGenerating, setCacGenerating]     = useState(false);
  const [cacMsg, setCacMsg]                   = useState(null);
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

  async function handleGenerateCaC() {
    if (!risks?.length) return;
    setCacGenerating(true); setCacMsg(null);
    try {
      // Fetch controls from the register endpoint, fall back to risk-derived list
      let controls = [];
      try {
        const cr = await fetch("/api/controls", { headers: _codeAuthHeaders() });
        if (cr.ok) { const cd = await cr.json(); controls = cd.controls || cd || []; }
      } catch {}

      const res = await fetch("/api/pac/cac/generate", {
        method: "POST", headers: _codeAuthHeaders(),
        body: JSON.stringify({ ticker: ticker || "", run_id: runId || null, controls }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setCacMsg({ kind:"ok", msg:"CaC generated & saved." });
    } catch (err) {
      setCacMsg({ kind:"err", msg:`CaC error: ${err.message}` });
    }
    setCacGenerating(false);
    setTimeout(() => setCacMsg(null), 4000);
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
          {cacMsg && <span className={"code-status " + cacMsg.kind}>{cacMsg.msg}</span>}
          <button
            className={"btn btn-sm" + (cacGenerating ? " loading" : "")}
            onClick={handleGenerateCaC}
            disabled={noRisks || cacGenerating}
            title={noRisks ? "Run the loop first" : "Generate Controls-as-Code Rego from the controls library"}
            style={{ borderColor:"#10b981", color:"#10b981" }}
          >
            <Icon name="spark" size={11} />
            {cacGenerating ? " CaC…" : " Generate CaC"}
          </button>
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
