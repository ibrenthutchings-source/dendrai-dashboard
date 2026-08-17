import { useEffect, useRef, useState } from "react";
import * as d3 from "d3";
import { DEFAULT_CONTROLS, fetchControlsFromApi, FRAMEWORK_COLOR, FRAMEWORK_SHORT_LABEL } from "./controls-reference.jsx";

// ─── Reference data — seeded from defaults, overwritten from DB on mount ─────
// Seed data + fetch logic now live in controls-reference.jsx (shared with
// risk-register-review.jsx and risk-sankey.jsx, which used to each hardcode
// their own independent copy of this same ~34-control list) — mapped here
// to this file's own fw/cat/domain field naming so nothing downstream in
// this file had to change.

let MASTER_CONTROLS = DEFAULT_CONTROLS.map(c => (
  { ref: c.ref, fw: c.framework, name: c.name, cat: c.category, domain: c.domain }
));

const AUTO_MAP_RULES = [
  { kws:["revenue","recognition","accounting","financial","margin","fraud","restat"],        refs:["FC-01","FC-02","FC-03","FC-04"] },
  { kws:["cyber","security","breach","data","unauthori","hack","phishing"],                 refs:["SC-01","SC-02","SC-03","SC-04","AC-02","AC-05"] },
  { kws:["access","identity","privilege","authentication","authoris","logical"],            refs:["AC-01","AC-02","AC-03","AC-04","AC-05"] },
  { kws:["operational","process","continuity","disaster","recovery","bcp"],                refs:["RM-01","OP-01"] },
  { kws:["compliance","regulatory","legal","penalty","gdpr","ccpa","sox"],                 refs:["CM-01","CM-02","CM-03"] },
  { kws:["vendor","supplier","third","supply","outsourc"],                                 refs:["VM-01","VM-02","OP-02"] },
  { kws:["talent","people","key","retention","staff","hiring"],                            refs:["HR-01","HR-02","OP-03"] },
  { kws:["macro","market","interest","credit","inflation","rate","currency"],              refs:["RM-02","RM-03","RM-04"] },
  { kws:["change","configuration","deployment","release","patch"],                         refs:["SC-05","CM-02"] },
  { kws:["incident","response","detection","monitoring","log"],                            refs:["SC-03","SC-04"] },
  { kws:["ai ","artificial intelligence","machine learning","llm","generative","algorithm","model bias","explainab","oversight of ai","training data"], refs:["AI-01","AI-02","AI-03","AI-04","AI-06"] },
  { kws:["third.party ai","ai vendor","ai tool","ai service","ai supply"],                refs:["AI-05","VM-01"] },
];

function autoMapControls(name, category) {
  const text = ((name || "") + " " + (category || "")).toLowerCase();
  const refs = [];
  for (const rule of AUTO_MAP_RULES) {
    if (rule.kws.some(kw => text.includes(kw))) {
      for (const r of rule.refs) { if (!refs.includes(r)) refs.push(r); }
    }
  }
  if (!refs.length) refs.push("RM-01");
  return refs.slice(0, 5);
}

// ─── Visual config ────────────────────────────────────────────────────────────

const INTERNAL_FWS = new Set(["Internal", "Internal Risk Register", ""]);

// "Enterprise" isn't one of DEFAULT_CONTROLS' real framework values — it's
// this file's own fallback bucket for a risk with no framework at all (see
// its one other use below) — so it's added on top of the shared canonical
// map (controls-reference.jsx) rather than folded into it.
const FW_COLOR = { ...FRAMEWORK_COLOR, "Enterprise": "#94a3b8" };
const FW_SHORT = { ...FRAMEWORK_SHORT_LABEL, "Enterprise": "Enterprise" };

const CAT_COLOR = {
  "Access Control":       "#60a5fa",
  "Security":             "#f87171",
  "Operations":           "#34d399",
  "Incident":             "#fbbf24",
  "Continuity":           "#818cf8",
  "Compliance":           "#fb923c",
  "Vendor":               "#e879f9",
  "Change Management":    "#2dd4bf",
  "Supplier":             "#e879f9",
  "Governance":           "#94a3b8",
  "Risk Assessment":      "#60a5fa",
  "System Integrity":     "#f87171",
  "Audit":                "#fbbf24",
  "Configuration":        "#34d399",
  "AI Impact Assessment": "#f472b6",
  "AI Lifecycle":         "#c084fc",
  "AI Data Governance":   "#67e8f9",
  "AI Transparency":      "#a3e635",
  "Third-Party AI":       "#fb7185",
  "Human Oversight":      "#fcd34d",
  "AppSec":               "#4ade80",
  "Monitoring":           "#38bdf8",
};

function fwColor(fw) { return FW_COLOR[fw] || "#6b7280"; }
function catColor(cat) { return CAT_COLOR[cat] || "#fbbf24"; }
function safeId(s) { return (s || "").replace(/[^a-zA-Z0-9]/g, "_"); }

// ─── Build graph ──────────────────────────────────────────────────────────────

// allowedFrameworks: when set (the Framework Matrix's own visible column
// set — matrixCfg.matrix plus any organically-detected extras, see
// risk-register-review.jsx), only controls tagged to one of those
// frameworks become nodes/edges here, so the Risk Graph never shows a
// control that isn't actually on the Framework Matrix tab. null/undefined =
// no filter (original always-show-everything behavior).
function buildGraph(risks, dbEdges, ctrlStates, allowedFrameworks) {
  const allRisks = (risks || [])
    .filter(r => r.id || r.risk_ref)
    .map(r => ({
      ...r,
      _fw: (r.source_framework && !INTERNAL_FWS.has(r.source_framework))
        ? r.source_framework : "Enterprise",
    }));

  const frameworks = [...new Set(allRisks.map(r => r._fw))];
  const nodes = [];
  const links = [];
  const seenNodes = new Set();
  const seenEdges = new Set();

  function addNode(n) {
    if (!seenNodes.has(n.id)) { seenNodes.add(n.id); nodes.push(n); }
  }
  function addLink(l) {
    if (!seenEdges.has(l.id)) { seenEdges.add(l.id); links.push(l); }
  }

  // Framework hub nodes
  frameworks.forEach(fw => {
    addNode({ id: `fw::${fw}`, type: "framework", label: FW_SHORT[fw] || fw, fw, fullLabel: fw });
  });

  // Risk nodes + membership edges
  allRisks.forEach(r => {
    const rid = r.id || r.risk_ref;
    addNode({
      id: rid, type: "risk",
      label: r.name || r.current_wording || rid,
      category: r.category || "",
      fw: r._fw,
      score: r.score ?? null,
      rag: r.rag || r.rag_status || null,
      domain: r.domain || r.assigned_domain || null,
    });
    addLink({ id: `m:${rid}`, source: `fw::${r._fw}`, target: rid, type: "membership", fw: r._fw });
  });

  // Cross-framework edges: same category, different framework
  const byCat = {};
  allRisks.forEach(r => {
    if (!r.category) return;
    (byCat[r.category] = byCat[r.category] || []).push(r);
  });
  Object.entries(byCat).forEach(([cat, rs]) => {
    for (let i = 0; i < rs.length; i++) {
      for (let j = i + 1; j < rs.length; j++) {
        if (rs[i]._fw === rs[j]._fw) continue;
        const s = rs[i].id || rs[i].risk_ref;
        const t = rs[j].id || rs[j].risk_ref;
        const key = [s, t].sort().join(":::");
        addLink({ id: `x:${key}`, source: s, target: t, type: "cross", category: cat });
      }
    }
  });

  // Control nodes — only controls on an allowed framework, when filtering
  const controlsInScope = allowedFrameworks
    ? MASTER_CONTROLS.filter(c => allowedFrameworks.includes(c.fw))
    : MASTER_CONTROLS;
  const refsInScope = new Set(controlsInScope.map(c => c.ref));
  controlsInScope.forEach(c => {
    addNode({ id: `ctrl::${c.ref}`, type: "control", label: c.name, ref: c.ref, category: c.cat, fw: c.fw, domain: c.domain });
  });

  // Control → Risk edges: prefer actual ctrlStates assignments, fallback to keyword auto-map
  allRisks.forEach(r => {
    const rid = r.id || r.risk_ref;
    const cs = ctrlStates?.[rid];
    const refs = cs
      ? [...new Set([...(cs.autoMapped || []), ...(cs.manual || [])])]
      : autoMapControls(r.name || r.current_wording || "", r.category);
    refs.filter(ref => refsInScope.has(ref)).forEach(ref => {
      addLink({ id: `c:${ref}:${rid}`, source: `ctrl::${ref}`, target: rid, type: "control" });
    });
  });

  // DB-persisted risk-to-risk relationship edges
  const riskIds = new Set(allRisks.map(r => r.id || r.risk_ref));
  (dbEdges || []).forEach(e => {
    if (!riskIds.has(e.from) || !riskIds.has(e.to)) return;
    const key = `rel:${e.type}:${[e.from, e.to].sort().join(":::")}`;
    addLink({ id: key, source: e.from, target: e.to, type: "relationship",
              relType: e.type, strength: e.strength ?? 0.5 });
  });

  return { nodes, links };
}

// ─── Tooltip ──────────────────────────────────────────────────────────────────

function Tooltip({ node, pos }) {
  if (!node || !pos) return null;
  const color = node.type === "control" ? "#22d3ee" : fwColor(node.fw);
  const ragColor = node.rag === "red" ? "#ef4444" : node.rag === "amber" ? "#f59e0b" : "#22c55e";
  return (
    <div style={{
      position: "fixed", left: pos.x + 16, top: pos.y - 8,
      background: "rgba(10,14,20,0.97)",
      border: `1px solid ${color}44`,
      borderLeft: `3px solid ${color}`,
      borderRadius: 7, padding: "9px 13px",
      maxWidth: 300, zIndex: 9999,
      pointerEvents: "none",
      boxShadow: `0 4px 32px ${color}22, 0 0 0 1px #ffffff08`,
      fontFamily: "system-ui, sans-serif",
    }}>
      <div style={{ fontSize: 9, color, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 5 }}>
        {node.type === "framework" ? "Framework Hub" : node.type === "control" ? `Control · ${node.ref || ""}` : "Risk"}
      </div>
      <div style={{ fontSize: 12, color: "#e2e8f0", fontWeight: 600, lineHeight: 1.45, marginBottom: node.category || node.score != null ? 6 : 0 }}>
        {node.type === "framework" ? node.fullLabel : node.label}
      </div>
      {node.category && (
        <div style={{ fontSize: 10, color: "#64748b" }}>
          Category <span style={{ color: catColor(node.category), fontWeight: 600 }}>{node.category}</span>
        </div>
      )}
      {node.type === "risk" && node.fw && node.fw !== "Enterprise" && (
        <div style={{ fontSize: 10, color: "#64748b", marginTop: 2 }}>
          Framework <span style={{ color: fwColor(node.fw), fontWeight: 600 }}>{node.fw}</span>
        </div>
      )}
      {node.score != null && (
        <div style={{ fontSize: 10, color: "#64748b", marginTop: 2 }}>
          Score <span style={{ color: ragColor, fontWeight: 700 }}>{node.score}</span>
          <span style={{ color: ragColor, marginLeft: 4, textTransform: "uppercase", fontSize: 9 }}>({node.rag})</span>
        </div>
      )}
      {node.type === "control" && node.domain && (
        <div style={{ fontSize: 10, color: "#64748b", marginTop: 2 }}>
          Domain <span style={{ color: "#94a3b8", fontWeight: 600 }}>{node.domain}</span>
        </div>
      )}
    </div>
  );
}

// ─── Legend ───────────────────────────────────────────────────────────────────

function Legend() {
  return (
    <div style={{
      position: "absolute", bottom: 12, left: 12, zIndex: 10,
      background: "rgba(10,14,20,0.85)", border: "1px solid #1e293b",
      borderRadius: 8, padding: "10px 13px",
      fontFamily: "system-ui, sans-serif",
      backdropFilter: "blur(6px)",
    }}>
      <div style={{ fontSize: 8, fontWeight: 700, color: "#475569", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 7 }}>Legend</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        {[
          { el: <div style={{ width: 16, height: 16, borderRadius: "50%", border: "2px solid #6366f1", background: "#6366f122", flexShrink: 0 }} />, label: "Framework" },
          { el: <div style={{ width: 11, height: 11, borderRadius: "50%", background: "#3b82f6", flexShrink: 0 }} />, label: "Risk" },
          { el: <div style={{ width: 9, height: 9, background: "#22d3ee", transform: "rotate(45deg)", borderRadius: 1, flexShrink: 0, opacity: 0.8 }} />, label: "Control" },
        ].map(({ el, label }) => (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: 7 }}>
            {el}
            <span style={{ fontSize: 9, color: "#94a3b8" }}>{label}</span>
          </div>
        ))}
        <div style={{ borderTop: "1px solid #1e293b", marginTop: 4, paddingTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
          {[
            { color: "#4f46e5", dash: null,   label: "Membership" },
            { color: "#fbbf24", dash: null,   label: "Cross-Framework" },
            { color: "#22d3ee", dash: "3,2",  label: "Control → Risk" },
            { color: "#f97316", dash: "5,3",  label: "Risk → Risk" },
          ].map(({ color, dash, label }) => (
            <div key={label} style={{ display: "flex", alignItems: "center", gap: 7 }}>
              <svg width="22" height="8" style={{ flexShrink: 0 }}>
                <line x1="0" y1="4" x2="22" y2="4" stroke={color} strokeWidth="1.5" strokeDasharray={dash || "none"} />
              </svg>
              <span style={{ fontSize: 9, color: "#64748b" }}>{label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Framework palette sidebar ────────────────────────────────────────────────

function FwPalette() {
  return (
    <div style={{
      position: "absolute", bottom: 12, right: 12, zIndex: 10,
      background: "rgba(10,14,20,0.85)", border: "1px solid #1e293b",
      borderRadius: 8, padding: "10px 13px",
      fontFamily: "system-ui, sans-serif",
      backdropFilter: "blur(6px)",
    }}>
      <div style={{ fontSize: 8, fontWeight: 700, color: "#475569", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 7 }}>Frameworks</div>
      {Object.entries(FW_COLOR).map(([fw, color]) => (
        <div key={fw} style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 4 }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: color, flexShrink: 0, boxShadow: `0 0 6px ${color}88` }} />
          <span style={{ fontSize: 9, color: "#94a3b8" }}>{FW_SHORT[fw] || fw}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function RiskGraphViz({ risks, ticker, runId, ctrlStates, allowedFrameworks }) {
  const svgRef       = useRef(null);
  const simRef       = useRef(null);
  const zoomRef      = useRef(null);
  const [tooltip,    setTooltip]    = useState(null);
  const [showMember, setShowMember] = useState(true);
  const [showCross,  setShowCross]  = useState(true);
  const [showCtrl,   setShowCtrl]   = useState(true);
  const [showRelations, setShowRelations] = useState(true);
  const [controlsRev, setControlsRev] = useState(0);
  const [relEdges,   setRelEdges]   = useState([]);

  // Load controls from DB on mount; rebuild graph once they arrive
  useEffect(() => {
    (async () => {
      const controls = await fetchControlsFromApi();
      if (!controls) return;
      MASTER_CONTROLS.length = 0;
      for (const c of controls) {
        MASTER_CONTROLS.push({ ref: c.ref, fw: c.framework || "", name: c.name, cat: c.category || "", domain: c.domain || "" });
      }
      setControlsRev(r => r + 1);
    })();
  }, []);

  // Load persisted risk-to-risk relationship edges from DB graph API
  useEffect(() => {
    if (!ticker) return;
    (async () => {
      try {
        const url = runId
          ? `/api/risk-register/graph/${ticker}/run/${runId}`
          : `/api/risk-register/graph/${ticker}`;
        const res = await fetch(url);
        if (!res.ok) return;
        const data = await res.json();
        const edges = (data.edges || []).map(e => ({
          from: e.from,
          to:   e.to,
          type: e.type,
          strength: e.strength ?? 0.5,
        }));
        setRelEdges(edges);
      } catch (_) {}
    })();
  }, [ticker, runId]);

  useEffect(() => {
    if (!svgRef.current) return;
    simRef.current?.stop();

    const svgEl = svgRef.current;
    const svg   = d3.select(svgEl);
    svg.selectAll("*").remove();

    const W = svgEl.clientWidth  || 900;
    const H = svgEl.clientHeight || 580;

    // ── Defs ─────────────────────────────────────────────────────────────────
    const defs = svg.append("defs");

    // Per-framework glow filters
    Object.entries(FW_COLOR).forEach(([fw, color]) => {
      const id = `glow-${safeId(fw)}`;
      const f  = defs.append("filter").attr("id", id).attr("x", "-60%").attr("y", "-60%").attr("width", "220%").attr("height", "220%");
      f.append("feGaussianBlur").attr("in", "SourceGraphic").attr("stdDeviation", "5").attr("result", "blur");
      const m = f.append("feMerge");
      m.append("feMergeNode").attr("in", "blur");
      m.append("feMergeNode").attr("in", "SourceGraphic");
    });

    // Soft glow for risk nodes
    const rGlow = defs.append("filter").attr("id", "risk-glow").attr("x", "-50%").attr("y", "-50%").attr("width", "200%").attr("height", "200%");
    rGlow.append("feGaussianBlur").attr("in", "SourceGraphic").attr("stdDeviation", "2").attr("result", "b");
    const rm = rGlow.append("feMerge");
    rm.append("feMergeNode").attr("in", "b");
    rm.append("feMergeNode").attr("in", "SourceGraphic");

    // Arrow marker for control edges
    defs.append("marker")
      .attr("id", "arr-ctrl").attr("markerWidth", 5).attr("markerHeight", 5)
      .attr("refX", 14).attr("refY", 2.5).attr("orient", "auto")
      .append("path").attr("d", "M0,0 L0,5 L5,2.5 Z")
      .attr("fill", "#22d3ee").attr("opacity", 0.6);

    // Arrow marker for risk→risk relationship edges
    defs.append("marker")
      .attr("id", "arr-rel").attr("markerWidth", 5).attr("markerHeight", 5)
      .attr("refX", 14).attr("refY", 2.5).attr("orient", "auto")
      .append("path").attr("d", "M0,0 L0,5 L5,2.5 Z")
      .attr("fill", "#f97316").attr("opacity", 0.7);

    // Dot-grid background pattern
    const pat = defs.append("pattern").attr("id", "dotgrid")
      .attr("patternUnits", "userSpaceOnUse").attr("width", 28).attr("height", 28);
    pat.append("circle").attr("cx", 14).attr("cy", 14).attr("r", 0.9).attr("fill", "#ffffff09");

    // ── Background ────────────────────────────────────────────────────────────
    svg.append("rect").attr("width", "100%").attr("height", "100%").attr("fill", "#090d14");
    svg.append("rect").attr("width", "100%").attr("height", "100%").attr("fill", "url(#dotgrid)");

    // ── Build & filter graph ──────────────────────────────────────────────────
    const { nodes, links } = buildGraph(risks, relEdges, ctrlStates, allowedFrameworks);

    const filteredLinks = links.filter(l =>
      (l.type !== "membership"   || showMember)    &&
      (l.type !== "cross"        || showCross)     &&
      (l.type !== "control"      || showCtrl)      &&
      (l.type !== "relationship" || showRelations)
    );

    const activeNodeIds = new Set();
    nodes.forEach(n => { if (n.type !== "control" || showCtrl) activeNodeIds.add(n.id); });
    filteredLinks.forEach(l => { activeNodeIds.add(typeof l.source === "object" ? l.source.id : l.source); activeNodeIds.add(typeof l.target === "object" ? l.target.id : l.target); });
    const activeNodes = nodes.filter(n => activeNodeIds.has(n.id));

    // ── Simulation ────────────────────────────────────────────────────────────
    const sim = d3.forceSimulation(activeNodes)
      .force("link", d3.forceLink(filteredLinks).id(d => d.id)
        .distance(d => d.type === "membership" ? 72 : d.type === "cross" ? 150 : 58)
        .strength(d => d.type === "membership" ? 0.55 : d.type === "cross" ? 0.04 : 0.22))
      .force("charge", d3.forceManyBody().strength(d =>
        d.type === "framework" ? -650 : d.type === "risk" ? -110 : -55))
      .force("center", d3.forceCenter(W / 2, H / 2).strength(0.06))
      .force("collide", d3.forceCollide()
        .radius(d => d.type === "framework" ? 46 : d.type === "risk" ? 18 : 13)
        .strength(0.8))
      .alphaDecay(0.018);

    simRef.current = sim;

    // ── Root group (zoomed) ───────────────────────────────────────────────────
    const g = svg.append("g");
    const zoomBehavior = d3.zoom().scaleExtent([0.15, 5]).on("zoom", e => g.attr("transform", e.transform));
    svg.call(zoomBehavior);
    zoomRef.current = zoomBehavior;

    // ── Edges ─────────────────────────────────────────────────────────────────
    const gEdges = g.append("g").attr("class", "edges");

    const memberLines = gEdges.append("g").selectAll("line")
      .data(filteredLinks.filter(l => l.type === "membership")).join("line")
      .attr("stroke",         d => fwColor(d.fw))
      .attr("stroke-width",   0.9)
      .attr("stroke-opacity", 0.22);

    const ctrlLines = gEdges.append("g").selectAll("line")
      .data(filteredLinks.filter(l => l.type === "control")).join("line")
      .attr("stroke",         "#22d3ee")
      .attr("stroke-width",   0.7)
      .attr("stroke-opacity", 0.18)
      .attr("stroke-dasharray", "3,3")
      .attr("marker-end",     "url(#arr-ctrl)");

    const crossPaths = gEdges.append("g").selectAll("path")
      .data(filteredLinks.filter(l => l.type === "cross")).join("path")
      .attr("fill",           "none")
      .attr("stroke",         d => catColor(d.category))
      .attr("stroke-width",   1.4)
      .attr("stroke-opacity", 0.45);

    const relLines = gEdges.append("g").selectAll("line")
      .data(filteredLinks.filter(l => l.type === "relationship")).join("line")
      .attr("stroke",         "#f97316")
      .attr("stroke-width",   d => 0.8 + (d.strength ?? 0.5) * 1.2)
      .attr("stroke-opacity", d => 0.25 + (d.strength ?? 0.5) * 0.35)
      .attr("stroke-dasharray", d => d.relType === "amplifies" ? null : "4,3")
      .attr("marker-end",     "url(#arr-rel)");

    // ── Nodes ─────────────────────────────────────────────────────────────────
    const gNodes = g.append("g").attr("class", "nodes");

    const nodeG = gNodes.selectAll("g")
      .data(activeNodes).join("g")
      .attr("cursor", "pointer");

    // Framework: dual-ring circles with glow
    const fwG = nodeG.filter(d => d.type === "framework");
    fwG.append("circle").attr("r", 34)
      .attr("fill", d => fwColor(d.fw) + "10")
      .attr("stroke", d => fwColor(d.fw) + "44")
      .attr("stroke-width", 1)
      .attr("filter", d => `url(#glow-${safeId(d.fw)})`);
    fwG.append("circle").attr("r", 24)
      .attr("fill", d => fwColor(d.fw) + "20")
      .attr("stroke", d => fwColor(d.fw))
      .attr("stroke-width", 1.5)
      .attr("filter", d => `url(#glow-${safeId(d.fw)})`);
    fwG.append("text")
      .text(d => d.label)
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("fill", "#e2e8f0").attr("font-size", 8).attr("font-weight", "700")
      .attr("letter-spacing", "0.04em").attr("pointer-events", "none");

    // Risk: filled circles with RAG dot overlay
    const riskG = nodeG.filter(d => d.type === "risk");
    riskG.append("circle").attr("r", 9)
      .attr("class", "risk-circle")
      .attr("fill",         d => fwColor(d.fw))
      .attr("fill-opacity", 0.82)
      .attr("stroke",       "#0d1117")
      .attr("stroke-width", 1.2)
      .attr("filter",       "url(#risk-glow)");

    riskG.filter(d => d.score != null)
      .append("circle").attr("r", 3.2).attr("cx", 6.5).attr("cy", -6.5)
      .attr("fill", d => d.rag === "red" ? "#ef4444" : d.rag === "amber" ? "#f59e0b" : "#22c55e")
      .attr("stroke", "#090d14").attr("stroke-width", 1);

    // Control: diamond (rotated rect)
    const ctrlG = nodeG.filter(d => d.type === "control");
    ctrlG.append("rect")
      .attr("width", 10).attr("height", 10)
      .attr("x", -5).attr("y", -5)
      .attr("rx", 1)
      .attr("transform", "rotate(45)")
      .attr("fill",         "#22d3ee")
      .attr("fill-opacity", 0.6)
      .attr("stroke",       "#0891b2")
      .attr("stroke-width", 0.8);

    // ── Neighbor map for highlight ────────────────────────────────────────────
    const nbMap = {};
    activeNodes.forEach(n => { nbMap[n.id] = new Set(); });

    function linkEndId(l, side) {
      const v = l[side];
      return typeof v === "object" ? v.id : v;
    }
    filteredLinks.forEach(l => {
      const s = linkEndId(l, "source"), t = linkEndId(l, "target");
      nbMap[s]?.add(t);
      nbMap[t]?.add(s);
    });

    let pinId = null;

    function highlight(id) {
      const nb = nbMap[id] || new Set();
      nodeG.style("opacity", d => d.id === id || nb.has(d.id) ? 1 : 0.07);
      memberLines.style("opacity", d => linkEndId(d,"source")===id||linkEndId(d,"target")===id ? 0.9 : 0.02);
      ctrlLines.style("opacity",   d => linkEndId(d,"source")===id||linkEndId(d,"target")===id ? 0.85: 0.02);
      crossPaths.style("opacity",  d => linkEndId(d,"source")===id||linkEndId(d,"target")===id ? 1   : 0.02);
      relLines.style("opacity",    d => linkEndId(d,"source")===id||linkEndId(d,"target")===id ? 1   : 0.02);
    }

    function resetHighlight() {
      nodeG.style("opacity", 1);
      memberLines.style("opacity", null);
      ctrlLines.style("opacity",   null);
      crossPaths.style("opacity",  null);
      relLines.style("opacity",    null);
    }

    // ── Interaction ───────────────────────────────────────────────────────────
    nodeG
      .on("mouseover", (evt, d) => {
        if (!pinId) highlight(d.id);
        if (d.type === "risk") {
          d3.select(evt.currentTarget).select(".risk-circle")
            .transition().duration(120).attr("r", 13);
        }
        setTooltip({ pos: { x: evt.clientX, y: evt.clientY }, node: d });
      })
      .on("mousemove", evt => {
        setTooltip(p => p ? { ...p, pos: { x: evt.clientX, y: evt.clientY } } : null);
      })
      .on("mouseout", (evt, d) => {
        if (!pinId) resetHighlight();
        if (d.type === "risk") {
          d3.select(evt.currentTarget).select(".risk-circle")
            .transition().duration(120).attr("r", 9);
        }
        setTooltip(null);
      })
      .on("click", (evt, d) => {
        evt.stopPropagation();
        if (pinId === d.id) { pinId = null; resetHighlight(); }
        else                 { pinId  = d.id; highlight(d.id); }
      });

    svg.on("click", () => { if (pinId) { pinId = null; resetHighlight(); } });

    // Drag
    nodeG.call(
      d3.drag()
        .on("start", (evt, d) => { if (!evt.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag",  (evt, d) => { d.fx = evt.x; d.fy = evt.y; })
        .on("end",   (evt, d) => { if (!evt.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
    );

    // ── Tick ─────────────────────────────────────────────────────────────────
    sim.on("tick", () => {
      memberLines
        .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x).attr("y2", d => d.target.y);

      ctrlLines
        .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => {
          const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
          const len = Math.sqrt(dx*dx + dy*dy) || 1;
          const r = d.target.type === "framework" ? 24 : 11;
          return d.target.x - (dx/len)*(r+2);
        })
        .attr("y2", d => {
          const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
          const len = Math.sqrt(dx*dx + dy*dy) || 1;
          const r = d.target.type === "framework" ? 24 : 11;
          return d.target.y - (dy/len)*(r+2);
        });

      crossPaths.attr("d", d => {
        const x1 = d.source.x, y1 = d.source.y, x2 = d.target.x, y2 = d.target.y;
        const mx = (x1+x2)/2, my = (y1+y2)/2;
        const dx = x2-x1, dy = y2-y1;
        const len = Math.sqrt(dx*dx+dy*dy) || 1;
        const ox = (-dy/len)*35, oy = (dx/len)*35;
        return `M${x1},${y1} Q${mx+ox},${my+oy} ${x2},${y2}`;
      });

      relLines
        .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => {
          const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
          const len = Math.sqrt(dx*dx + dy*dy) || 1;
          return d.target.x - (dx/len)*11;
        })
        .attr("y2", d => {
          const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
          const len = Math.sqrt(dx*dx + dy*dy) || 1;
          return d.target.y - (dy/len)*11;
        });

      nodeG.attr("transform", d => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });

    return () => sim.stop();
  }, [risks, showMember, showCross, showCtrl, showRelations, controlsRev, relEdges, ctrlStates, allowedFrameworks]);

  // ── Toggle button style ───────────────────────────────────────────────────
  const togStyle = (on, color) => ({
    fontSize: 10, padding: "3px 11px", borderRadius: 20, cursor: "pointer",
    border:      `1px solid ${on ? color : "#1e293b"}`,
    background:  on ? color + "22" : "transparent",
    color:       on ? color : "#475569",
    fontWeight:  on ? 600 : 400,
    fontFamily:  "system-ui, sans-serif",
    transition:  "all 0.15s",
  });

  return (
    <div style={{ position: "relative", width: "100%", height: 580, borderRadius: 8, overflow: "hidden", border: "1px solid #1e293b" }}>
      {/* Filter toolbar */}
      <div style={{ position: "absolute", top: 10, left: 10, zIndex: 10, display: "flex", gap: 6, alignItems: "center" }}>
        <span style={{ fontSize: 9, color: "#334155", fontWeight: 700, letterSpacing: "0.07em", fontFamily: "system-ui, sans-serif" }}>SHOW</span>
        <button style={togStyle(showMember,    "#6366f1")} onClick={() => setShowMember(v => !v)}>Membership</button>
        <button style={togStyle(showCross,     "#fbbf24")} onClick={() => setShowCross(v => !v)}>Cross-Framework</button>
        <button style={togStyle(showCtrl,      "#22d3ee")} onClick={() => setShowCtrl(v => !v)}>Controls</button>
        {relEdges.length > 0 && (
          <button style={togStyle(showRelations, "#f97316")} onClick={() => setShowRelations(v => !v)}>Relations</button>
        )}
      </div>

      {/* Zoom controls */}
      <div style={{ position: "absolute", top: 10, right: 12, zIndex: 10, display: "flex", gap: 4 }}>
        {[
          { label: "+", factor: 1.4, title: "Zoom in" },
          { label: "−", factor: 1/1.4, title: "Zoom out" },
          { label: "⊙", factor: null, title: "Reset zoom" },
        ].map(({ label, factor, title }) => (
          <button
            key={label}
            title={title}
            onClick={() => {
              const svg = d3.select(svgRef.current);
              if (factor === null) {
                svg.transition().duration(400).call(zoomRef.current.transform, d3.zoomIdentity);
              } else {
                svg.transition().duration(250).call(zoomRef.current.scaleBy, factor);
              }
            }}
            style={{
              width: 24, height: 24, borderRadius: 5, cursor: "pointer",
              background: "#0f172a", border: "1px solid #1e293b",
              color: "#94a3b8", fontSize: label === "⊙" ? 13 : 15,
              lineHeight: 1, display: "flex", alignItems: "center", justifyContent: "center",
              fontFamily: "system-ui, sans-serif",
            }}
          >{label}</button>
        ))}
      </div>

      {/* Hint */}
      <div style={{ position: "absolute", top: 42, right: 12, zIndex: 10, fontSize: 9, color: "#1e293b", fontFamily: "system-ui, sans-serif" }}>
        Hover · Click to pin · Drag · Scroll to zoom
      </div>

      <Legend />
      <FwPalette />

      <svg ref={svgRef} style={{ width: "100%", height: "100%", display: "block" }} />

      {tooltip && <Tooltip node={tooltip.node} pos={tooltip.pos} />}
    </div>
  );
}
