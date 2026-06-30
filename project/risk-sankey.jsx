import { useEffect, useRef, useState } from "react";
import { sankey, sankeyLinkHorizontal } from "d3-sankey";
import * as d3 from "d3";

// ─── Static graph data (derived from MASTER_CONTROLS) ─────────────────────────
// Three-column layout: Domain → Control Category → Framework

const GRAPH_DATA = {
  nodes: [
    // Domains (depth 0) — ordered by control count desc
    { id: "dom:IT",          label: "IT",          type: "domain",    color: "#3b82f6" },
    { id: "dom:Operational", label: "Operational", type: "domain",    color: "#f59e0b" },
    { id: "dom:Technology",  label: "Technology",  type: "domain",    color: "#22d3ee" },
    { id: "dom:Finance",     label: "Finance",     type: "domain",    color: "#22c55e" },
    { id: "dom:Legal",       label: "Legal",       type: "domain",    color: "#a855f7" },
    { id: "dom:HR",          label: "HR",          type: "domain",    color: "#ec4899" },
    // Categories (depth 1)
    { id: "cat:Access Control", label: "Access Control", type: "category", color: "#60a5fa" },
    { id: "cat:Security",       label: "Security",       type: "category", color: "#f87171" },
    { id: "cat:AI Governance",  label: "AI Governance",  type: "category", color: "#22d3ee" },
    { id: "cat:Risk Mgmt",      label: "Risk Mgmt",      type: "category", color: "#fbbf24" },
    { id: "cat:Financial",      label: "Financial",      type: "category", color: "#34d399" },
    { id: "cat:Operational",    label: "Operational Ctrls", type: "category", color: "#fb923c" },
    { id: "cat:Compliance",     label: "Compliance",     type: "category", color: "#c084fc" },
    { id: "cat:Vendor",         label: "Vendor",         type: "category", color: "#e879f9" },
    { id: "cat:HR",             label: "HR Ctrls",       type: "category", color: "#f472b6" },
    // Frameworks (depth 2) — ordered by control count desc
    { id: "fw:Internal",       label: "Internal",       type: "framework", color: "#94a3b8" },
    { id: "fw:ISO/IEC 42001",  label: "ISO/IEC 42001",  type: "framework", color: "#ec4899" },
    { id: "fw:SOC 2",          label: "SOC 2",          type: "framework", color: "#a855f7" },
    { id: "fw:ISO/IEC 27001",  label: "ISO/IEC 27001",  type: "framework", color: "#22c55e" },
    { id: "fw:NIST SP 800-53", label: "NIST SP 800-53", type: "framework", color: "#3b82f6" },
    { id: "fw:CIS Controls",   label: "CIS Controls",   type: "framework", color: "#f59e0b" },
    { id: "fw:COSO ERM",       label: "COSO ERM",       type: "framework", color: "#f97316" },
  ],
  links: [
    // Domain → Category
    { source: "dom:IT",          target: "cat:Access Control", value: 5 },
    { source: "dom:IT",          target: "cat:Security",       value: 5 },
    { source: "dom:Operational", target: "cat:Risk Mgmt",      value: 4 },
    { source: "dom:Operational", target: "cat:Operational",    value: 3 },
    { source: "dom:Operational", target: "cat:Vendor",         value: 2 },
    { source: "dom:Technology",  target: "cat:AI Governance",  value: 6 },
    { source: "dom:Finance",     target: "cat:Financial",      value: 4 },
    { source: "dom:Legal",       target: "cat:Compliance",     value: 3 },
    { source: "dom:HR",          target: "cat:HR",             value: 2 },
    // Category → Framework
    { source: "cat:Access Control", target: "fw:Internal",        value: 1 },
    { source: "cat:Access Control", target: "fw:NIST SP 800-53",  value: 2 },
    { source: "cat:Access Control", target: "fw:CIS Controls",    value: 1 },
    { source: "cat:Access Control", target: "fw:SOC 2",           value: 1 },
    { source: "cat:Security",       target: "fw:ISO/IEC 27001",   value: 2 },
    { source: "cat:Security",       target: "fw:CIS Controls",    value: 1 },
    { source: "cat:Security",       target: "fw:NIST SP 800-53",  value: 1 },
    { source: "cat:Security",       target: "fw:SOC 2",           value: 1 },
    { source: "cat:AI Governance",  target: "fw:ISO/IEC 42001",   value: 6 },
    { source: "cat:Risk Mgmt",      target: "fw:Internal",        value: 2 },
    { source: "cat:Risk Mgmt",      target: "fw:ISO/IEC 27001",   value: 1 },
    { source: "cat:Risk Mgmt",      target: "fw:COSO ERM",        value: 1 },
    { source: "cat:Financial",      target: "fw:Internal",        value: 3 },
    { source: "cat:Financial",      target: "fw:SOC 2",           value: 1 },
    { source: "cat:Operational",    target: "fw:Internal",        value: 2 },
    { source: "cat:Operational",    target: "fw:ISO/IEC 27001",   value: 1 },
    { source: "cat:Compliance",     target: "fw:Internal",        value: 1 },
    { source: "cat:Compliance",     target: "fw:SOC 2",           value: 2 },
    { source: "cat:Vendor",         target: "fw:Internal",        value: 1 },
    { source: "cat:Vendor",         target: "fw:CIS Controls",    value: 1 },
    { source: "cat:HR",             target: "fw:Internal",        value: 2 },
  ],
};

// ─── Tooltip ──────────────────────────────────────────────────────────────────

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
      borderRadius: 7, padding: "9px 13px", maxWidth: 240,
      zIndex: 9999, pointerEvents: "none",
      boxShadow: `0 6px 32px rgba(0,0,0,0.6)`,
      fontFamily: "system-ui, sans-serif",
    }}>
      {isLink ? (
        <>
          <div style={{ fontSize: 9, color: "#475569", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 5 }}>
            Control Flow
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
      ) : (
        <>
          <div style={{ fontSize: 9, color: data.color, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 5 }}>
            {data.type === "domain" ? "Domain" : data.type === "category" ? "Control Category" : "Framework"}
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

// ─── Column header component ──────────────────────────────────────────────────

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

// ─── Main component ───────────────────────────────────────────────────────────

export function RiskSankey() {
  const svgRef   = useRef(null);
  const [tooltip, setTooltip]  = useState(null);
  const [colHdrs, setColHdrs]  = useState([]);   // column header positions resolved after layout

  useEffect(() => {
    if (!svgRef.current) return;

    const svgEl = svgRef.current;
    const svg   = d3.select(svgEl);
    svg.selectAll("*").remove();

    const W   = svgEl.clientWidth  || 960;
    const H   = svgEl.clientHeight || 640;
    const PAD = { top: 48, bottom: 20, left: 148, right: 168 };

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
      .nodeWidth(20)
      .nodePadding(12)
      .nodeSort(null)
      .extent([[PAD.left, PAD.top], [W - PAD.right, H - PAD.bottom]]);

    const graphData = {
      nodes: GRAPH_DATA.nodes.map(d => ({ ...d })),
      links: GRAPH_DATA.links.map(d => ({ ...d })),
    };
    const { nodes, links } = layout(graphData);

    // Resolve column headers from node positions
    const depthX = {};
    nodes.forEach(n => { if (!(n.depth in depthX)) depthX[n.depth] = (n.x0 + n.x1) / 2; });
    setColHdrs([
      { label: "DOMAIN",   x: depthX[0] ?? PAD.left,         y: PAD.top },
      { label: "CATEGORY", x: depthX[1] ?? W / 2,            y: PAD.top },
      { label: "FRAMEWORK",x: depthX[2] ?? W - PAD.right,    y: PAD.top },
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
      g.append("stop").attr("offset", "0%")  .attr("stop-color", srcColor).attr("stop-opacity", 0.6);
      g.append("stop").attr("offset", "100%").attr("stop-color", tgtColor).attr("stop-opacity", 0.6);
    });

    // ── Links ─────────────────────────────────────────────────────────────────
    const linkGen = sankeyLinkHorizontal();

    const linkG     = svg.append("g").attr("class", "sk-links");
    const linkPaths = linkG.selectAll("path")
      .data(links).join("path")
      .attr("d",              linkGen)
      .attr("fill",           "none")
      .attr("stroke",         (_, i) => `url(#sk-g-${i})`)
      .attr("stroke-width",   d => Math.max(1.5, d.width))
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
      .attr("rx",     5)
      .attr("fill",   d => d.color || "#6b7280")
      .attr("fill-opacity", 0);

    // Main node rect
    nodeGrp.append("rect")
      .attr("x",      d => d.x0)
      .attr("y",      d => d.y0)
      .attr("width",  d => d.x1 - d.x0)
      .attr("height", d => Math.max(2, d.y1 - d.y0))
      .attr("rx",     3)
      .attr("fill",         d => d.color || "#6b7280")
      .attr("fill-opacity", 0.88)
      .attr("stroke",       d => d.color || "#6b7280")
      .attr("stroke-width", 0.4)
      .attr("stroke-opacity", 0.4);

    // Count badge — shown on all nodes tall enough (> 16px)
    nodeGrp.filter(d => (d.y1 - d.y0) >= 16)
      .append("text")
      .attr("x", d => (d.x0 + d.x1) / 2)
      .attr("y", d => (d.y0 + d.y1) / 2)
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("fill", "#ffffff").attr("fill-opacity", 0.9)
      .attr("font-size", 8).attr("font-weight", 700)
      .attr("font-family", "system-ui, sans-serif")
      .attr("pointer-events", "none")
      .text(d => d.value);

    // Labels — left for domains, right for categories & frameworks
    nodeGrp.append("text")
      .attr("x",           d => d.depth === 0 ? d.x0 - 9 : d.x1 + 9)
      .attr("y",           d => (d.y0 + d.y1) / 2)
      .attr("dy",          "0.35em")
      .attr("text-anchor", d => d.depth === 0 ? "end" : "start")
      .attr("fill",        d => d.color || "#94a3b8")
      .attr("fill-opacity", 0.9)
      .attr("font-size",   10)
      .attr("font-weight", 600)
      .attr("font-family", "system-ui, sans-serif")
      .attr("pointer-events", "none")
      .text(d => d.label);

    // ── Highlight helpers ─────────────────────────────────────────────────────
    function linkConnects(l, nodeId) {
      return (typeof l.source === "object" ? l.source.id : l.source) === nodeId
          || (typeof l.target === "object" ? l.target.id : l.target) === nodeId;
    }

    function applyNodeHighlight(nodeId) {
      linkPaths
        .attr("stroke-opacity", l => linkConnects(l, nodeId) ? 0.82 : 0.05);
      marchPaths
        .attr("stroke-opacity", l => linkConnects(l, nodeId) ? 0.35 : 0);
      nodeGrp
        .style("opacity", d => d.id === nodeId
          || links.some(l => linkConnects(l, nodeId) && linkConnects(l, d.id)) ? 1 : 0.2);
    }

    function clearHighlight() {
      linkPaths.attr("stroke-opacity", 0.38);
      marchPaths.attr("stroke-opacity", 0);
      nodeGrp.style("opacity", 1);
      nodeGrp.selectAll(".nk-glow").attr("fill-opacity", 0);
    }

    let pinId = null;

    // Node interaction
    nodeGrp
      .on("mouseover", (evt, d) => {
        if (!pinId) {
          applyNodeHighlight(d.id);
          d3.select(evt.currentTarget).select(".nk-glow").attr("fill-opacity", 0.18);
        }
        setTooltip({ pos: { x: evt.clientX, y: evt.clientY }, data: d });
      })
      .on("mousemove", evt => {
        setTooltip(p => p ? { ...p, pos: { x: evt.clientX, y: evt.clientY } } : null);
      })
      .on("mouseout", evt => {
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
          applyNodeHighlight(d.id);
          nodeGrp.selectAll(".nk-glow").attr("fill-opacity", 0);
          d3.select(evt.currentTarget).select(".nk-glow").attr("fill-opacity", 0.22);
        }
      });

    // Link interaction
    linkPaths
      .on("mouseover", (evt, d, i) => {
        if (!pinId) {
          linkPaths.attr("stroke-opacity", (l, j) => l === d ? 0.85 : 0.05);
          marchPaths.attr("stroke-opacity", (l) => l === d ? 0.4 : 0);
          nodeGrp.style("opacity", n =>
            n.id === d.source.id || n.id === d.target.id ? 1 : 0.15);
        }
        setTooltip({
          pos: { x: evt.clientX, y: evt.clientY },
          data: {
            _isLink:     true,
            sourceName:  d.source.label,
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

  }, []);

  return (
    <div style={{
      position: "relative", width: "100%", height: 640,
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

      {/* Legend strip */}
      <div style={{
        position: "absolute", bottom: 10, left: "50%", transform: "translateX(-50%)",
        display: "flex", gap: 16, zIndex: 10,
        background: "rgba(8,12,20,0.8)", border: "1px solid #1e293b",
        borderRadius: 6, padding: "5px 14px",
        fontFamily: "system-ui, sans-serif",
        backdropFilter: "blur(4px)",
      }}>
        {[
          { color: "#3b82f6", label: "Domain" },
          { color: "#f87171", label: "Category" },
          { color: "#ec4899", label: "Framework" },
        ].map(({ color, label }) => (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <div style={{ width: 10, height: 10, borderRadius: 2, background: color, opacity: 0.85 }} />
            <span style={{ fontSize: 9, color: "#64748b" }}>{label}</span>
          </div>
        ))}
        <div style={{ width: 1, background: "#1e293b" }} />
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <div style={{ width: 22, height: 3, background: "linear-gradient(90deg,#3b82f6,#a855f7)", borderRadius: 2, opacity: 0.7 }} />
          <span style={{ fontSize: 9, color: "#64748b" }}>Control flow (width = count)</span>
        </div>
      </div>

      {tooltip && <Tooltip data={tooltip.data} pos={tooltip.pos} />}
    </div>
  );
}
