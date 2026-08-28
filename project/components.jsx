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
    className, "aria-hidden": "true" };
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

// ---- Brand mark — bold gold "D" monogram on the tile (replaces the
// earlier tree icon per brand sheet update). Rendered as real text with a
// vertical gold-to-bronze gradient fill (background-clip:text) rather than
// a hand-drawn glyph, so it uses the actual font's "D" letterform and
// stays crisp at any size. Same mark used for the nav/header logo chip and
// public/favicon.svg — keep them in sync if this is ever redrawn. ----
function DendraiMark({ size = 16 }) {
  return (
    <span aria-hidden="true" style={{
      fontFamily: "'Geist', sans-serif", fontWeight: 700, fontSize: size * 1.3,
      lineHeight: 1, display: "inline-block",
      background: "linear-gradient(180deg, #D4B483 0%, #A08C52 55%, #7A6438 100%)",
      WebkitBackgroundClip: "text", backgroundClip: "text",
      color: "transparent", WebkitTextFillColor: "transparent",
    }}>D</span>
  );
}

// ---- Wordmark — bold all-caps "DENDR" + gold "AI", with a small leaf
// perched atop the "I" (per the brand sheet's logotype — not a dot
// replacement this time, since a capital I has no dot to begin with; the
// leaf sits beside/above the stroke instead). Real lowercase text plus
// CSS text-transform:uppercase, rather than hardcoded capital letters, so
// the actual DOM text stays "Dendrai" (correct for copy/paste and screen
// readers) while still rendering as the all-caps logotype visually. ----
function DendraiWordmark({ size = 13.5 }) {
  return (
    <span style={{ fontWeight: 700, fontSize: size, letterSpacing: "0.01em", textTransform: "uppercase", color: "var(--ink)", display: "inline-flex", alignItems: "baseline" }}>
      <span>Dendr</span>
      <span style={{ color: "#A08C52" }}>a</span>
      <span style={{ position: "relative", display: "inline-block", color: "#A08C52" }}>
        i
        <svg width={size * 0.48} height={size * 0.48} viewBox="0 0 16 16" style={{
          position: "absolute", right: "-58%", top: "-22%", transform: "rotate(18deg)",
        }} aria-hidden="true">
          <path d="M8 1.5C4.5 1.5 2 4.3 2 8c0 3 2 5 6 6.2C12 13 14 11 14 8c0-3.7-2.5-6.5-6-6.5z" fill="#4CAF59"/>
          <path d="M8 2v11.8" stroke="#2E7D32" strokeWidth="0.8" strokeLinecap="round"/>
        </svg>
      </span>
    </span>
  );
}

// ---- Pill / tag ----
// `tone` picks a status color from the shared palette (good/warn/bad/neutral/acc).
// `ink`/`soft` are an escape hatch for callers with their own color mapping
// (e.g. a RAG or per-priority lookup) that still want the shared pill shape
// instead of hand-rolling one.
function Pill({ tone = "neutral", mono = true, size, ink, soft, children }) {
  const custom = ink || soft;
  const cls = "pill" + (custom ? "" : ` pill-${tone}`) + (mono ? " mono" : "");
  const style = custom || size ? { ...(custom && { color: ink, background: soft }), ...(size && { fontSize: size }) } : undefined;
  return <span className={cls} style={style}>{children}</span>;
}

// ---- RAG chip helpers ----
function RAGChip({ rag, children, mono = true }) {
  return <span className={`rag-chip rag-${rag}` + (mono ? "" : "")}>{children || rag}</span>;
}

// ---- Auditor takeaway strip ----
// Standardized "what this means -> what to do" callout for charts/gauges
// (M-Score, Z-Score, forecasts, peer comparisons). tone drives the accent
// color; actionLabel+onAction render an optional single action button that
// turns the insight into real work (e.g. "-> Add to scope") rather than
// leaving the reader to remember to act on it themselves.
function AuditorTakeaway({ tone = "info", children, actionLabel, onAction }) {
  return (
    <div className={`aud-tk aud-tk-${tone}`}>
      <span className="aud-tk-icon"><Icon name="compass" size={12}/></span>
      <div className="aud-tk-body">
        <span className="aud-tk-label">Auditor takeaway</span>
        <span className="aud-tk-text">{children}</span>
      </div>
      {actionLabel && onAction && (
        <button type="button" className="btn btn-sm aud-tk-action" onClick={onAction}>
          {actionLabel} <Icon name="chev-r" size={10}/>
        </button>
      )}
    </div>
  );
}

// ---- Velocity pill ----
function VelocityPill({ v }) {
  const tone = v > 0 ? "vel-up" : v < 0 ? "vel-dn" : "vel-flat";
  const txt = v > 0 ? `+${v}` : `${v}`;
  return <span className={`mono vel-pill ${tone}`}>{txt}</span>;
}

// ---- Sparkline ----
function Sparkline({ data, w = 60, h = 18, color, min: minProp, max: maxProp }) {
  if (!data || !data.length) return null;
  // min/max default to this row's own data (unchanged standalone behavior),
  // but callers rendering many sparklines side by side for comparison can
  // pass a shared min/max so a flat line actually reads as flat across rows.
  const min = minProp ?? Math.min(...data), max = maxProp ?? Math.max(...data);
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

// ---- AI narrative review banner ----
// persona_brief/audit_report are the two AI endpoints that reach a
// user/board with no gate before delivery (MODEL_CARD.md known limitation
// #3) — every generation now carries a `_review` block the API attaches
// (see ai_endpoints.py) so this banner can show its actual state instead of
// silently looking identical to a reviewed one. Cleared via Approval Inbox's
// AI Narrative Review section, not inline here — this is a status readout,
// not the review action itself.
function AiReviewBanner({ review }) {
  if (!review) return null;
  const reviewed = review.status === "reviewed";
  return (
    <div className="mono" style={{
      fontSize: 10, fontWeight: 700, letterSpacing: "0.05em", padding: "6px 10px", borderRadius: 5,
      marginBottom: 10, display: "flex", alignItems: "center", gap: 6,
      background: reviewed ? "var(--green-soft, #ecfdf5)" : "var(--amber-soft, #fffbeb)",
      color: reviewed ? "var(--green-ink, #047857)" : "var(--amber-ink, #b45309)",
      border: `1px solid ${reviewed ? "var(--green-ink, #047857)" : "var(--amber-ink, #b45309)"}`,
    }}>
      {reviewed
        ? <>✓ REVIEWED{review.reviewed_by_name ? ` BY ${review.reviewed_by_name.toUpperCase()}` : ""}{review.reviewed_at ? ` · ${new Date(review.reviewed_at).toLocaleDateString()}` : ""}</>
        : <>⏳ PENDING REVIEW — not yet cleared for distribution · see Approval Inbox</>}
    </div>
  );
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

// ---- Modal: Escape-to-close, focus trap, scroll lock ----
// Backdrop-click-to-close was deliberately removed platform-wide (accidental
// data loss on a stray click) — which makes Escape the ONLY keyboard way to
// dismiss a modal. Before this component, only one of ~15 modal-bearing
// screens actually wired it. Reuses the existing .modal/.modal-box/
// .modal-head/.modal-title/.modal-body/.modal-foot classes every hand-built
// modal already used, so retrofitting a screen onto this is a markup swap,
// not a restyle.
function useEscapeToClose(open, onClose) {
  React.useEffect(() => {
    if (!open) return;
    function onKey(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
}

function Modal({ open, onClose, title, titleSub, size, width, boxClassName, headerActions, banner, children, foot }) {
  const boxRef = React.useRef(null);
  useEscapeToClose(open, onClose);

  // Body scroll lock + initial focus, both scoped to this modal's own open
  // lifetime so nested/sequential modals don't fight over document state.
  React.useEffect(() => {
    if (!open) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const prevFocus = document.activeElement;
    boxRef.current?.focus();
    return () => {
      document.body.style.overflow = prevOverflow;
      if (prevFocus && prevFocus.focus) prevFocus.focus();
    };
  }, [open]);

  // Minimal focus trap: Tab past the last focusable element wraps to the
  // first, Shift+Tab past the first wraps to the last — keeps keyboard focus
  // inside the modal without needing a full focus-trap library.
  function onKeyDown(e) {
    if (e.key !== "Tab" || !boxRef.current) return;
    const focusable = boxRef.current.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  if (!open) return null;
  return (
    <div className="modal open" role="presentation">
      <div className={"modal-box" + (boxClassName ? " " + boxClassName : "")} style={size === "sm" ? { width: 480 } : (width ? { width } : undefined)}
        role="dialog" aria-modal="true" aria-label={title || undefined}
        tabIndex={-1} ref={boxRef} onKeyDown={onKeyDown}>
        {title && (
          <div className="modal-head">
            <div>
              <div className="modal-title">{title}</div>
              {titleSub && <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2 }}>{titleSub}</div>}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {headerActions}
              <button type="button" className="btn btn-sm" onClick={onClose} aria-label="Close">✕</button>
            </div>
          </div>
        )}
        {banner}
        <div className="modal-body">{children}</div>
        {foot && <div className="modal-foot">{foot}</div>}
      </div>
    </div>
  );
}

// ---- Clickable: a <div> that behaves like a real interactive control —
// role="button", tabIndex, and Enter/Space handling — for the row/card
// expanders that need arbitrary block-level content and can't just be a
// <button>. Mirrors the pattern continuous-monitoring.jsx's CMTile and
// continuous-monitoring-viz.jsx's event rows already used by hand, just
// centralized so every other "div that toggles something" gets it too.
function Clickable({ onClick, className = "", style, children, ...rest }) {
  return (
    <div
      role="button"
      tabIndex={0}
      className={className}
      style={style}
      onClick={onClick}
      onKeyDown={e => {
        // Only react to a key event that originated on this element itself —
        // otherwise Enter/Space activating a nested real <button> (e.g. an
        // inline Approve/Reject action) would also re-toggle the row via
        // keydown bubbling, which native click handlers already guard against
        // with stopPropagation but keydown never did until this component.
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick && onClick(e);
        }
      }}
      {...rest}
    >
      {children}
    </div>
  );
}

// ---- ConfirmModal: the app's one dialog for "are you sure" on an
// irreversible action, built on Modal so it inherits Escape-to-close, the
// focus trap, and scroll lock for free. `requireTypedConfirmation` (a string
// the user must type verbatim) gates Confirm for the highest-stakes subset —
// e.g. deleting a connector's stored credentials — where a stray click on
// the wrong button is a materially worse outcome than on a routine delete.
function ConfirmModal({
  open, onConfirm, onCancel, title, message,
  danger = false, confirmLabel = "Confirm", cancelLabel = "Cancel",
  requireTypedConfirmation = null,
}) {
  const [typed, setTyped] = React.useState("");
  React.useEffect(() => { if (open) setTyped(""); }, [open]);

  const locked = !!requireTypedConfirmation && typed !== requireTypedConfirmation;

  return (
    <Modal open={open} onClose={onCancel} title={title} size="sm" foot={
      <>
        <button type="button" className="btn btn-sm" onClick={onCancel}>{cancelLabel}</button>
        <button type="button" className={"btn btn-sm" + (danger ? " btn-danger" : "")}
          disabled={locked} onClick={onConfirm}>{confirmLabel}</button>
      </>
    }>
      <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.5 }}>{message}</div>
      {requireTypedConfirmation && (
        <div style={{ marginTop: 14 }}>
          <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)", letterSpacing: "0.05em", marginBottom: 6 }}>
            TYPE "{requireTypedConfirmation}" TO CONFIRM
          </div>
          <input className="code-input mono" value={typed} onChange={e => setTyped(e.target.value)}
            placeholder={requireTypedConfirmation} autoFocus />
        </div>
      )}
    </Modal>
  );
}

// ---- Toast: a small, dismissible, non-blocking surface for things a user
// should know but that shouldn't interrupt them — a queue item someone else
// already resolved, a background autosave that silently failed. Not a
// general notification system (that's the existing in-page Notification Log
// in rail.jsx) — this is transient and disappears on its own.
let _toastListeners = [];
let _toastIdSeq = 0;

function showToast(message, { tone = "neutral", duration = 5000 } = {}) {
  const toast = { id: ++_toastIdSeq, message, tone };
  _toastListeners.forEach(fn => fn(toast, duration));
  return toast.id;
}

function ToastHost() {
  const [toasts, setToasts] = React.useState([]);

  React.useEffect(() => {
    function onToast(toast, duration) {
      setToasts(prev => [...prev, toast]);
      setTimeout(() => setToasts(prev => prev.filter(t => t.id !== toast.id)), duration);
    }
    _toastListeners.push(onToast);
    return () => { _toastListeners = _toastListeners.filter(fn => fn !== onToast); };
  }, []);

  if (!toasts.length) return null;
  return (
    <div className="toast-host" aria-live="polite">
      {toasts.map(t => (
        <div key={t.id} className={`toast toast-${t.tone}`}
          onClick={() => setToasts(prev => prev.filter(x => x.id !== t.id))}>
          {t.message}
        </div>
      ))}
    </div>
  );
}

// ---- Shared "needs a human" queue taxonomy — Exception Management and
// JE Testing both score connector events into the exact same
// exception_control_events/exception_model_inferences/exception_auditor_triage
// tables (discriminated only by event_type='JOURNAL_ENTRY', see db.py's
// _EXCLUDE_JE_TESTING_SQL) and always resolve to one of these same 4 root
// causes — a real finding, noise, an already-approved exception, or bad
// data. One canonical definition here instead of two screens each keeping
// their own copy (JE Testing's used to be a plainer, undocumented duplicate).
const ATTENTION_RESOLUTION_LABELS = [
  { value: "TRUE_CONTROL_FAILURE", label: "True Control Failure", tone: "bad",
    what: "The control genuinely failed to do its job — a real finding, not noise. Requires notes." },
  { value: "BENIGN_OPERATIONAL_NOISE", label: "Benign Operational Noise", tone: "good",
    what: "Flagged, but on inspection this is normal business activity the scoring model was too sensitive to." },
  { value: "APPROVED_CARVE_OUT", label: "Approved Carve-Out", tone: "neutral",
    what: "Outside normal parameters, but already covered by a documented, approved exception. Requires notes." },
  { value: "DATA_PIPELINE_ERROR", label: "Data Pipeline Error", tone: "warn",
    what: "The event itself is bad data (a connector glitch, a malformed record) — not a real control signal either way." },
];
const ATTENTION_NOTES_REQUIRED = new Set(["TRUE_CONTROL_FAILURE", "APPROVED_CARVE_OUT"]);
const ATTENTION_LABEL_META = Object.fromEntries(ATTENTION_RESOLUTION_LABELS.map(l => [l.value, l]));
const ATTENTION_LABEL_COLOR = {
  TRUE_CONTROL_FAILURE: "var(--red-ink)", BENIGN_OPERATIONAL_NOISE: "var(--green-ink)",
  APPROVED_CARVE_OUT: "var(--ink-3)", DATA_PIPELINE_ERROR: "var(--amber-ink)",
};

// R/A/G — same vocabulary management_action_plans.risk_rating / risk_scores.rag_status
// use elsewhere in this app. Shared because both queues' grouped rows carry a
// worst_risk_rating field (JE rows are simply always "Unrated" today, since
// je_testing_sweep.py doesn't compute one yet — same honest fallback either way).
const ATTENTION_RISK_RATING_META = {
  R: { label: "R — Urgent", bg: "var(--red-soft)", ink: "var(--red-ink)" },
  A: { label: "A — Moderate", bg: "var(--amber-soft)", ink: "var(--amber-ink)" },
  G: { label: "G — Low", bg: "var(--green-soft)", ink: "var(--green-ink)" },
};

function RiskRatingPill({ rating }) {
  const meta = ATTENTION_RISK_RATING_META[rating];
  if (!meta) return <span style={{ fontSize: 9.5, color: "var(--ink-4)" }}>Unrated</span>;
  return (
    <span className="mono" style={{
      fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999,
      background: meta.bg, color: meta.ink, whiteSpace: "nowrap",
    }}>
      {meta.label}
    </span>
  );
}

// ---- AttentionGroupRow: the grouped/bulk-resolve queue row shared by
// Exception Management and JE Testing (the "unify the queue" UX-audit
// recommendation) — one row per (control_id, system_source) recurring
// pattern instead of one per event, with a "resolve all N as one decision"
// action. Deliberately NOT extended to Risk Approval/Audit Scope/SOX
// (a different Approve-vs-Adjust-with-manager-routing paradigm) or CEM
// Holds/Human Review (simple accept/reject verdicts, live-polling, no
// notes) — those three are structurally different queues, not just
// differently skinned copies of this one.
//
// Props:
//   group          — { control_id, system_source, occurrence_count, worst_risk_rating,
//                       owner, first_seen_at, last_seen_at, has_open_map, map_ref }
//   getMembers(group) -> Promise<row[]>   fetch this group's individual pending rows
//   renderMember(row, onMemberResolved) -> node   render one drilled-in row
//   onBulkResolve(group, label, notes) -> Promise   resolve every member at once
//   onResolved(eventId | null, group)   called after a single member or the whole
//                                        group resolves (null eventId = bulk)
//   onNavigate, resolveAllLabel (default "event")
function AttentionGroupRow({ group, getMembers, renderMember, onBulkResolve, onResolved, onNavigate, resolveAllLabel = "event" }) {
  const [expanded, setExpanded] = React.useState(false);
  const [members, setMembers] = React.useState(null);
  const [membersLoading, setMembersLoading] = React.useState(false);
  const [bulkLabel, setBulkLabel] = React.useState(null);
  const [bulkNotes, setBulkNotes] = React.useState("");
  const [bulkSubmitting, setBulkSubmitting] = React.useState(false);
  const [bulkError, setBulkError] = React.useState(null);
  const [bulkDone, setBulkDone] = React.useState(false);

  async function toggleExpand() {
    const next = !expanded;
    setExpanded(next);
    if (next && members === null) {
      setMembersLoading(true);
      try {
        setMembers(await getMembers(group));
      } finally {
        setMembersLoading(false);
      }
    }
  }

  function handleMemberResolved(eventId) {
    setMembers(ms => (ms || []).filter(r => r.event_id !== eventId));
    onResolved && onResolved(eventId, group);
  }

  const needsNotes = bulkLabel && ATTENTION_NOTES_REQUIRED.has(bulkLabel);
  const canBulkSubmit = bulkLabel && (!needsNotes || bulkNotes.trim().length > 0) && !bulkSubmitting;

  async function handleBulkSubmit() {
    setBulkSubmitting(true);
    setBulkError(null);
    try {
      await onBulkResolve(group, bulkLabel, bulkNotes);
      setBulkDone(true);
      onResolved && onResolved(null, group);
    } catch (e) {
      setBulkError(e.message || String(e));
    } finally {
      setBulkSubmitting(false);
    }
  }

  if (bulkDone) return null;

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "10px 12px", marginBottom: 8, background: "var(--surface)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, cursor: "pointer" }}
        onClick={toggleExpand}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 11.5, fontWeight: 600, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            {group.control_id}
            <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>· {group.system_source}</span>
            <span className="mono" style={{ fontSize: 9.5, fontWeight: 700, padding: "1px 7px", borderRadius: 999, background: "var(--surface-2)", color: "var(--ink-2)" }}>
              ×{group.occurrence_count}
            </span>
            {group.has_open_map && (
              <span className="mono" style={{ fontSize: 9.5, fontWeight: 700, padding: "1px 7px", borderRadius: 999, background: "var(--acc-soft)", color: "var(--acc-ink)" }}
                onClick={e => { e.stopPropagation(); onNavigate && onNavigate("continuousmonitoring"); }}
                title="Already tracked by a Management Action Plan">
                Tracked by {group.map_ref}
              </span>
            )}
          </div>
          <div style={{ fontSize: 9.5, color: "var(--ink-4)", marginTop: 2 }}>
            {group.owner ? `owner: ${group.owner} · ` : ""}
            first seen {group.first_seen_at ? new Date(group.first_seen_at).toLocaleDateString() : "—"}
            {" · "}last seen {group.last_seen_at ? new Date(group.last_seen_at).toLocaleString() : "—"}
          </div>
        </div>
        <RiskRatingPill rating={group.worst_risk_rating} />
      </div>

      {expanded && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--line)" }}>
          {group.occurrence_count > 1 && (
            <div style={{ marginBottom: 10, padding: "8px 10px", background: "var(--surface-2)", borderRadius: 5 }}>
              <div className="kicker" style={{ fontSize: 9.5, marginBottom: 6 }}>
                Resolve all {group.occurrence_count} as one decision
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                {ATTENTION_RESOLUTION_LABELS.map(l => (
                  <button key={l.value} type="button" onClick={() => setBulkLabel(l.value)} title={l.what}
                    style={{
                      fontSize: 10.5, padding: "5px 10px", borderRadius: 5, cursor: "pointer",
                      border: l.value === bulkLabel ? "1px solid var(--acc,#2563eb)" : "1px solid var(--line)",
                      background: l.value === bulkLabel ? "var(--acc,#2563eb)" : "transparent",
                      color: l.value === bulkLabel ? "#fff" : "var(--ink-2)",
                      fontWeight: l.value === bulkLabel ? 600 : 400,
                    }}>
                    {l.label}
                  </button>
                ))}
              </div>
              {needsNotes && (
                <textarea className="code-input" rows={2} placeholder="Justification notes (required for this resolution)…"
                  value={bulkNotes} onChange={e => setBulkNotes(e.target.value)}
                  style={{ width: "100%", fontSize: 11, marginBottom: 8, resize: "vertical" }} />
              )}
              {bulkError && <div className="mono" style={{ fontSize: 10.5, color: "var(--red-ink)", marginBottom: 8 }}>{bulkError}</div>}
              <button className="btn btn-acc btn-sm" disabled={!canBulkSubmit} onClick={handleBulkSubmit}>
                {bulkSubmitting ? "Resolving…" : `Resolve all ${group.occurrence_count} as ${bulkLabel ? ATTENTION_LABEL_META[bulkLabel].label : "…"}`}
              </button>
            </div>
          )}
          <div className="kicker" style={{ fontSize: 9.5, marginBottom: 6 }}>Individual {resolveAllLabel}s</div>
          {membersLoading ? <Empty>Loading…</Empty> : (members || []).map(row => renderMember(row, handleMemberResolved))}
        </div>
      )}
    </div>
  );
}

// ---- ProvenanceChip: one consistent "can I trust this number" affordance
// for every AI-touched verdict/score in the app (risk-approval.jsx,
// approval-inbox.jsx, cem.jsx, audit-scope-review.jsx today) — a UX-audit
// "worth building" recommendation. Renders only the fields actually passed:
// model name and a per-instance timestamp aren't threaded through to the
// frontend anywhere yet (they exist server-side on ai_analyses.model/
// created_at but aren't in the response payloads these screens read), so
// this is deliberately tolerant of missing fields rather than blocking on
// adding that plumbing everywhere first.
function ProvenanceChip({ verdict, confidence, model, timestamp, reviewedByName, reviewedAt }) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);

  React.useEffect(() => {
    if (!open) return;
    function onDocMouseDown(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [open]);

  const reviewed = !!(reviewedByName || reviewedAt);

  return (
    <span ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button type="button" className="mono"
        onClick={() => setOpen(o => !o)}
        title="AI provenance — click for details"
        style={{
          fontSize: 9.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999, cursor: "pointer",
          border: "1px solid transparent",
          background: reviewed ? "var(--green-soft)" : "var(--acc-soft)",
          color: reviewed ? "var(--green-ink)" : "var(--acc-ink)",
        }}>
        ✨ AI{confidence ? ` · ${confidence}` : ""}
      </button>
      {open && (
        <div style={{
          position: "absolute", top: "calc(100% + 4px)", left: 0, zIndex: 50,
          minWidth: 220, background: "var(--surface)", border: "1px solid var(--line)",
          borderRadius: 8, boxShadow: "0 6px 20px rgba(0,0,0,0.15)", padding: "10px 12px",
          fontSize: 11.5, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 5,
        }}>
          {verdict && <div><b style={{ color: "var(--ink)" }}>Verdict</b> — {verdict}</div>}
          {confidence && <div><b style={{ color: "var(--ink)" }}>Confidence</b> — {confidence}</div>}
          {model && <div><b style={{ color: "var(--ink)" }}>Model</b> — <span className="mono">{model}</span></div>}
          {timestamp && <div><b style={{ color: "var(--ink)" }}>Generated</b> — {new Date(timestamp).toLocaleString()}</div>}
          <div>
            <b style={{ color: "var(--ink)" }}>Reviewed</b> — {reviewed
              ? <>{reviewedByName || "yes"}{reviewedAt ? ` · ${new Date(reviewedAt).toLocaleString()}` : ""}</>
              : <span style={{ color: "var(--amber-ink)" }}>not yet reviewed</span>}
          </div>
        </div>
      )}
    </span>
  );
}

// ---- Screen access gate — enforces the per-user Read/Edit matrix configured
// in Configuration > User Configuration > Screen Access (user-config.jsx /
// auth.screen_permissions). Admins always bypass it. A screen with no saved
// row is allowed by default.
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

// ---- Freshness indicators ----
// Extracted from cem.jsx, which had this exact badge implemented twice
// (BBTermHeader actions + RawFeedTab's compact form) — this is the one
// definition both now use, plus the reused pattern for screens that
// auto-poll (5s intervals, cheap DB queries).
function LiveBadge({ lastRefresh, isPaused, onToggle, intervalLabel = "5s", compact = false }) {
  if (compact) {
    return isPaused
      ? <span style={{fontSize:9,fontWeight:700,color:"var(--amber-ink)"}}>⏸ PAUSED</span>
      : <span style={{fontSize:9,fontWeight:700,color:"var(--green-ink)",display:"flex",alignItems:"center",gap:3}}>
          <span style={{width:5,height:5,borderRadius:"50%",background:"var(--green-ink)",
            display:"inline-block",animation:"ubo-pulse 1.4s ease-in-out infinite"}}/>
          LIVE
        </span>;
  }
  return (
    <div style={{display:"flex",gap:8,alignItems:"center"}}>
      {isPaused ? (
        <span style={{fontSize:10,fontFamily:"'Geist Mono',monospace",padding:"2px 7px",borderRadius:4,background:"var(--amber-soft,#fff8e1)",color:"var(--amber-ink,#b45309)",fontWeight:700,letterSpacing:".04em"}}>
          ⏸ PAUSED
        </span>
      ) : (
        <span style={{fontSize:10,fontFamily:"'Geist Mono',monospace",padding:"2px 7px",borderRadius:4,background:"var(--green-soft,#e8f5e9)",color:"var(--green-ink,#166534)",fontWeight:700,letterSpacing:".04em",display:"flex",alignItems:"center",gap:4}}>
          <span style={{width:6,height:6,borderRadius:"50%",background:"var(--green-ink,#166534)",display:"inline-block",animation:"ubo-pulse 1.4s ease-in-out infinite"}}/>
          LIVE · {intervalLabel}
        </span>
      )}
      {lastRefresh && (
        <span style={{fontSize:10,color:"var(--ink-3)",fontFamily:"'Geist Mono',monospace"}}>
          {lastRefresh.toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false})}
        </span>
      )}
      {onToggle && (
        <button className="btn btn-sm" onClick={onToggle}>
          {isPaused ? "▶ RESUME" : "⏸ PAUSE"}
        </button>
      )}
    </div>
  );
}

// Manual-refresh variant for screens whose data comes from expensive external
// calls (EDGAR, FRED, PSI drift computation) rather than cheap DB queries —
// auto-polling those every few seconds would be wasteful/rate-limit-risky, so
// this shows "as of HH:MM:SS" plus an explicit Refresh button instead of a
// LIVE auto-poll badge.
function RefreshBadge({ lastRefresh, onRefresh, loading }) {
  return (
    <div style={{display:"flex",gap:8,alignItems:"center"}}>
      {lastRefresh && (
        <span style={{fontSize:10,color:"var(--ink-3)",fontFamily:"'Geist Mono',monospace"}}>
          As of {lastRefresh.toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false})}
        </span>
      )}
      <button className="btn btn-sm" onClick={onRefresh} disabled={loading}>
        {loading ? <span className="spin"/> : "↻"} Refresh
      </button>
    </div>
  );
}

// ---- usePolling: pausable interval + auto-pause on hidden tab + session-
// expiry backoff ----
// Every LIVE-badge screen (Approval Inbox, Continuous Watch, CEM,
// Infrastructure Monitoring) used to hand-roll its own polling effect, with
// the same two gaps in every copy: a backgrounded tab kept polling forever
// (nothing in this codebase ever checked document.hidden), and a dead
// session — fn() rejecting on a 401 — retried every tick forever instead of
// routing into the logout() flow auth.jsx's idle timer already uses. That
// combination is what produced the 401 console flood seen on Approval Inbox:
// a stale session, polled every 5s, with no backoff.
//
// Skips the fetch entirely while paused (manual or hidden-tab), fires it
// immediately on mount and on every transition back to unpaused (resume, or
// tab foregrounded) — matching the one hand-rolled effect (CEM's) that was
// already careful not to fetch while paused, since some screens treat a
// pause as "don't touch my state right now" (e.g. CEM's manual pagination).
//
// `fn` should reject with an Error whose `.status === 401` to signal an
// expired session (as opposed to any other transient failure) — three in a
// row stops polling and logs out, rather than retrying forever.
const _AUTH_EXPIRED_THRESHOLD = 3;

function usePolling(fn, intervalMs, { paused = false } = {}) {
  const auth = window.useAuth ? window.useAuth() : null;
  const fnRef = React.useRef(fn);
  fnRef.current = fn;
  const failCountRef = React.useRef(0);

  const [hidden, setHidden] = React.useState(document.hidden);
  React.useEffect(() => {
    function onVisibility() { setHidden(document.hidden); }
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  // A backgrounded tab is paused exactly like an explicit user pause.
  const effectivePaused = paused || hidden;

  const run = React.useCallback(async () => {
    try {
      await fnRef.current();
      failCountRef.current = 0;
    } catch (e) {
      if (e && e.status === 401) {
        failCountRef.current += 1;
        if (failCountRef.current >= _AUTH_EXPIRED_THRESHOLD) {
          failCountRef.current = 0;
          auth?.logout?.("Your session expired — sign back in to keep watching this screen.");
        }
      } else {
        failCountRef.current = 0;
      }
    }
  }, [auth]);

  React.useEffect(() => {
    // Skip the fetch entirely while paused (manual or hidden-tab) rather than
    // firing one anyway before bailing — a couple of screens rely on a pause
    // meaning "don't touch my state right now" (e.g. CEM's manual pagination
    // while paused), and a redundant fetch on the way into a pause serves no
    // one. Coming back from a pause (resume, or tab foregrounded) re-runs
    // this effect and fires the immediate refresh here.
    if (effectivePaused) return;
    run();
    const id = setInterval(run, intervalMs);
    return () => clearInterval(id);
  }, [run, intervalMs, effectivePaused]);
}

// Shown in the screen-switch Suspense boundary while a lazily-loaded
// screen chunk is being fetched on first navigation.
function ScreenLoadingFallback() {
  return (
    <div className="panel active" style={{display:"flex", alignItems:"center", justifyContent:"center", minHeight:300}}>
      <div style={{display:"flex", alignItems:"center", gap:10, color:"var(--ink-3)", fontSize:12}}>
        <span className="spin"/> Loading…
      </div>
    </div>
  );
}

// Expose globally
Object.assign(window, {
  Icon, DendraiMark, DendraiWordmark, Pill, RAGChip, VelocityPill, Sparkline, AuditorTakeaway,
  scoreColor, scoreColorInk, ragFromScore,
  likelihoodFromCE, ceMultiplier, projectQuarters,
  clamp, fmt2, fmt$M,
  Empty, SectionLabel, BBTermHeader, AiReviewBanner,
  LiveBadge, RefreshBadge, usePolling,
  ScreenAccessGate, ScreenLoadingFallback,
  Modal, useEscapeToClose, ConfirmModal, Clickable,
  showToast, ToastHost,
  ATTENTION_RESOLUTION_LABELS, ATTENTION_NOTES_REQUIRED, ATTENTION_LABEL_META, ATTENTION_LABEL_COLOR,
  RiskRatingPill, AttentionGroupRow,
  ProvenanceChip,
});
