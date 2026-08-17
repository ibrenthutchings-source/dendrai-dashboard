import { useEffect, useRef, useState } from "react";
import { sankey, sankeyLinkHorizontal } from "d3-sankey";
import * as d3 from "d3";
import { DEFAULT_CONTROLS, fetchControlsFromApi, FRAMEWORK_COLOR, FRAMEWORK_COLOR_FALLBACK } from "./controls-reference.jsx";

// ─── Colors ────────────────────────────────────────────────────────────────────

const DOMAIN_COLOR = {
  "Finance":     "#22c55e",
  "IT":          "#3b82f6",
  "Operational": "#f59e0b",
  "HR":          "#ec4899",
  "Legal":       "#a855f7",
  "Technology":  "#22d3ee",
};

// Was this file's own local copy of controls-reference.jsx's canonical
// FRAMEWORK_COLOR — identical values, kept in sync by hand until now.
const FW_COLOR = FRAMEWORK_COLOR;

const BASE_FW_ORDER  = ["Internal", "ISO/IEC 42001", "SOC 2", "ISO/IEC 27001", "NIST SP 800-53", "CIS Controls", "COSO ERM"];
const BASE_DOM_ORDER = ["Finance", "IT", "Operational", "HR", "Legal", "Technology"];
const FALLBACK_COLOR = FRAMEWORK_COLOR_FALLBACK;

// ─── Controls (sorted by framework, then domain within framework — minimizes
// link crossings). Seed data now lives in controls-reference.jsx (shared
// with risk-register-review.jsx and risk-graph-viz.jsx, which used to each
// hardcode their own independent copy of this same ~34-control list) —
// mapped here to this file's own fw/cat/dom field naming, then sorted by
// (BASE_FW_ORDER, BASE_DOM_ORDER) to reproduce the original hand-ordered
// array's crossing-minimizing grouping. Mutable so the component can append
// DB-loaded custom controls after mount, same as before. ──────────────────

let CONTROLS = DEFAULT_CONTROLS
  .map(c => ({ ref: c.ref, fw: c.framework, name: c.name, cat: c.category, dom: c.domain }))
  .sort((a, b) => {
    const fwDiff = BASE_FW_ORDER.indexOf(a.fw) - BASE_FW_ORDER.indexOf(b.fw);
    return fwDiff !== 0 ? fwDiff : BASE_DOM_ORDER.indexOf(a.dom) - BASE_DOM_ORDER.indexOf(b.dom);
  });

// allowedFrameworks: when set (the Framework Matrix's own visible column
// set — matrixCfg.matrix plus any organically-detected extras, see
// risk-register-review.jsx), only controls tagged to one of those
// frameworks are included, so the Sankey never shows a control/framework
// that isn't actually on the Framework Matrix tab. null/undefined = no
// filter (matches this function's original always-show-everything behavior).
function buildGraphData(allowedFrameworks) {
  const filteredControls = allowedFrameworks
    ? CONTROLS.filter(c => allowedFrameworks.includes(c.fw))
    : CONTROLS;

  // Derive fw/dom order dynamically so custom controls' frameworks appear
  const fwOrder  = allowedFrameworks ? BASE_FW_ORDER.filter(f => allowedFrameworks.includes(f)) : [...BASE_FW_ORDER];
  const domOrder = BASE_DOM_ORDER.filter(d => filteredControls.some(c => c.dom === d));
  filteredControls.forEach(c => {
    if (!fwOrder.includes(c.fw))   fwOrder.push(c.fw);
    if (!domOrder.includes(c.dom)) domOrder.push(c.dom);
  });

  const nodes = [
    ...filteredControls.map(c => ({
      id: `ctrl:${c.ref}`, label: c.ref, fullName: c.name,
      type: "control", color: DOMAIN_COLOR[c.dom] || FALLBACK_COLOR,
      fw: c.fw, dom: c.dom, cat: c.cat,
    })),
    ...fwOrder.map(fw => ({
      id: `fw:${fw}`, label: fw, type: "framework", color: FW_COLOR[fw] || FALLBACK_COLOR,
    })),
    ...domOrder.map(dom => ({
      id: `dom:${dom}`, label: dom, type: "domain", color: DOMAIN_COLOR[dom] || FALLBACK_COLOR,
    })),
  ];

  const ctrlFwLinks = filteredControls.map(c => ({
    source: `ctrl:${c.ref}`, target: `fw:${c.fw}`, value: 1,
  }));

  const fwDomAgg = {};
  filteredControls.forEach(c => {
    const key = `fw:${c.fw}|||dom:${c.dom}`;
    fwDomAgg[key] = (fwDomAgg[key] || 0) + 1;
  });
  const fwDomLinks = Object.entries(fwDomAgg).map(([key, value]) => {
    const [source, target] = key.split("|||");
    return { source, target, value };
  });

  return { nodes, links: [...ctrlFwLinks, ...fwDomLinks] };
}

// ─── Tooltip ───────────────────────────────────────────────────────────────────

function Tooltip({ data, pos }) {
  if (!data || !pos) return null;
  const isLink = data._isLink;
  const accent = isLink ? "#ffffff44" : (data.color || "#6b7280");

  return (
    <div style={{
      position: "fixed", left: pos.x + 16, top: pos.y - 10,
      background: "rgba(8,12,20,0.97)",
      border:     `1px solid ${accent}`,
      borderLeft: `3px solid ${isLink ? "#ffffff66" : accent}`,
      borderRadius: 7, padding: "9px 13px", maxWidth: 260,
      zIndex: 9999, pointerEvents: "none",
      boxShadow: "0 6px 32px rgba(0,0,0,0.6)",
      fontFamily: "system-ui, sans-serif",
    }}>
      {isLink ? (
        data._type === "ctrl-fw" ? (
          <>
            <div style={{ fontSize: 9, color: "#475569", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 5 }}>
              Control → Framework
            </div>
            <div style={{ fontFamily: "monospace", fontSize: 10, color: data.sourceColor, marginBottom: 2 }}>{data.sourceRef}</div>
            <div style={{ fontSize: 11, color: "#e2e8f0", fontWeight: 600, lineHeight: 1.4, marginBottom: 5 }}>{data.sourceName}</div>
            <div style={{ fontSize: 11, color: "#94a3b8" }}>
              maps to <span style={{ color: data.targetColor, fontWeight: 600 }}>{data.targetName}</span>
            </div>
          </>
        ) : (
          <>
            <div style={{ fontSize: 9, color: "#475569", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 5 }}>
              Framework → Domain
            </div>
            <div style={{ fontSize: 11, color: "#e2e8f0", fontWeight: 600, lineHeight: 1.5 }}>
              <span style={{ color: data.sourceColor }}>{data.sourceName}</span>
              <span style={{ color: "#334155", margin: "0 6px" }}>→</span>
              <span style={{ color: data.targetColor }}>{data.targetName}</span>
            </div>
            <div style={{ marginTop: 5, fontSize: 10, color: "#94a3b8" }}>
              <span style={{ color: "#e2e8f0", fontWeight: 700 }}>{data.value}</span> control{data.value !== 1 ? "s" : ""}
            </div>
          </>
        )
      ) : data.type === "control" ? (
        <>
          <div style={{ fontSize: 9, color: data.color, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4 }}>
            Control · {data.cat}
          </div>
          <div style={{ fontFamily: "monospace", fontSize: 11, color: "#94a3b8", marginBottom: 3 }}>{data.label}</div>
          <div style={{ fontSize: 11, color: "#e2e8f0", fontWeight: 600, lineHeight: 1.4, marginBottom: 6 }}>{data.fullName}</div>
          <div style={{ fontSize: 10, color: "#64748b" }}>
            Framework: <span style={{ color: FW_COLOR[data.fw] || "#94a3b8", fontWeight: 600 }}>{data.fw}</span>
          </div>
          <div style={{ fontSize: 10, color: "#64748b" }}>
            Domain: <span style={{ color: data.color, fontWeight: 600 }}>{data.dom}</span>
          </div>
        </>
      ) : (
        <>
          <div style={{ fontSize: 9, color: data.color, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 5 }}>
            {data.type === "framework" ? "Framework" : "Domain"}
          </div>
          <div style={{ fontSize: 12, color: "#e2e8f0", fontWeight: 600, marginBottom: 5 }}>
            {data.label}
          </div>
          <div style={{ fontSize: 10, color: "#94a3b8" }}>
            <span style={{ color: "#e2e8f0", fontWeight: 700 }}>{data.value}</span> control{data.value !== 1 ? "s" : ""}
          </div>
        </>
      )}
    </div>
  );
}

// ─── Column header component ───────────────────────────────────────────────────

function ColHeader({ label, x, y }) {
  return (
    <g>
      <text
        x={x} y={y - 10} textAnchor="middle"
        fill="#334155" fontSize={9} fontWeight={700}
        letterSpacing="0.12em" fontFamily="system-ui, sans-serif"
      >
        {label}
      </text>
      <line x1={x - 40} x2={x + 40} y1={y - 3} y2={y - 3} stroke="#1e293b" strokeWidth={1} />
    </g>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────

export function RiskSankey({ allowedFrameworks } = {}) {
  const svgRef  = useRef(null);
  const [tooltip, setTooltip]  = useState(null);
  const [colHdrs, setColHdrs]  = useState([]);   // column header positions resolved after layout
  const [controlsRev, setControlsRev] = useState(0);

  // Append any DB-persisted controls not already in the hardcoded list
  useEffect(() => {
    (async () => {
      const controls = await fetchControlsFromApi();
      if (!controls) return;
      const existing = new Set(CONTROLS.map(c => c.ref));
      let added = 0;
      for (const c of controls) {
        if (existing.has(c.ref)) continue;
        CONTROLS.push({
          ref: c.ref,
          fw:  c.framework || "Custom",
          name: c.name || c.ref,
          cat: c.category || "Custom",
          dom: c.domain   || "Custom",
        });
        added++;
      }
      if (added > 0) setControlsRev(r => r + 1);
    })();
  }, []);

  useEffect(() => {
    if (!svgRef.current) return;

    const svgEl = svgRef.current;
    const svg   = d3.select(svgEl);
    svg.selectAll("*").remove();

    const W   = svgEl.clientWidth  || 960;
    const H   = svgEl.clientHeight || 860;
    const PAD = { top: 48, bottom: 20, left: 84, right: 142 };

    // ── Background ────────────────────────────────────────────────────────────
    const defs = svg.append("defs");

    const dotPat = defs.append("pattern").attr("id", "sk-dots")
      .attr("patternUnits", "userSpaceOnUse").attr("width", 24).attr("height", 24);
    dotPat.append("circle").attr("cx", 12).attr("cy", 12).attr("r", 0.8).attr("fill", "#ffffff08");

    svg.append("rect").attr("width", "100%").attr("height", "100%").attr("fill", "#080c14");
    svg.append("rect").attr("width", "100%").attr("height", "100%").attr("fill", "url(#sk-dots)");

    // ── Sankey layout ─────────────────────────────────────────────────────────
    const layout = sankey()
      .nodeId(d => d.id)
      .nodeWidth(14)
      .nodePadding(4)
      .nodeSort(null)
      .extent([[PAD.left, PAD.top], [W - PAD.right, H - PAD.bottom]]);

    const _fresh = buildGraphData(allowedFrameworks);
    const graphData = {
      nodes: _fresh.nodes.map(d => ({ ...d })),
      links: _fresh.links.map(d => ({ ...d })),
    };
    const { nodes, links } = layout(graphData);

    // Resolve column headers from node positions
    const depthX = {};
    nodes.forEach(n => { if (!(n.depth in depthX)) depthX[n.depth] = (n.x0 + n.x1) / 2; });
    setColHdrs([
      { label: "CONTROLS",   x: depthX[0] ?? PAD.left,       y: PAD.top },
      { label: "FRAMEWORKS", x: depthX[1] ?? W / 2,          y: PAD.top },
      { label: "DOMAINS",    x: depthX[2] ?? W - PAD.right,  y: PAD.top },
    ]);

    // ── Link gradients ────────────────────────────────────────────────────────
    links.forEach((l, i) => {
      const srcColor = l.source.color || "#6b7280";
      const tgtColor = l.target.color || "#6b7280";
      const g = defs.append("linearGradient")
        .attr("id", `sk-g-${i}`)
        .attr("gradientUnits", "userSpaceOnUse")
        .attr("x1", l.source.x1)
        .attr("y1", (l.source.y0 + l.source.y1) / 2)
        .attr("x2", l.target.x0)
        .attr("y2", (l.target.y0 + l.target.y1) / 2);
      g.append("stop").attr("offset", "0%")  .attr("stop-color", srcColor).attr("stop-opacity", 0.55);
      g.append("stop").attr("offset", "100%").attr("stop-color", tgtColor).attr("stop-opacity", 0.55);
    });

    // ── Links ─────────────────────────────────────────────────────────────────
    const linkGen = sankeyLinkHorizontal();

    const linkG     = svg.append("g").attr("class", "sk-links");
    const linkPaths = linkG.selectAll("path")
      .data(links).join("path")
      .attr("d",              linkGen)
      .attr("fill",           "none")
      .attr("stroke",         (_, i) => `url(#sk-g-${i})`)
      .attr("stroke-width",   d => Math.max(1, d.width))
      .attr("stroke-opacity", 0.38)
      .attr("cursor",         "pointer");

    // Animated march overlay (injected as CSS from JS, so no separate stylesheet needed)
    svg.append("style").text(`
      @keyframes sk-march { to { stroke-dashoffset: -20; } }
      .sk-march { animation: sk-march 1.2s linear infinite; }
    `);

    const marchG     = svg.append("g").attr("class", "sk-march-layer").attr("pointer-events", "none");
    const marchPaths = marchG.selectAll("path")
      .data(links).join("path")
      .attr("d",              linkGen)
      .attr("fill",           "none")
      .attr("stroke",         "#ffffff")
      .attr("stroke-width",   1)
      .attr("stroke-opacity", 0)
      .attr("stroke-dasharray", "6 14")
      .attr("class",          "sk-march");

    // ── Nodes ─────────────────────────────────────────────────────────────────
    const nodeG = svg.append("g").attr("class", "sk-nodes");

    const nodeGrp = nodeG.selectAll("g")
      .data(nodes).join("g")
      .attr("cursor", "pointer");

    // Glow backdrop
    nodeGrp.append("rect")
      .attr("class", "nk-glow")
      .attr("x",      d => d.x0 - 4)
      .attr("y",      d => d.y0 - 4)
      .attr("width",  d => (d.x1 - d.x0) + 8)
      .attr("height", d => Math.max(1, d.y1 - d.y0) + 8)
      .attr("rx",     4)
      .attr("fill",   d => d.color || "#6b7280")
      .attr("fill-opacity", 0);

    // Main node rect
    nodeGrp.append("rect")
      .attr("x",      d => d.x0)
      .attr("y",      d => d.y0)
      .attr("width",  d => d.x1 - d.x0)
      .attr("height", d => Math.max(2, d.y1 - d.y0))
      .attr("rx",     2)
      .attr("fill",         d => d.color || "#6b7280")
      .attr("fill-opacity", 0.85)
      .attr("stroke",       d => d.color || "#6b7280")
      .attr("stroke-width", 0.4)
      .attr("stroke-opacity", 0.4);

    // Count badge — frameworks & domains only, when tall enough
    nodeGrp.filter(d => d.type !== "control" && (d.y1 - d.y0) >= 14)
      .append("text")
      .attr("x", d => (d.x0 + d.x1) / 2)
      .attr("y", d => (d.y0 + d.y1) / 2)
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("fill", "#ffffff").attr("fill-opacity", 0.9)
      .attr("font-size", 8).attr("font-weight", 700)
      .attr("font-family", "system-ui, sans-serif")
      .attr("pointer-events", "none")
      .text(d => d.value);

    // Labels — controls on the left, frameworks & domains on the right
    nodeGrp.append("text")
      .attr("x",           d => d.depth === 0 ? d.x0 - 7 : d.x1 + 7)
      .attr("y",            d => (d.y0 + d.y1) / 2)
      .attr("dy",           "0.35em")
      .attr("text-anchor",  d => d.depth === 0 ? "end" : "start")
      .attr("fill",         d => d.color || "#94a3b8")
      .attr("fill-opacity", 0.88)
      .attr("font-size",    d => d.type === "control" ? 8 : 10)
      .attr("font-weight",  d => d.type === "control" ? 500 : 600)
      .attr("font-family",  d => d.type === "control" ? "monospace" : "system-ui, sans-serif")
      .attr("pointer-events", "none")
      .text(d => d.label);

    // ── Highlight helpers ─────────────────────────────────────────────────────
    function linkId(l, end) {
      const v = l[end];
      return typeof v === "object" ? v.id : v;
    }
    function linkConnects(l, nodeId) {
      return linkId(l, "source") === nodeId || linkId(l, "target") === nodeId;
    }

    function setGlow(ids) {
      const op = ids.length > 1 ? 0.16 : 0.2;
      nodeGrp.selectAll(".nk-glow").attr("fill-opacity", n => ids.includes(n.id) ? op : 0);
    }

    // Hovering a framework/domain node: highlight every link directly touching it
    function applyNodeHighlight(nodeId) {
      linkPaths
        .attr("stroke-opacity", l => linkConnects(l, nodeId) ? 0.82 : 0.04);
      marchPaths
        .attr("stroke-opacity", l => linkConnects(l, nodeId) ? 0.35 : 0);
      nodeGrp
        .style("opacity", d => d.id === nodeId
          || links.some(l => linkConnects(l, nodeId) && linkConnects(l, d.id)) ? 1 : 0.15);
      setGlow([nodeId]);
    }

    // Hovering a control node: highlight its full path — control → framework → domain
    function applyPathHighlight(d) {
      const fwId  = `fw:${d.fw}`;
      const domId = `dom:${d.dom}`;
      function inPath(l) {
        const s = linkId(l, "source"), t = linkId(l, "target");
        return (s === d.id && t === fwId) || (s === fwId && t === domId);
      }
      linkPaths.attr("stroke-opacity", l => inPath(l) ? 0.88 : 0.04);
      marchPaths.attr("stroke-opacity", l => inPath(l) ? 0.4 : 0);
      nodeGrp.style("opacity", n => (n.id === d.id || n.id === fwId || n.id === domId) ? 1 : 0.15);
      setGlow([d.id, fwId, domId]);
    }

    function applyHighlight(d) {
      if (d.type === "control") applyPathHighlight(d);
      else applyNodeHighlight(d.id);
    }

    function clearHighlight() {
      linkPaths.attr("stroke-opacity", 0.38);
      marchPaths.attr("stroke-opacity", 0);
      nodeGrp.style("opacity", 1);
      setGlow([]);
    }

    let pinId = null;

    // Node interaction
    nodeGrp
      .on("mouseover", (evt, d) => {
        if (!pinId) applyHighlight(d);
        setTooltip({ pos: { x: evt.clientX, y: evt.clientY }, data: d });
      })
      .on("mousemove", evt => {
        setTooltip(p => p ? { ...p, pos: { x: evt.clientX, y: evt.clientY } } : null);
      })
      .on("mouseout", () => {
        if (!pinId) {
          clearHighlight();
        }
        setTooltip(null);
      })
      .on("click", (evt, d) => {
        evt.stopPropagation();
        if (pinId === d.id) {
          pinId = null;
          clearHighlight();
        } else {
          pinId = d.id;
          applyHighlight(d);
        }
      });

    // Link interaction
    linkPaths
      .on("mouseover", (evt, d) => {
        if (!pinId) {
          linkPaths.attr("stroke-opacity", l => l === d ? 0.85 : 0.04);
          marchPaths.attr("stroke-opacity", l => l === d ? 0.4 : 0);
          nodeGrp.style("opacity", n =>
            n.id === d.source.id || n.id === d.target.id ? 1 : 0.12);
        }
        const isCtrlFw = d.source.type === "control";
        setTooltip({
          pos: { x: evt.clientX, y: evt.clientY },
          data: {
            _isLink:     true,
            _type:       isCtrlFw ? "ctrl-fw" : "fw-dom",
            sourceRef:   d.source.label,
            sourceName:  isCtrlFw ? (d.source.fullName || d.source.label) : d.source.label,
            sourceColor: d.source.color,
            targetName:  d.target.label,
            targetColor: d.target.color,
            value:       d.value,
          },
        });
      })
      .on("mousemove", evt => {
        setTooltip(p => p ? { ...p, pos: { x: evt.clientX, y: evt.clientY } } : null);
      })
      .on("mouseout", () => {
        if (!pinId) clearHighlight();
        setTooltip(null);
      });

    svg.on("click", () => {
      if (pinId) { pinId = null; clearHighlight(); }
    });

  }, [controlsRev, allowedFrameworks]);

  return (
    <div style={{
      position: "relative", width: "100%", height: 860,
      borderRadius: 8, overflow: "hidden",
      border: "1px solid #1e293b", background: "#080c14",
    }}>
      {/* Injected column headers as React SVG (positions resolved after layout) */}
      <svg
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}
      >
        {colHdrs.map(h => <ColHeader key={h.label} {...h} />)}
      </svg>

      <svg ref={svgRef} style={{ width: "100%", height: "100%", display: "block" }} />

      {/* Hint */}
      <div style={{
        position: "absolute", top: 10, right: 12, zIndex: 10,
        fontSize: 9, color: "#1e293b", fontFamily: "system-ui, sans-serif",
      }}>
        Hover to highlight · Click to pin
      </div>

      {/* Legend strip — domain colors (shared between controls and domain column) */}
      <div style={{
        position: "absolute", bottom: 10, left: "50%", transform: "translateX(-50%)",
        display: "flex", gap: 12, zIndex: 10,
        background: "rgba(8,12,20,0.8)", border: "1px solid #1e293b",
        borderRadius: 6, padding: "5px 14px",
        fontFamily: "system-ui, sans-serif",
        backdropFilter: "blur(4px)",
        flexWrap: "wrap", maxWidth: "90%", justifyContent: "center",
      }}>
        {Object.entries(DOMAIN_COLOR).map(([dom, color]) => (
          <div key={dom} style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <div style={{ width: 8, height: 8, borderRadius: 2, background: color, opacity: 0.85 }} />
            <span style={{ fontSize: 9, color: "#64748b" }}>{dom}</span>
          </div>
        ))}
        <div style={{ width: 1, background: "#1e293b" }} />
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <div style={{ width: 22, height: 3, background: "linear-gradient(90deg,#3b82f6,#a855f7)", borderRadius: 2, opacity: 0.7 }} />
          <span style={{ fontSize: 9, color: "#64748b" }}>flow width = control count</span>
        </div>
      </div>

      {tooltip && <Tooltip data={tooltip.data} pos={tooltip.pos} />}
    </div>
  );
}
