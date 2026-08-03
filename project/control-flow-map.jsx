/* ============================================================
   Control Flow Map — a real process-mining view of the adjudication
   log: a directly-follows-graph (Source System -> Risk Tier ->
   Verdict -> Fired Control) built from REAL observability.
   adjudicated_tool_calls rows, edge width = actual observed event
   count. Contrast risk-sankey.jsx, which renders the curated control
   catalog's static structure (which controls exist, tagged with which
   frameworks/domains) — that's a taxonomy diagram; this mines what
   actually happened. Control nodes carry their SOC 2/NIST/ISO/COSO
   crosswalk (from controls_catalog, via GET /pac/control-flow-map) so
   multi-framework mapping shows up on hover without a 5th column.

   Data: GET /pac/control-flow-map?days=N (pac_endpoints.py). Same
   d3-sankey/d3 approach as risk-sankey.jsx.
   ============================================================ */
import { useCallback, useEffect, useRef, useState } from "react";
import { sankey, sankeyLinkHorizontal } from "d3-sankey";
import * as d3 from "d3";

const TIER_COLOR = {
  CRITICAL: "#ef4444",
  HIGH:     "#f97316",
  MEDIUM:   "#f59e0b",
  LOW:      "#22c55e",
  UNKNOWN:  "#64748b",
};

const VERDICT_COLOR = {
  ESCALATE: "#ef4444",
  MONITOR:  "#3b82f6",
  CLEAR:    "#22c55e",
  UNKNOWN:  "#64748b",
};

const SYSTEM_PALETTE = ["#a855f7", "#22d3ee", "#ec4899", "#84cc16", "#f97316", "#3b82f6", "#eab308", "#14b8a6"];
const CONTROL_MAPPED_COLOR   = "#22d3ee";
const CONTROL_UNMAPPED_COLOR = "#475569";
const FALLBACK_COLOR = "#64748b";

function colorForSystem(name, cache) {
  if (!cache.has(name)) cache.set(name, SYSTEM_PALETTE[cache.size % SYSTEM_PALETTE.length]);
  return cache.get(name);
}

function hasFrameworkMapping(node) {
  return !!(
    (node.soc2_criteria && node.soc2_criteria.length) ||
    (node.nist_800_53 && node.nist_800_53.length) ||
    (node.iso_27001 && node.iso_27001.length) ||
    node.coso_component
  );
}

function decorateNodes(nodes) {
  const systemColors = new Map();
  return nodes.map(n => {
    if (n.type === "system") return { ...n, color: colorForSystem(n.label, systemColors) };
    if (n.type === "tier")   return { ...n, color: TIER_COLOR[n.label] || FALLBACK_COLOR };
    if (n.type === "verdict") return { ...n, color: VERDICT_COLOR[n.label] || FALLBACK_COLOR };
    if (n.type === "control") return { ...n, color: hasFrameworkMapping(n) ? CONTROL_MAPPED_COLOR : CONTROL_UNMAPPED_COLOR };
    return { ...n, color: FALLBACK_COLOR };
  });
}

function frameworkSummary(node) {
  const parts = [];
  if (node.soc2_criteria?.length) parts.push(`SOC 2: ${node.soc2_criteria.join(", ")}`);
  if (node.nist_800_53?.length) parts.push(`NIST 800-53: ${node.nist_800_53.join(", ")}`);
  if (node.iso_27001?.length) parts.push(`ISO 27001: ${node.iso_27001.join(", ")}`);
  if (node.coso_component) parts.push(`COSO: ${node.coso_component}`);
  return parts;
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
      border: `1px solid ${accent}`,
      borderLeft: `3px solid ${isLink ? "#ffffff66" : accent}`,
      borderRadius: 7, padding: "9px 13px", maxWidth: 280,
      zIndex: 9999, pointerEvents: "none",
      boxShadow: "0 6px 32px rgba(0,0,0,0.6)",
      fontFamily: "system-ui, sans-serif",
    }}>
      {isLink ? (
        <>
          <div style={{ fontSize: 11, color: "#e2e8f0", fontWeight: 600, lineHeight: 1.5 }}>
            <span style={{ color: data.sourceColor }}>{data.sourceLabel}</span>
            <span style={{ color: "#334155", margin: "0 6px" }}>→</span>
            <span style={{ color: data.targetColor }}>{data.targetLabel}</span>
          </div>
          <div style={{ marginTop: 5, fontSize: 10, color: "#94a3b8" }}>
            <span style={{ color: "#e2e8f0", fontWeight: 700 }}>{data.value}</span> event{data.value !== 1 ? "s" : ""}
          </div>
        </>
      ) : data.type === "control" ? (
        <>
          <div style={{ fontSize: 9, color: data.color, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4 }}>
            Control
          </div>
          <div style={{ fontFamily: "monospace", fontSize: 11, color: "#94a3b8", marginBottom: 3 }}>{data.label}</div>
          {data.name && <div style={{ fontSize: 11, color: "#e2e8f0", fontWeight: 600, lineHeight: 1.4, marginBottom: 6 }}>{data.name}</div>}
          {frameworkSummary(data).length ? (
            frameworkSummary(data).map((line, i) => (
              <div key={i} style={{ fontSize: 10, color: "#94a3b8" }}>{line}</div>
            ))
          ) : (
            <div style={{ fontSize: 10, color: "#64748b", fontStyle: "italic" }}>Not yet mapped to a framework</div>
          )}
        </>
      ) : (
        <>
          <div style={{ fontSize: 9, color: data.color, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 5 }}>
            {data.type}
          </div>
          <div style={{ fontSize: 12, color: "#e2e8f0", fontWeight: 600, marginBottom: 5 }}>{data.label}</div>
          <div style={{ fontSize: 10, color: "#94a3b8" }}>
            <span style={{ color: "#e2e8f0", fontWeight: 700 }}>{data.value}</span> event{data.value !== 1 ? "s" : ""}
          </div>
        </>
      )}
    </div>
  );
}

function ColHeader({ label, x, y }) {
  return (
    <g>
      <text x={x} y={y - 10} textAnchor="middle" fill="#334155" fontSize={9} fontWeight={700}
        letterSpacing="0.12em" fontFamily="system-ui, sans-serif">{label}</text>
      <line x1={x - 40} x2={x + 40} y1={y - 3} y2={y - 3} stroke="#1e293b" strokeWidth={1} />
    </g>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────

function _cfmBase() {
  return (window.MCP_API_BASE || "/api/mcp") + "/pac";
}

export function ControlFlowMap() {
  const svgRef = useRef(null);
  const [tooltip, setTooltip] = useState(null);
  const [colHdrs, setColHdrs] = useState([]);
  const [days, setDays] = useState(30);
  const [graph, setGraph] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    return fetch(`${_cfmBase()}/control-flow-map?days=${days}`, { credentials: "include" })
      .then(res => {
        if (!res.ok) throw new Error(`Failed to load control flow map (${res.status})`);
        return res.json();
      })
      .then(d => { setGraph(d); setError(d?.note || null); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [days]);

  useEffect(() => { load(); }, [load]);

  const hasData = graph && graph.nodes && graph.nodes.length > 0;

  useEffect(() => {
    if (!svgRef.current || !hasData) return;

    const svgEl = svgRef.current;
    const svg = d3.select(svgEl);
    svg.selectAll("*").remove();

    const W = svgEl.clientWidth || 960;
    const H = svgEl.clientHeight || 640;
    const PAD = { top: 48, bottom: 20, left: 84, right: 142 };

    const defs = svg.append("defs");
    const dotPat = defs.append("pattern").attr("id", "cfm-dots")
      .attr("patternUnits", "userSpaceOnUse").attr("width", 24).attr("height", 24);
    dotPat.append("circle").attr("cx", 12).attr("cy", 12).attr("r", 0.8).attr("fill", "#ffffff08");
    svg.append("rect").attr("width", "100%").attr("height", "100%").attr("fill", "#080c14");
    svg.append("rect").attr("width", "100%").attr("height", "100%").attr("fill", "url(#cfm-dots)");

    const layout = sankey()
      .nodeId(d => d.id)
      .nodeWidth(14)
      .nodePadding(4)
      .nodeSort(null)
      .extent([[PAD.left, PAD.top], [W - PAD.right, H - PAD.bottom]]);

    const nodes = decorateNodes(graph.nodes).map(d => ({ ...d }));
    const links = graph.links.map(d => ({ ...d }));
    const { nodes: laidOutNodes, links: laidOutLinks } = layout({ nodes, links });

    const depthX = {};
    laidOutNodes.forEach(n => { if (!(n.depth in depthX)) depthX[n.depth] = (n.x0 + n.x1) / 2; });
    setColHdrs([
      { label: "SOURCE SYSTEM", x: depthX[0] ?? PAD.left, y: PAD.top },
      { label: "RISK TIER",     x: depthX[1] ?? W * 0.36, y: PAD.top },
      { label: "VERDICT",       x: depthX[2] ?? W * 0.64, y: PAD.top },
      { label: "CONTROL FIRED", x: depthX[3] ?? W - PAD.right, y: PAD.top },
    ]);

    laidOutLinks.forEach((l, i) => {
      const srcColor = l.source.color || "#6b7280";
      const tgtColor = l.target.color || "#6b7280";
      const g = defs.append("linearGradient")
        .attr("id", `cfm-g-${i}`).attr("gradientUnits", "userSpaceOnUse")
        .attr("x1", l.source.x1).attr("y1", (l.source.y0 + l.source.y1) / 2)
        .attr("x2", l.target.x0).attr("y2", (l.target.y0 + l.target.y1) / 2);
      g.append("stop").attr("offset", "0%").attr("stop-color", srcColor).attr("stop-opacity", 0.55);
      g.append("stop").attr("offset", "100%").attr("stop-color", tgtColor).attr("stop-opacity", 0.55);
    });

    const linkGen = sankeyLinkHorizontal();
    const linkG = svg.append("g").attr("class", "cfm-links");
    const linkPaths = linkG.selectAll("path").data(laidOutLinks).join("path")
      .attr("d", linkGen).attr("fill", "none")
      .attr("stroke", (_, i) => `url(#cfm-g-${i})`)
      .attr("stroke-width", d => Math.max(1, d.width))
      .attr("stroke-opacity", 0.38).attr("cursor", "pointer");

    const nodeG = svg.append("g").attr("class", "cfm-nodes");
    const nodeGrp = nodeG.selectAll("g").data(laidOutNodes).join("g").attr("cursor", "pointer");

    nodeGrp.append("rect")
      .attr("x", d => d.x0).attr("y", d => d.y0)
      .attr("width", d => d.x1 - d.x0).attr("height", d => Math.max(2, d.y1 - d.y0))
      .attr("rx", 2)
      .attr("fill", d => d.color || "#6b7280").attr("fill-opacity", 0.85)
      .attr("stroke", d => d.color || "#6b7280").attr("stroke-width", 0.4).attr("stroke-opacity", 0.4);

    nodeGrp.filter(d => (d.y1 - d.y0) >= 14)
      .append("text")
      .attr("x", d => (d.x0 + d.x1) / 2).attr("y", d => (d.y0 + d.y1) / 2)
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("fill", "#ffffff").attr("fill-opacity", 0.9)
      .attr("font-size", 8).attr("font-weight", 700)
      .attr("font-family", "system-ui, sans-serif").attr("pointer-events", "none")
      .text(d => d.value);

    nodeGrp.append("text")
      .attr("x", d => d.depth === 0 ? d.x0 - 7 : d.x1 + 7)
      .attr("y", d => (d.y0 + d.y1) / 2).attr("dy", "0.35em")
      .attr("text-anchor", d => d.depth === 0 ? "end" : "start")
      .attr("fill", d => d.color || "#94a3b8").attr("fill-opacity", 0.88)
      .attr("font-size", d => d.type === "control" ? 8 : 10)
      .attr("font-weight", d => d.type === "control" ? 500 : 600)
      .attr("font-family", d => d.type === "control" ? "monospace" : "system-ui, sans-serif")
      .attr("pointer-events", "none")
      .text(d => d.label);

    function linkId(l, end) { const v = l[end]; return typeof v === "object" ? v.id : v; }
    function linkConnects(l, nodeId) { return linkId(l, "source") === nodeId || linkId(l, "target") === nodeId; }

    function applyHighlight(nodeId) {
      linkPaths.attr("stroke-opacity", l => linkConnects(l, nodeId) ? 0.85 : 0.05);
      nodeGrp.style("opacity", d => d.id === nodeId
        || laidOutLinks.some(l => linkConnects(l, nodeId) && linkConnects(l, d.id)) ? 1 : 0.18);
    }
    function clearHighlight() {
      linkPaths.attr("stroke-opacity", 0.38);
      nodeGrp.style("opacity", 1);
    }

    nodeGrp
      .on("mouseover", (evt, d) => { applyHighlight(d.id); setTooltip({ pos: { x: evt.clientX, y: evt.clientY }, data: d }); })
      .on("mousemove", evt => setTooltip(p => p ? { ...p, pos: { x: evt.clientX, y: evt.clientY } } : null))
      .on("mouseout", () => { clearHighlight(); setTooltip(null); });

    linkPaths
      .on("mouseover", (evt, d) => {
        linkPaths.attr("stroke-opacity", l => l === d ? 0.9 : 0.05);
        nodeGrp.style("opacity", n => n.id === d.source.id || n.id === d.target.id ? 1 : 0.15);
        setTooltip({
          pos: { x: evt.clientX, y: evt.clientY },
          data: { _isLink: true, sourceLabel: d.source.label, sourceColor: d.source.color,
                  targetLabel: d.target.label, targetColor: d.target.color, value: d.value },
        });
      })
      .on("mousemove", evt => setTooltip(p => p ? { ...p, pos: { x: evt.clientX, y: evt.clientY } } : null))
      .on("mouseout", () => { clearHighlight(); setTooltip(null); });
  }, [graph, hasData]);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <div className="kicker">Continuous evidence · Control Flow Map</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <select value={days} onChange={e => setDays(Number(e.target.value))}
            style={{ fontSize: 11, padding: "3px 6px", borderRadius: 4, border: "1px solid var(--line)", background: "var(--surface)", color: "var(--ink)" }}>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
          <button type="button" className="btn btn-sm" onClick={load}>Refresh</button>
        </div>
      </div>
      <div className="panel-sub" style={{ marginBottom: 10 }}>
        What actually flowed through adjudication in the last {days} days — source system → risk tier →
        verdict → the specific controls that fired, mined from the real event log, not the control catalog.
        Hover a control for its SOC 2 / NIST / ISO / COSO mapping.
      </div>

      {error && !hasData && (
        <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", marginBottom: 10 }}>{error}</div>
      )}

      <div style={{
        position: "relative", width: "100%", height: 640,
        borderRadius: 8, overflow: "hidden",
        border: "1px solid #1e293b", background: "#080c14",
      }}>
        {loading && !graph && (
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#64748b", fontSize: 12, fontFamily: "system-ui, sans-serif" }}>
            Loading…
          </div>
        )}
        {!loading && !hasData && !error && (
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#64748b", fontSize: 12, fontFamily: "system-ui, sans-serif", textAlign: "center", padding: 24 }}>
            No adjudicated events in the last {days} days yet — this fills in as the governance pipeline runs.
          </div>
        )}

        <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}>
          {colHdrs.map(h => <ColHeader key={h.label} {...h} />)}
        </svg>
        <svg ref={svgRef} style={{ width: "100%", height: "100%", display: "block" }} />

        <div style={{
          position: "absolute", bottom: 10, left: "50%", transform: "translateX(-50%)",
          display: "flex", gap: 12, zIndex: 10,
          background: "rgba(8,12,20,0.8)", border: "1px solid #1e293b",
          borderRadius: 6, padding: "5px 14px", fontFamily: "system-ui, sans-serif",
          backdropFilter: "blur(4px)", flexWrap: "wrap", maxWidth: "90%", justifyContent: "center",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <div style={{ width: 8, height: 8, borderRadius: 2, background: CONTROL_MAPPED_COLOR, opacity: 0.85 }} />
            <span style={{ fontSize: 9, color: "#64748b" }}>control mapped to a framework</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <div style={{ width: 8, height: 8, borderRadius: 2, background: CONTROL_UNMAPPED_COLOR, opacity: 0.85 }} />
            <span style={{ fontSize: 9, color: "#64748b" }}>not yet mapped</span>
          </div>
          <div style={{ width: 1, background: "#1e293b" }} />
          <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <div style={{ width: 22, height: 3, background: "linear-gradient(90deg,#3b82f6,#a855f7)", borderRadius: 2, opacity: 0.7 }} />
            <span style={{ fontSize: 9, color: "#64748b" }}>flow width = real event count</span>
          </div>
        </div>

        {tooltip && <Tooltip data={tooltip.data} pos={tooltip.pos} />}
      </div>
    </div>
  );
}
