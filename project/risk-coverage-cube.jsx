/* ============================================================
   Risk Coverage Cube — two COSO-aligned views, selected by a
   framework toggle. Corrected 2026-08-26 after an audit found the
   original single "cube" mixed COSO ERM 2017 components with
   COSO ERM 2004's objective categories, in a cube shape COSO itself
   retired when it published ERM 2017 (replaced by a helix). See
   risk_coverage_cube.py's module docstring for the full rationale.

   COSO Cube (IC-IF 2013, default) — the real COSO Cube, still
   current:
     X (ground)   IC-IF component, driven by the mapped CONTROL,
                  not the risk (the fix for "2 of 5 columns
                  structurally unreachable" — see
                  risk_coverage_cube.py's build_icif_cube)
     Z (ground)   objective category (Operations/Reporting/
                  Compliance — IC-IF has no "Strategic")
     Y (vertical) operating unit, stacked as layers — Consolidated
                  at the base (real risk bars), any filed/uploaded
                  segment revenue as labeled layers above it.
   Bar height WITHIN a layer separately encodes risk_count — the
   only way to fit 2 categorical axes + 1 magnitude + 1 more
   categorical axis into 3 spatial dimensions.

   COSO ERM 2017 — NOT a cube (COSO ERM 2017 doesn't have one): a
   flat component x principle conformance list (ErmEvidencePanel),
   answering "is this ERM activity evidenced?" from real persisted
   artifacts, never a 3D shape.

   Standalone nav screen, scoped to the ticker's latest risk-loop
   run (risk_coverage_cube.py) — spans the whole loop plus
   RaC/CaC/PaC, so it doesn't belong inside a single Assess Risk
   stage canvas.

   Color (COSO Cube view) = two independent signals, never merged:
     - component IDENTITY: a fixed categorical hue per IC-IF
       component (_ICIF_COLOR) — COSO has no official per-component
       color standard, so this is a hand-picked, colorblind-mindful
       palette, chosen to stay clear of the red/amber/green band
       already used for RAG severity and reused nowhere else here.
     - coverage STATE: fill opacity + edge weight on top of that
       hue — empty (flat neutral gray, no component color at all
       for a cell with nothing in it), mapped_unverified (hollow,
       ~30% fill), verified (solid, ~95% fill) — never collapsed to
       a binary green/red, and never merged with the loop's own
       inferred control_env (shown separately in the detail panel).

   Operating-unit layers above Consolidated are honestly incomplete
   by design: no risk or control anywhere in the schema is tagged
   to a segment, so those layers are real (named, revenue-weighted)
   but carry no bars — a translucent labeled slab, not a fabricated
   breakdown. Division/Function (IC-IF's other org-structure levels)
   are omitted from the axis entirely (see data.omitted_z_levels) —
   no data source, so no permanently-empty level is rendered either.

   Clicking a bar dollies the camera toward it and opens the same
   detail panel a plain click would. A Table view (the original
   flat grid) stays available as an equivalent, non-WebGL-dependent
   fallback — see dataviz accessibility guidance: color/state is
   never the only way to read a cell.
   ============================================================ */
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

const _RAG_META = {
  R: { label: "Red",   color: "var(--red-ink)" },
  A: { label: "Amber", color: "var(--amber-ink)" },
  G: { label: "Green", color: "var(--green-ink)" },
};

// IC-IF 2013 — the framework this Cube view actually draws (COSO ERM 2017
// has no cube; see ErmEvidencePanel below for that framework's view).
const _COMPONENT_SHORT = {
  "Control Environment": "Control Env",
  "Risk Assessment": "Risk Assessment",
  "Control Activities": "Control Activities",
  "Information & Communication": "Info & Comms",
  "Monitoring Activities": "Monitoring",
  "Unmapped": "Unmapped",
};

// COSO has no single official per-component color standard the way RAG has
// red/amber/green — this is a hand-picked, fixed-order categorical palette
// (one hue per IC-IF 2013 component, Unmapped as neutral gray), chosen to
// stay clear of the red/amber/green band already carrying RAG-severity and
// coverage-state meaning elsewhere on this screen. Color now encodes
// component IDENTITY; coverage STATE is encoded separately via fill opacity
// + edge weight (see _cellStyle) so the two signals never collide.
const _ICIF_COLOR = {
  "Control Environment": "#17A398",
  "Risk Assessment": "#2E7BD6",
  "Control Activities": "#6C5CE7",
  "Information & Communication": "#C43FA6",
  "Monitoring Activities": "#9B4FE0",
  "Unmapped": "#9AA0A6",
};
// Kept as an alias — the bulk of this file's cell-rendering code (written
// when this screen only had one, mislabelled view) refers to the palette by
// this name; renaming every call site wasn't worth the diff noise.
const _COSO_COLOR = _ICIF_COLOR;

const _STATE_LABEL = { empty: "No coverage", mapped_unverified: "Mapped, unverified", verified: "Verified" };

// Coverage state -> fill opacity + edge treatment, independent of the
// component hue above. Empty always renders as flat neutral gray regardless
// of component — a cell with nothing in it shouldn't borrow a component's
// color and imply something's there.
function _cellStyle(component, state, palette) {
  const hex = _COSO_COLOR[component] || _COSO_COLOR.Unmapped;
  if (state === "verified") return { fill: hex, fillOpacity: 0.92, edge: hex, edgeWeight: 2 };
  if (state === "mapped_unverified") return { fill: hex, fillOpacity: 0.32, edge: hex, edgeWeight: 1 };
  return { fill: "#" + palette.empty.toString(16).padStart(6, "0"), fillOpacity: 1, edge: "#" + palette.line.toString(16).padStart(6, "0"), edgeWeight: 1 };
}

function CubeLegend() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
        <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)" }}>IC-IF component:</span>
        {Object.entries(_COSO_COLOR).map(([comp, hex]) => (
          <div key={comp} style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 10, height: 10, borderRadius: "50%", display: "inline-block", background: hex }} />
            <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>{_COMPONENT_SHORT[comp] || comp}</span>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
        <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)" }}>Coverage:</span>
        {["empty", "mapped_unverified", "verified"].map(state => {
          const s = _cellStyle("Unmapped", state, { empty: 0xe5e2da, line: 0xe2ded2 });
          return (
            <div key={state} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{
                width: 12, height: 12, borderRadius: 3, display: "inline-block",
                background: s.fill, opacity: state === "empty" ? 1 : s.fillOpacity,
                border: `${s.edgeWeight}px solid ${s.edge}`,
              }} />
              <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>{_STATE_LABEL[state]}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CubeCell({ cell, onSelect, selected, entityLabel = "Consolidated" }) {
  const empty = cell.state === "empty";
  const s = _cellStyle(cell.coso_component, cell.state, { empty: 0xe5e2da, line: 0xe2ded2 });
  return (
    <button
      type="button"
      onClick={() => !empty && onSelect(cell)}
      disabled={empty}
      title={empty ? "No risk in this cell" : `${cell.risk_count} risk(s) (RaC), ${cell.mapped_control_count} control(s) (CaC) — ${entityLabel} — click for detail`}
      style={{
        width: "100%", minHeight: 76, padding: "8px 10px", position: "relative",
        display: "flex", flexDirection: "column", justifyContent: "space-between", gap: 2,
        borderRadius: 6, textAlign: "left", cursor: empty ? "default" : "pointer",
        background: empty ? s.fill : _rgba(s.fill, s.fillOpacity),
        border: selected ? "2px solid var(--acc)" : `${s.edgeWeight}px solid ${empty ? "var(--line)" : s.edge}`,
      }}
    >
      {empty ? (
        <span className="mono" style={{ fontSize: 10, color: "var(--ink-4)" }}>—</span>
      ) : (
        <>
          {/* Indicates the cell opens a detail panel — a cursor change alone
              is invisible until the pointer happens to be there. */}
          <span aria-hidden="true" title="Click for detail" style={{
            position: "absolute", top: 5, right: 6, fontSize: 11, lineHeight: 1,
            color: "var(--ink-3)", opacity: 0.75,
          }}>⌕</span>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <span className="mono" style={{ fontSize: 16, fontWeight: 600, color: "var(--ink)" }}>
              {cell.risk_count}
            </span>
            {cell.worst_rag && (
              <span className="mono" style={{
                fontSize: 9, fontWeight: 600, color: _RAG_META[cell.worst_rag]?.color || "var(--ink-3)", marginRight: 12,
              }}>
                {cell.worst_rag}
              </span>
            )}
          </div>
          <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-2)" }}>{_STATE_LABEL[cell.state]}</span>
          <span className="mono" style={{ fontSize: 8.5, color: "var(--ink-4)" }}>
            RaC {cell.risk_count} · CaC {cell.verified_control_count}/{cell.mapped_control_count} · {entityLabel}
          </span>
        </>
      )}
    </button>
  );
}

function _rgba(hex, alpha) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function CubeGridTable({ data, entity, cellsByKey, selectedKey, onSelectKey }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <div style={{
        display: "grid",
        gridTemplateColumns: `140px repeat(${data.coso_components.length}, minmax(110px, 1fr))`,
        gap: 6, minWidth: 780,
      }}>
        <div />
        {data.coso_components.map(c => (
          <div key={c} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: _COSO_COLOR[c] || _COSO_COLOR.Unmapped }} />
            <div className="mono" style={{
              fontSize: 9.5, color: "var(--ink-3)", textAlign: "center", padding: "0 2px", textWrap: "balance",
            }}>{c}</div>
          </div>
        ))}

        {data.objective_categories.map(row => (
          <React.Fragment key={row}>
            <div className="mono" style={{
              fontSize: 10.5, color: "var(--ink-2, var(--ink))", fontWeight: 600,
              display: "flex", alignItems: "center",
            }}>{row}</div>
            {data.coso_components.map(col => {
              const key = `${entity}::${row}::${col}`;
              const cell = cellsByKey[key] || { objective_category: row, coso_component: col, entity, state: "empty", risk_count: 0 };
              return (
                <CubeCell key={key} cell={cell} selected={selectedKey === key} entityLabel={entity}
                  onSelect={() => onSelectKey(prev => (prev === key ? null : key))} />
              );
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

function EntitySelector({ entities, selected, onSelect }) {
  if (!entities || entities.length <= 1) return null;
  return (
    <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
      <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", alignSelf: "center", marginRight: 2 }}>Entity:</span>
      {entities.map(e => (
        <button key={e} type="button" onClick={() => onSelect(e)} className="mono" style={{
          fontSize: 10, padding: "3px 9px", borderRadius: 5, cursor: "pointer",
          border: `1px solid ${e === selected ? "var(--acc)" : "var(--line)"}`,
          background: e === selected ? "var(--acc)" : "var(--surface)",
          color: e === selected ? "var(--surface)" : "var(--ink-3)",
        }}>{e}</button>
      ))}
    </div>
  );
}

// ── 3D bar landscape ─────────────────────────────────────────────────────
// Ground plane = coso_component (x) x objective_category (z); bar height
// (y) = sqrt-scaled risk_count so one concentrated cell doesn't flatten the
// rest of the landscape; bar color = the same three-state palette as the
// table view, resolved from the live CSS custom properties (via an offscreen
// canvas fillStyle round-trip) so dark/light mode and the toggle both just
// work with zero duplicated color literals.
function _resolveCssColor(cssValue, fallbackHex) {
  try {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = cssValue;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
    return (r << 16) | (g << 8) | b;
  } catch {
    return fallbackHex;
  }
}

function _cubePalette() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name, fb) => (cs.getPropertyValue(name) || "").trim() || fb;
  return {
    empty:    _resolveCssColor(v("--surface-3", "#e5e2da"), 0xe5e2da),
    ink:      _resolveCssColor(v("--ink", "#232019"), 0x232019),
    ink3:     _resolveCssColor(v("--ink-3", "#8a8370"), 0x8a8370),
    line:     _resolveCssColor(v("--line", "#e2ded2"), 0xe2ded2),
    accent:   _resolveCssColor(v("--acc", "#2f8f5a"), 0x2f8f5a),
    surface:  _resolveCssColor(v("--surface", "#faf8f3"), 0xfaf8f3),
  };
}

// Z axis — operating unit. `entities` comes straight from the backend
// (risk_coverage_cube.py's build_icif_cube): "Consolidated" plus every distinct
// segment_name a real risk was tagged with by segment_risk_tool.py
// (Concentration/Decline/Divergence) — these get REAL bars, same as
// Consolidated. A segment with filed/uploaded revenue (`segments`) but no
// risk of its own yet (below every threshold, or Phase 2 forecasting
// skipped it for insufficient history) is still drawn as a labeled ghost
// layer — real entity, no risk data on it yet — so the axis reflects what's
// actually known rather than hiding the gap.
function _operatingUnitLayers(entities, segments) {
  const real = (entities && entities.length ? entities : ["Consolidated"]).map(name => ({ name, real: true }));
  const realNames = new Set(real.map(l => l.name));
  const ghostCandidates = (segments || []).filter(s => !realNames.has(s.segment_name));
  const ghosts = [...ghostCandidates]
    .sort((a, b) => (b.revenue_pct || 0) - (a.revenue_pct || 0))
    .slice(0, 3)
    .map(s => ({ name: s.segment_name, real: false, revenuePct: s.revenue_pct, segmentType: s.segment_type }));
  return [...real, ...ghosts];
}

// Bar height for a given risk_count — sqrt-scaled so one concentrated cell
// doesn't flatten the rest of the landscape. Shared by the bars themselves
// and by the vertical axis ticks below, so the ruler always matches what's
// actually drawn.
function _barHeight(riskCount) {
  return 0.12 + Math.sqrt(riskCount || 0) * 0.55;
}

// "Nice" tick values for the vertical (risk-count) axis: 0 plus evenly
// stepped counts up to the max, in a round step (1/2/5/10...) rather than
// literal max/N divisions.
function _niceTicks(maxCount) {
  if (maxCount <= 0) return [0];
  const step = maxCount <= 5 ? 1 : maxCount <= 10 ? 2 : maxCount <= 25 ? 5 : 10;
  const ticks = [];
  for (let v = 0; v <= maxCount; v += step) ticks.push(v);
  if (ticks[ticks.length - 1] !== maxCount) ticks.push(maxCount);
  return ticks;
}

function _makeTextSprite(text, { fontSize = 42, color = "#333333" } = {}) {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  ctx.font = `600 ${fontSize}px system-ui, -apple-system, sans-serif`;
  const padding = 14;
  const w = Math.ceil(ctx.measureText(text).width) + padding * 2;
  const h = fontSize + padding * 2;
  canvas.width = w; canvas.height = h;
  ctx.font = `600 ${fontSize}px system-ui, -apple-system, sans-serif`;
  ctx.fillStyle = color;
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  ctx.fillText(text, w / 2, h / 2);
  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false });
  const sprite = new THREE.Sprite(material);
  const scale = 0.017;
  sprite.scale.set(w * scale, h * scale, 1);
  return sprite;
}

function Cube3D({ data, cellsByKey, onSelectKey }) {
  const mountRef = useRef(null);
  const onSelectKeyRef = useRef(onSelectKey);
  onSelectKeyRef.current = onSelectKey;
  const [webglError, setWebglError] = useState(false);
  const [hoverInfo, setHoverInfo] = useState(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !data) return;
    let renderer, scene, camera, controls, raf, ro;
    let disposed = false;

    try {
      const palette = _cubePalette();
      const nCols = data.coso_components.length;
      const nRows = data.objective_categories.length;
      const spacing = 1.6;
      const baseX = -((nCols - 1) * spacing) / 2;
      const baseZ = -((nRows - 1) * spacing) / 2;

      scene = new THREE.Scene();
      scene.background = new THREE.Color(palette.surface);

      const width = mount.clientWidth || 780;
      const height = 460;
      camera = new THREE.PerspectiveCamera(42, width / height, 0.1, 100);
      // A steeper, more top-down angle than a typical orbit default — the
      // axis labels are camera-facing sprites, and a shallow oblique angle
      // packs them close enough in screen space to overlap. Looking down
      // more spreads them out; users can still orbit to a flatter angle
      // themselves once oriented.
      camera.position.set(nCols * 0.55, nRows * 2.85, nRows * 1.05);

      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setSize(width, height);
      mount.innerHTML = "";
      mount.appendChild(renderer.domElement);

      const maxCount = Math.max(1, ...data.cells.map(c => c.risk_count || 0));
      const topH = _barHeight(maxCount);
      const layers = _operatingUnitLayers(data.entities, data.segments);
      const layerGap = topH + 1.1;
      const stackHeight = (layers.length - 1) * layerGap;

      controls = new OrbitControls(camera, renderer.domElement);
      controls.target.set(0, 0.6 + stackHeight / 2, 0);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.minDistance = 2.5;
      controls.maxDistance = nCols * 4.5 + stackHeight * 2;
      controls.maxPolarAngle = Math.PI / 2.05;
      camera.position.y += stackHeight * 1.3;
      camera.position.z += stackHeight * 0.6;
      controls.update();

      scene.add(new THREE.AmbientLight(0xffffff, 0.75));
      const dir = new THREE.DirectionalLight(0xffffff, 0.6);
      dir.position.set(4, 8 + stackHeight, 5);
      scene.add(dir);

      const boxGeo = new THREE.BoxGeometry(1, 1, 1);
      const clickable = [];
      const inkHex = "#" + palette.ink.toString(16).padStart(6, "0");
      const ink3Hex = "#" + palette.ink3.toString(16).padStart(6, "0");

      layers.forEach((layer, li) => {
        const yBase = li * layerGap;

        const grid = new THREE.GridHelper(Math.max(nCols, nRows) * spacing + 1, 20, palette.line, palette.line);
        grid.position.y = yBase;
        scene.add(grid);

        // Layer / operating-unit axis label — the third (Z) axis this whole
        // stack exists to represent, read top-to-bottom rather than left-
        // right/front-back since it shares the vertical dimension with bar
        // height (see module comment: only 3 spatial axes exist, and two are
        // already spent on component x objective category).
        const layerLabel = _makeTextSprite(
          layer.real ? layer.name : `${layer.name}${layer.revenuePct != null ? ` · ${layer.revenuePct}% rev` : ""}`,
          { fontSize: 28, color: layer.real ? inkHex : ink3Hex }
        );
        layerLabel.position.set(baseX - spacing * 2.5, yBase + 0.1, baseZ - spacing * 0.2);
        scene.add(layerLabel);

        if (!layer.real) {
          // Ghost layer: a real, named operating unit (filed/uploaded
          // segment revenue), but with no risk bars — no risk or control
          // anywhere in the schema is tagged to a segment yet, so drawing
          // bars here would fabricate a breakdown that doesn't exist. The
          // flat translucent slab + label is the honest representation:
          // the axis is real, this slice's risk data isn't (yet).
          const slab = new THREE.Mesh(
            new THREE.BoxGeometry(nCols * spacing * 0.94, 0.03, nRows * spacing * 0.94),
            new THREE.MeshBasicMaterial({ color: palette.line, transparent: true, opacity: 0.35 })
          );
          slab.position.set(0, yBase, 0);
          scene.add(slab);
          return;
        }

        data.objective_categories.forEach((row, ri) => {
          data.coso_components.forEach((col, ci) => {
            const key = `${layer.name}::${row}::${col}`;
            const cell = cellsByKey[key] || { objective_category: row, coso_component: col, entity: layer.name, state: "empty", risk_count: 0 };
            const h = _barHeight(cell.risk_count);
            const style = _cellStyle(col, cell.state, palette);
            const mat = new THREE.MeshStandardMaterial({
              color: new THREE.Color(style.fill), roughness: 0.7, metalness: 0.05,
              transparent: style.fillOpacity < 1, opacity: style.fillOpacity,
            });
            const mesh = new THREE.Mesh(boxGeo, mat);
            mesh.scale.set(1.1, h, 1.1);
            const x = baseX + ci * spacing;
            const z = baseZ + ri * spacing;
            mesh.position.set(x, yBase + h / 2, z);
            mesh.userData = { cell, key, baseColor: mat.color.getHex(), baseOpacity: style.fillOpacity };
            scene.add(mesh);

            const edges = new THREE.EdgesGeometry(boxGeo);
            const edgeMat = new THREE.LineBasicMaterial({ color: new THREE.Color(style.edge) });
            const wire = new THREE.LineSegments(edges, edgeMat);
            wire.scale.copy(mesh.scale);
            wire.position.copy(mesh.position);
            scene.add(wire);

            if (cell.state !== "empty") {
              clickable.push(mesh);
              // Risks (RaC) and controls (CaC) counts, on the bar itself —
              // not just reachable by clicking through to the detail panel.
              const countLabel = _makeTextSprite(
                `RaC ${cell.risk_count} · CaC ${cell.verified_control_count}/${cell.mapped_control_count} ⌕`,
                { fontSize: 20, color: ink3Hex }
              );
              countLabel.position.set(x, yBase + h + 0.16, z);
              scene.add(countLabel);
            }
          });
        });

        data.coso_components.forEach((col, ci) => {
          const sprite = _makeTextSprite(_COMPONENT_SHORT[col] || col, { fontSize: 30, color: ink3Hex });
          sprite.position.set(baseX + ci * spacing, yBase + 0.05, baseZ - spacing * 1.5);
          scene.add(sprite);
        });
        data.objective_categories.forEach((row, ri) => {
          const sprite = _makeTextSprite(row, { fontSize: 30, color: inkHex });
          sprite.position.set(baseX - spacing * 1.5, yBase + 0.05, baseZ + ri * spacing);
          scene.add(sprite);
        });

        // Risk-count ruler — bar height within this layer encodes risk_count;
        // without a ruler that's only discoverable via hover/click. Drawn at
        // the corner furthest from both label rows so it doesn't collide.
        const ticks = _niceTicks(maxCount);
        const axisX = baseX + (nCols - 1) * spacing + spacing * 0.75;
        const axisZ = baseZ - spacing * 0.15;
        const poleMat = new THREE.MeshBasicMaterial({ color: palette.ink3 });
        const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, topH, 8), poleMat);
        pole.position.set(axisX, yBase + topH / 2, axisZ);
        scene.add(pole);

        ticks.forEach(v => {
          const h = _barHeight(v);
          const tick = new THREE.Mesh(new THREE.BoxGeometry(spacing * 0.18, 0.02, 0.02), poleMat);
          tick.position.set(axisX + spacing * 0.09, yBase + h, axisZ);
          scene.add(tick);
          const label = _makeTextSprite(String(v), { fontSize: 26, color: ink3Hex });
          label.position.set(axisX + spacing * 0.32, yBase + h, axisZ);
          scene.add(label);
        });

        const title = _makeTextSprite("risks (bar height)", { fontSize: 26, color: ink3Hex });
        title.position.set(axisX, yBase + topH + 0.3, axisZ);
        scene.add(title);
      });

      const raycaster = new THREE.Raycaster();
      const ndc = new THREE.Vector2();
      let hovered = null;

      const camAnim = { active: false, t: 0, dur: 550, fromPos: new THREE.Vector3(), toPos: new THREE.Vector3(), fromTarget: new THREE.Vector3(), toTarget: new THREE.Vector3() };

      function setNdc(evt) {
        const rect = renderer.domElement.getBoundingClientRect();
        ndc.x = ((evt.clientX - rect.left) / rect.width) * 2 - 1;
        ndc.y = -((evt.clientY - rect.top) / rect.height) * 2 + 1;
      }

      function pick(evt) {
        setNdc(evt);
        raycaster.setFromCamera(ndc, camera);
        const hits = raycaster.intersectObjects(clickable, false);
        return hits.length > 0 ? hits[0].object : null;
      }

      function onMove(evt) {
        const hit = pick(evt);
        if (hit !== hovered) {
          // Hover emphasis is opacity + emissive glow, not a hue swap — the
          // color itself is the component identity and must survive hover.
          if (hovered) { hovered.material.opacity = hovered.userData.baseOpacity; hovered.material.emissive?.setHex(0x000000); }
          if (hit) { hit.material.opacity = 1; hit.material.emissive?.setHex(hit.userData.baseColor); hit.material.emissiveIntensity = 0.15; }
          hovered = hit;
          renderer.domElement.style.cursor = hit ? "pointer" : "grab";
          setHoverInfo(hit ? hit.userData.cell : null);
          requestFrame();
        }
      }

      function onClick(evt) {
        const hit = pick(evt);
        if (!hit) return;
        onSelectKeyRef.current(hit.userData.key);
        camAnim.active = true;
        camAnim.t = 0;
        camAnim.fromPos.copy(camera.position);
        camAnim.fromTarget.copy(controls.target);
        const dirToCam = camera.position.clone().sub(hit.position).normalize();
        camAnim.toTarget.copy(hit.position);
        camAnim.toPos.copy(hit.position).add(dirToCam.multiplyScalar(2.6)).setY(Math.max(hit.position.y + 1.4, 1.6));
        requestFrame();
      }

      renderer.domElement.addEventListener("pointermove", onMove);
      renderer.domElement.addEventListener("click", onClick);

      // Render on-demand, not an unconditional 60fps loop forever — a chart
      // that keeps rendering every 16ms while nobody is touching it (which a
      // naive requestAnimationFrame loop does) competes with the rest of the
      // page for the main thread indefinitely. The loop runs only while the
      // camera is actually moving (OrbitControls damping inertia, or the
      // click-to-zoom animation) and stops itself once settled; user
      // interaction (drag/zoom start) wakes it back up.
      let rafScheduled = false;
      function requestFrame() {
        if (disposed || rafScheduled) return;
        rafScheduled = true;
        raf = requestAnimationFrame(animate);
      }
      controls.addEventListener("start", requestFrame);
      controls.addEventListener("change", requestFrame);

      function animate() {
        rafScheduled = false;
        if (disposed) return;
        if (camAnim.active) {
          camAnim.t += 16;
          const p = Math.min(1, camAnim.t / camAnim.dur);
          const ease = 1 - Math.pow(1 - p, 3);
          camera.position.lerpVectors(camAnim.fromPos, camAnim.toPos, ease);
          controls.target.lerpVectors(camAnim.fromTarget, camAnim.toTarget, ease);
          if (p >= 1) camAnim.active = false;
        }
        const stillDamping = controls.update();
        renderer.render(scene, camera);
        if (camAnim.active || stillDamping) requestFrame();
      }
      requestFrame();

      ro = new ResizeObserver(() => {
        const w = mount.clientWidth || width;
        camera.aspect = w / height;
        camera.updateProjectionMatrix();
        renderer.setSize(w, height);
        requestFrame();
      });
      ro.observe(mount);

      return () => {
        disposed = true;
        cancelAnimationFrame(raf);
        ro?.disconnect();
        renderer.domElement.removeEventListener("pointermove", onMove);
        renderer.domElement.removeEventListener("click", onClick);
        controls.dispose();
        renderer.dispose();
        scene.traverse(obj => {
          if (obj.geometry) obj.geometry.dispose();
          if (obj.material) {
            if (obj.material.map) obj.material.map.dispose();
            obj.material.dispose();
          }
        });
      };
    } catch (e) {
      setWebglError(true);
    }
  }, [data]);

  if (webglError) {
    return (
      <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)", padding: 20 }}>
        ⚠ 3D rendering unavailable in this browser — use the Table view below instead.
      </div>
    );
  }

  return (
    <div>
      <div ref={mountRef} style={{ width: "100%", height: 460, borderRadius: 8, overflow: "hidden", border: "1px solid var(--line)" }} />
      <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", marginTop: 6 }}>
        Ground plane = IC-IF component x objective category · bar height = risk count (ruler at back-right corner) ·
        stacked layers = operating unit, the 3rd axis (Consolidated + any segment with its own real risk data get
        full bars; a segment with filed/uploaded revenue but no risk of its own yet is a labeled slab with no bars) ·
        drag to rotate · scroll to zoom · click a bar to zoom in and open detail
        {hoverInfo && ` — ${hoverInfo.objective_category} · ${hoverInfo.coso_component} · ${hoverInfo.entity || "Consolidated"}: `
          + `RaC ${hoverInfo.risk_count} · CaC ${hoverInfo.verified_control_count}/${hoverInfo.mapped_control_count}`}
      </div>
    </div>
  );
}

function CubeCellDetail({ cell, entityLabel = "Consolidated", onClose }) {
  if (!cell) return null;
  const mix = cell.control_env_mix || {};
  return (
    <div style={{
      border: "1px solid var(--line)", borderRadius: 8, padding: 14, marginTop: 12,
      background: "var(--surface-2)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <div className="kicker">{cell.objective_category} · {cell.coso_component} · {entityLabel}</div>
          <div style={{ fontSize: 13, fontWeight: 600, marginTop: 2 }}>
            {cell.risk_count} risk{cell.risk_count === 1 ? "" : "s"} — {_STATE_LABEL[cell.state] || cell.state}
          </div>
        </div>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onClose}>✕</button>
      </div>

      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", marginTop: 10 }}>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>Risks (RaC)</div>
          <div className="mono" style={{ fontSize: 13 }}>{cell.risk_count}</div>
        </div>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>Controls (CaC)</div>
          <div className="mono" style={{ fontSize: 13 }}>
            {cell.mapped_control_count} mapped · {cell.verified_control_count} verified
          </div>
        </div>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>Entity</div>
          <div className="mono" style={{ fontSize: 13 }}>{entityLabel}</div>
        </div>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>Max score</div>
          <div className="mono" style={{ fontSize: 13 }}>{cell.max_score ?? "—"}</div>
        </div>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>Velocity</div>
          <div className="mono" style={{ fontSize: 13 }}>{cell.velocity_label || "—"}</div>
        </div>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>
            Inferred control strength (loop, unverified)
          </div>
          <div className="mono" style={{ fontSize: 11 }}>
            weak {mix.WEAK ?? 0} · adequate {mix.ADEQUATE ?? 0} · strong {mix.STRONG ?? 0}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 10 }}>
        <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)", marginBottom: 4 }}>Risks (RaC)</div>
        <div className="mono" style={{ fontSize: 11, display: "flex", flexWrap: "wrap", gap: 6 }}>
          {(cell.risk_refs || []).map(ref => (
            <span key={ref} style={{
              padding: "2px 6px", borderRadius: 4, border: "1px solid var(--line)", background: "var(--surface)",
            }}>{ref}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

function SegmentStrip({ segments, entities = [] }) {
  if (!segments || segments.length === 0) {
    return (
      <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
        Consolidated — no geography / business-segment breakdown on file for this entity.
      </div>
    );
  }
  const byType = { geography: [], business_segment: [] };
  for (const s of segments) (byType[s.segment_type] || (byType[s.segment_type] = [])).push(s);
  const hasRiskEntities = entities.filter(e => e !== "Consolidated").length > 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {Object.entries(byType).filter(([, rows]) => rows.length > 0).map(([type, rows]) => (
        <div key={type}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)", marginBottom: 4 }}>
            {type === "geography" ? "Geography" : "Business segment"} — revenue mix ({rows[0]?.source || "filed"})
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {rows.map(r => {
              const hasRisk = entities.includes(r.segment_name);
              return (
                <span key={r.segment_name} className="mono" style={{
                  fontSize: 10.5, padding: "3px 8px", borderRadius: 5,
                  border: `1px solid ${hasRisk ? "var(--acc)" : "var(--line)"}`, background: "var(--surface-2)",
                }} title={hasRisk ? "Has real risk-level data in the cube above" : "Revenue context only — no risk assessed for this segment yet"}>
                  {r.segment_name} · {r.revenue_pct != null ? `${r.revenue_pct}%` : "—"}{hasRisk ? " ✓" : ""}
                </span>
              );
            })}
          </div>
        </div>
      ))}
      <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", fontStyle: "italic" }}>
        {hasRiskEntities
          ? "✓ marks a segment with real risk-level data (Concentration/Decline/Divergence) in the cube above — select it via the Entity selector in Table view, or scroll up in 3D."
          : "Not yet joined to individual risks for this entity — no segment cleared a risk threshold on this run."}
      </div>
    </div>
  );
}

const _ERM_STATE_META = {
  evidenced:   { label: "Evidenced",            color: "var(--green-ink)" },
  no_evidence: { label: "No evidence this run", color: "var(--amber-ink)" },
  no_source:   { label: "No source in schema",  color: "var(--ink-4)" },
};

// COSO ERM 2017's view: not a cube (COSO replaced the cube with a helix when
// it published ERM 2017), and not a cross-product of component x principle —
// each principle is nested under its own component, never repeated under
// another. "Evidenced" means a real, persisted artifact was found for the
// ticker's latest run (risk_coverage_cube.build_erm_evidence); "no source in
// schema" means no such artifact exists anywhere in this app, ever — a
// permanent gap, not something this run happens to be missing.
function ErmEvidencePanel({ data }) {
  if (!data) return null;
  return (
    <div>
      <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginBottom: 12, lineHeight: 1.55 }}>
        COSO ERM 2017 has no cube — it replaced the cube with a helix/ribbon diagram. This view asks a different
        question per principle: is this ERM activity <b>evidenced</b>, from a real persisted artifact, for
        {" "}{data.ticker}'s latest run — never inferred.
      </div>
      <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginBottom: 14, display: "flex", gap: 16, flexWrap: "wrap" }}>
        <span>{data.evidenced_count} of {data.total_principles} evidenced</span>
        <span style={{ color: "var(--amber-ink)" }}>{data.no_evidence_count} no evidence this run</span>
        <span style={{ color: "var(--ink-4)" }}>{data.no_source_count} no source in schema</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {(data.components || []).map(comp => (
          <div key={comp.component}>
            <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6 }}>{comp.component}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {comp.principles.map(p => {
                const meta = _ERM_STATE_META[p.state] || {};
                return (
                  <div key={p.number} style={{
                    display: "flex", alignItems: "center", gap: 10, padding: "6px 10px",
                    borderRadius: 6, border: "1px solid var(--line)", background: "var(--surface-2)",
                  }}>
                    <span className="mono" style={{ fontSize: 10, color: "var(--ink-4)", width: 24, flexShrink: 0 }}>P{p.number}</span>
                    <span style={{ fontSize: 12, flex: 1 }}>{p.label}</span>
                    <span className="mono" style={{ fontSize: 10, color: meta.color || "var(--ink-3)", flexShrink: 0 }}>
                      {meta.label || p.state}{p.count != null ? ` (${p.count})` : ""}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const _FRAMEWORKS = [
  { id: "icif_2013", label: "COSO Cube (IC-IF 2013)" },
  { id: "erm_2017",  label: "COSO ERM 2017" },
];

function RiskCoverageCubeScreen({ ticker }) {
  const [framework, setFramework] = useState("icif_2013");
  const [state, setState] = useState({ loading: false, error: null, data: null });
  const [selectedKey, setSelectedKey] = useState(null);
  const [view, setView] = useState("3d");
  const [selectedEntity, setSelectedEntity] = useState("Consolidated");

  useEffect(() => {
    if (!ticker || typeof window === "undefined" || !window.MCP?.getCoverageCube) return;
    let cancelled = false;
    setState({ loading: true, error: null, data: null });
    setSelectedKey(null);
    setSelectedEntity("Consolidated");
    window.MCP.getCoverageCube(ticker, framework)
      .then(data => { if (!cancelled) setState({ loading: false, error: null, data }); })
      .catch(e => { if (!cancelled) setState({ loading: false, error: e.message || "Request failed", data: null }); });
    return () => { cancelled = true; };
  }, [ticker, framework]);

  const data = state.data;
  const isIcif = framework === "icif_2013";
  const cellsByKey = {};
  (data?.cells || []).forEach(c => { cellsByKey[`${c.entity}::${c.objective_category}::${c.coso_component}`] = c; });
  const selected = selectedKey ? cellsByKey[selectedKey] : null;
  // Sums of per-cell risk-control mapping counts — a control mapped to risks
  // in more than one cell counts once per cell it touches, so this is total
  // mapped-control LINKS (RaC-to-CaC edges), not a deduplicated control-register count.
  const totalMappedControlLinks = (data?.cells || []).reduce((sum, c) => sum + (c.mapped_control_count || 0), 0);
  const totalVerifiedControlLinks = (data?.cells || []).reduce((sum, c) => sum + (c.verified_control_count || 0), 0);

  return (
    <div className="panel active" data-screen-label="Risk Coverage Cube">
      <div className="panel-head">
        <div>
          <div className="kicker">Risk Intelligence</div>
          <div className="panel-title mt-8">Risk Coverage Cube</div>
          <div className="panel-sub">
            {isIcif ? (
              <>Where risk assessment (RaC), control assurance (CaC/PaC), and policy enforcement actually meet — and
              where nothing is watching, per the real COSO Cube (Internal Control — Integrated Framework, 2013):
              IC-IF component (driven by the mapped control, not the risk), objective category, and operating unit
              (Consolidated, plus any filed/uploaded segments) — for {ticker || "—"}'s latest run.</>
            ) : (
              <>COSO ERM 2017 conformance: is each of the framework's 20 principles evidenced by a real, persisted
              artifact in this app — for {ticker || "—"}'s latest run. ERM 2017 has no cube (COSO replaced it with a
              helix), so this view isn't one.</>
            )}
          </div>
        </div>
      </div>

      <div style={{ display: "flex", border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden", width: "fit-content", marginBottom: 14 }}>
        {_FRAMEWORKS.map(f => (
          <button key={f.id} type="button"
            onClick={() => setFramework(f.id)}
            className="mono"
            style={{
              fontSize: 10.5, padding: "5px 12px", border: "none", cursor: "pointer",
              background: framework === f.id ? "var(--acc)" : "var(--surface)",
              color: framework === f.id ? "var(--surface)" : "var(--ink-3)",
            }}>
            {f.label}
          </button>
        ))}
      </div>

      {!ticker ? (
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          Set a Company / Ticker in Mission Control first.
        </div>
      ) : state.loading ? (
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>Loading…</div>
      ) : state.error ? (
        <div className="mono" style={{ fontSize: 11, color: "var(--red-ink)" }}>⚠ {state.error}</div>
      ) : !data || data.run_id == null ? (
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
          No risk-loop run found for {ticker} yet — run Assess Risk first.
        </div>
      ) : !isIcif ? (
        <ErmEvidencePanel data={data} />
      ) : (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 10 }}>
            <CubeLegend />
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
                {data.total_risks} risks (RaC)
                {data.unmapped_risk_count > 0 ? ` · ${data.unmapped_risk_count} unmapped` : ""}
                {data.out_of_icif_scope_risk_count > 0 ? ` · ${data.out_of_icif_scope_risk_count} out of IC-IF scope` : ""}
                {" · "}{totalMappedControlLinks} control links (CaC), {totalVerifiedControlLinks} verified
              </div>
              <div style={{ display: "flex", border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
                {["3d", "table"].map(v => (
                  <button key={v} type="button"
                    onClick={() => setView(v)}
                    className="mono"
                    style={{
                      fontSize: 10.5, padding: "4px 12px", border: "none", cursor: "pointer",
                      background: view === v ? "var(--acc)" : "var(--surface)",
                      color: view === v ? "var(--surface)" : "var(--ink-3)",
                    }}>
                    {v === "3d" ? "3D" : "Table"}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {view === "table" && (
            <div style={{ marginBottom: 10 }}>
              <EntitySelector entities={data.entities} selected={selectedEntity} onSelect={setSelectedEntity} />
            </div>
          )}

          {view === "3d" ? (
            <Cube3D data={data} cellsByKey={cellsByKey} onSelectKey={key => setSelectedKey(prev => (prev === key ? null : key))} />
          ) : (
            <CubeGridTable data={data} entity={selectedEntity} cellsByKey={cellsByKey} selectedKey={selectedKey} onSelectKey={setSelectedKey} />
          )}

          <CubeCellDetail cell={selected} entityLabel={selected?.entity || selectedEntity} onClose={() => setSelectedKey(null)} />

          <div style={{ marginTop: 20, paddingTop: 14, borderTop: "1px solid var(--line)" }}>
            <div className="kicker" style={{ marginBottom: 8 }}>Operating unit context</div>
            <SegmentStrip segments={data.segments} entities={data.entities} />
          </div>

          {data.omitted_z_levels?.length > 0 && (
            <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", marginTop: 12, fontStyle: "italic" }}>
              IC-IF's org-structure axis also includes {data.omitted_z_levels.map(o => o.level).join(" and ")} —
              omitted here, not rendered empty: {data.omitted_z_levels.map(o => `${o.level} (${o.reason})`).join("; ")}.
            </div>
          )}
        </>
      )}
    </div>
  );
}

Object.assign(window, { RiskCoverageCubeScreen });
