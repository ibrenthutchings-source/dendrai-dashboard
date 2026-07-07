/* ============================================================
   Shared UI primitives — Geist-styled enterprise SaaS
   Exposed on window so other Babel scripts can use them.
   ============================================================ */

const { useState, useEffect, useRef, useMemo, useCallback } = React;

// ---- Icons (tiny inline SVGs, stroke-based, minimal) ----
function Icon({ name, size = 14, className = "" }) {
  const s = size;
  const stroke = "currentColor";
  const sw = 1.5;
  const common = { width: s, height: s, viewBox: "0 0 16 16", fill: "none",
    stroke, strokeWidth: sw, strokeLinecap: "round", strokeLinejoin: "round",
    className };
  switch (name) {
    case "play":     return <svg {...common}><path d="M4 3l9 5-9 5V3z" fill={stroke} stroke="none"/></svg>;
    case "pause":    return <svg {...common}><path d="M5 3v10M11 3v10"/></svg>;
    case "reset":    return <svg {...common}><path d="M2 8a6 6 0 1 0 1.5-4M2 3v3h3"/></svg>;
    case "check":    return <svg {...common}><path d="M3 8.5l3 3 7-7"/></svg>;
    case "alert":    return <svg {...common}><path d="M8 1.5l7 13H1l7-13z"/><path d="M8 6v3M8 11.5v.01"/></svg>;
    case "chev-d":   return <svg {...common}><path d="M3 5.5l5 5 5-5"/></svg>;
    case "chev-r":   return <svg {...common}><path d="M5.5 3l5 5-5 5"/></svg>;
    case "chev-u":   return <svg {...common}><path d="M3 10.5l5-5 5 5"/></svg>;
    case "x":        return <svg {...common}><path d="M3 3l10 10M13 3L3 13"/></svg>;
    case "spark":    return <svg {...common}><path d="M2 11l3-4 3 2 3-5 3 3"/></svg>;
    case "arrow-r":  return <svg {...common}><path d="M3 8h10M9 4l4 4-4 4"/></svg>;
    case "arrow-up": return <svg {...common}><path d="M8 13V3M4 7l4-4 4 4"/></svg>;
    case "arrow-dn": return <svg {...common}><path d="M8 3v10M4 9l4 4 4-4"/></svg>;
    case "doc":      return <svg {...common}><path d="M3 1.5h7l3 3v10H3v-13z"/><path d="M10 1.5v3h3"/></svg>;
    case "table":    return <svg {...common}><rect x="2" y="3" width="12" height="10" rx="1"/><path d="M2 7h12M2 10h12M6 3v10"/></svg>;
    case "grid":     return <svg {...common}><rect x="2" y="2" width="5" height="5"/><rect x="9" y="2" width="5" height="5"/><rect x="2" y="9" width="5" height="5"/><rect x="9" y="9" width="5" height="5"/></svg>;
    case "list":     return <svg {...common}><path d="M5 4h9M5 8h9M5 12h9M2 4h.01M2 8h.01M2 12h.01"/></svg>;
    case "user":     return <svg {...common}><circle cx="8" cy="5.5" r="2.5"/><path d="M2 14c1-3 4-4 6-4s5 1 6 4"/></svg>;
    case "bolt":     return <svg {...common}><path d="M9 1L3 9h4l-1 6 6-8H8l1-6z"/></svg>;
    case "satellite":return <svg {...common}><circle cx="8" cy="8" r="2"/><path d="M2 8a6 6 0 0 1 6-6M2 4a10 10 0 0 1 10 10M14 8a6 6 0 0 1-6 6"/></svg>;
    case "flow":     return <svg {...common}><rect x="2" y="2" width="4" height="4"/><rect x="10" y="6" width="4" height="4"/><rect x="2" y="10" width="4" height="4"/><path d="M6 4h2a2 2 0 0 1 2 2v2M6 12h2a2 2 0 0 0 2-2v-2"/></svg>;
    case "shield":   return <svg {...common}><path d="M8 1.5L2 4v5c0 3.5 2.5 5 6 5.5 3.5-.5 6-2 6-5.5V4l-6-2.5z"/></svg>;
    case "trend":    return <svg {...common}><path d="M2 12l4-4 3 3 5-7"/></svg>;
    case "code":     return <svg {...common}><path d="M5 4L1 8l4 4M11 4l4 4-4 4M9 2l-2 12"/></svg>;
    case "compass":  return <svg {...common}><circle cx="8" cy="8" r="6"/><path d="M10.5 5.5L9 9l-3.5 1.5L7 7l3.5-1.5z" fill={stroke} stroke="none"/></svg>;
    case "download": return <svg {...common}><path d="M8 1v9M4 7l4 4 4-4M2 14h12"/></svg>;
    case "wifi":     return <svg {...common}><path d="M1.5 5.5a10 10 0 0 1 13 0M3.5 8a7 7 0 0 1 9 0M5.5 10.5a4 4 0 0 1 5 0"/><circle cx="8" cy="13" r=".5" fill={stroke}/></svg>;
    case "plus":     return <svg {...common}><path d="M8 2v12M2 8h12"/></svg>;
    case "edit":     return <svg {...common}><path d="M11 2l3 3-8 8H3v-3l8-8z"/></svg>;
    case "logout":   return <svg {...common}><path d="M6 2H3.5A1.5 1.5 0 0 0 2 3.5v9A1.5 1.5 0 0 0 3.5 14H6"/><path d="M10.5 11l3-3-3-3M13.2 8H6"/></svg>;
    default:         return null;
  }
}

// ---- Pill / tag ----
function Pill({ tone = "neutral", mono = true, children }) {
  const cls = `pill pill-${tone}` + (mono ? " mono" : "");
  return <span className={cls}>{children}</span>;
}

// ---- RAG chip helpers ----
function RAGChip({ rag, children, mono = true }) {
  return <span className={`rag-chip rag-${rag}` + (mono ? "" : "")}>{children || rag}</span>;
}

// ---- Velocity pill ----
function VelocityPill({ v }) {
  const tone = v > 0 ? "vel-up" : v < 0 ? "vel-dn" : "vel-flat";
  const txt = v > 0 ? `+${v}` : `${v}`;
  return <span className={`mono vel-pill ${tone}`}>{txt}</span>;
}

// ---- Sparkline ----
function Sparkline({ data, w = 60, h = 18, color }) {
  if (!data || !data.length) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;
  const step = w / (data.length - 1);
  const pts = data.map((v, i) => `${(i * step).toFixed(1)},${(h - ((v - min) / range) * (h - 2) - 1).toFixed(1)}`).join(" ");
  return (
    <svg className="spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
      <polyline points={pts} fill="none" stroke={color || "currentColor"} strokeWidth="1.2" strokeLinejoin="round" strokeLinecap="round"/>
      <circle cx={(data.length - 1) * step} cy={h - ((data[data.length - 1] - min) / range) * (h - 2) - 1} r="2" fill={color || "currentColor"}/>
    </svg>
  );
}

// ---- Score → color helper ----
function scoreColor(s) {
  if (s >= 15) return "var(--red)";
  if (s >= 9)  return "var(--amber)";
  return "var(--green)";
}
function scoreColorInk(s) {
  if (s >= 15) return "var(--red-ink)";
  if (s >= 9)  return "var(--amber-ink)";
  return "var(--green-ink)";
}
function ragFromScore(s) { return s >= 15 ? "R" : s >= 9 ? "A" : "G"; }

// ---- Likelihood from control effectiveness (1-5 scale for heatmap) ----
function likelihoodFromCE(ce) {
  return ({ NONE: 4.5, WEAK: 3.5, ADEQUATE: 2.5, STRONG: 1.5 })[ce] || 2.5;
}
function ceMultiplier(ce) {
  return ({ NONE: 1.2, WEAK: 1.1, ADEQUATE: 0.95, STRONG: 0.8 })[ce] || 1.0;
}

// ---- Projection: residual + velocity × dampening × CE multiplier ----
function projectQuarters(risk) {
  const base = risk.score || 5;
  const vel = risk.velocity || 0;
  const cem = ceMultiplier(risk.ce);
  const qs = [];
  for (let q = 1; q <= 4; q++) {
    const velContrib = vel * Math.pow(0.85, q - 1);
    const raw = base + velContrib * cem * 1.0; // calibrated for 0-25 scale
    qs.push(Math.max(1, Math.min(25, raw)));
  }
  return qs;
}

// ---- clamp ----
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function fmt2(n) { return (Math.round(n * 10) / 10).toFixed(1); }
function fmt$M(n) {
  if (n == null) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e9) return `$${(n/1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(n/1e6).toFixed(0)}M`;
  return `$${n.toFixed(0)}`;
}

// ---- Empty state ----
function Empty({ children, icon = "—" }) {
  return (
    <div className="empty">
      <div className="icon">{icon}</div>
      {children}
    </div>
  );
}

// ---- Screen access gate — enforces the per-role Read/Edit matrix configured
// in Configuration > Screen Access (admin-config.jsx / auth.screen_permissions).
// Admins always bypass it. A screen with no saved row is allowed by default.
// Read=false hides the screen entirely; Edit=false renders it but disables
// every form control inside via a native <fieldset disabled> (backed up by a
// pointer-events overlay for non-form interactive elements).
function ScreenAccessGate({ screenId, children }) {
  const auth = window.useAuth ? window.useAuth() : null;
  if (!auth?.user || auth.user.role === "admin") return children;

  const p = (auth.user.screen_permissions || {})[screenId];
  const canRead = !p || p.can_read !== false;
  const canEdit = !p || p.can_edit !== false;

  if (!canRead) {
    return (
      <div className="panel active">
        <Empty>You don't have access to this screen. Contact an administrator if this seems wrong.</Empty>
      </div>
    );
  }
  if (!canEdit) {
    return (
      <div style={{ position: "relative" }}>
        <div className="mono" style={{
          fontSize: 10, padding: "5px 12px", letterSpacing: "0.04em",
          background: "var(--amber-soft)", color: "var(--amber-ink)", borderBottom: "1px solid var(--line)",
        }}>
          VIEW ONLY — your role doesn't have edit access to this screen
        </div>
        <fieldset disabled style={{ border: 0, padding: 0, margin: 0, pointerEvents: "none", opacity: 0.92 }}>
          {children}
        </fieldset>
      </div>
    );
  }
  return children;
}

// ---- Section heading ----
function SectionLabel({ children, right }) {
  return (
    <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom: 10}}>
      <div className="sec-lbl">{children}</div>
      {right}
    </div>
  );
}

// ---- Bloomberg Terminal Header ----
function BBTermHeader({ section, title, status, liveMode, actions }) {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="bb-header">
      <div className="bb-header-topbar">
        <div className="bb-brand">
          <span>◆ DENDRAI</span>
          <span className="bb-brand-sep"> | </span>
          <span className="bb-brand-section">{section}</span>
          {liveMode != null && (
            <span className={`bb-live-pill${liveMode ? "" : " sim"}`}>
              <span className="bb-live-dot"/>
              {liveMode ? "LIVE" : "SIM"}
            </span>
          )}
        </div>
        <div className="bb-clock">
          <span className="bb-clock-time">
            {time.toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false})}
          </span>
          <span className="bb-clock-date">
            {time.toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"}).toUpperCase()}
          </span>
        </div>
      </div>
      <div className="bb-header-main">
        <div style={{flex:1,minWidth:0}}>
          <div className="bb-title">{section}</div>
          <div className="bb-subtitle">{title}</div>
          {status && <div className="bb-status-line">{status}</div>}
        </div>
        {actions && <div className="bb-header-actions">{actions}</div>}
      </div>
    </div>
  );
}

// Expose globally
Object.assign(window, {
  Icon, Pill, RAGChip, VelocityPill, Sparkline,
  scoreColor, scoreColorInk, ragFromScore,
  likelihoodFromCE, ceMultiplier, projectQuarters,
  clamp, fmt2, fmt$M,
  Empty, SectionLabel, BBTermHeader,
  ScreenAccessGate,
});
