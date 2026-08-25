/* ============================================================
   Risk Coverage Cube — COSO ERM 2017 component (X) x objective
   category (Y) grid, showing how much of the risk universe is
   actually covered and by what. Standalone nav screen, scoped to
   the ticker's latest risk-loop run (risk_coverage_cube.py) —
   spans the whole loop plus RaC/CaC/PaC, so it doesn't belong
   inside a single Assess Risk stage canvas.

   Each cell is one of three states, never collapsed to a binary
   green/red:
     empty              — no risk in the current run falls here
     mapped_unverified  — a risk is here, but no linked control has
                           real, tested/observed assurance evidence
     verified           — a risk is here AND at least one linked
                           control has proven, not just asserted,
                           evidence (last_test_passed or fired
                           recently)

   Rendered as an actual 3D bar landscape (three.js): the ground
   plane is COSO component x objective category, bar height is
   risk_count (sqrt-scaled so one hot cell doesn't flatten the
   rest), color is the three-state above. Clicking a bar dollies
   the camera toward it and opens the same detail panel a plain
   click would. A Table view (the original flat grid) stays
   available as an equivalent, non-WebGL-dependent fallback — see
   dataviz accessibility guidance: color/state is never the only
   way to read a cell.
   ============================================================ */
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

const _CUBE_STATE_META = {
  empty:              { label: "No coverage",       fg: "var(--ink-4)" },
  mapped_unverified:  { label: "Mapped, unverified", fg: "var(--amber-ink)", bg: "var(--amber-soft)", border: "var(--amber)" },
  verified:           { label: "Verified",           fg: "var(--green-ink)", bg: "var(--green-soft, var(--acc-soft))", border: "var(--green-ink)" },
};

const _RAG_META = {
  R: { label: "Red",   color: "var(--red-ink)" },
  A: { label: "Amber", color: "var(--amber-ink)" },
  G: { label: "Green", color: "var(--green-ink)" },
};

const _COMPONENT_SHORT = {
  "Governance & Culture": "Governance",
  "Strategy & Objective-Setting": "Strategy",
  "Performance": "Performance",
  "Review & Revision": "Review",
  "Information, Communication & Reporting": "Info & Reporting",
  "Unmapped": "Unmapped",
};

function CubeLegend() {
  return (
    <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
      {Object.entries(_CUBE_STATE_META).map(([state, meta]) => (
        <div key={state} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{
            width: 12, height: 12, borderRadius: 3, display: "inline-block",
            background: meta.bg || "var(--surface-3)",
            border: `1px solid ${meta.border || "var(--line-2)"}`,
          }} />
          <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>{meta.label}</span>
        </div>
      ))}
    </div>
  );
}

function CubeCell({ cell, onSelect, selected }) {
  const meta = _CUBE_STATE_META[cell.state] || _CUBE_STATE_META.empty;
  const empty = cell.state === "empty";
  return (
    <button
      type="button"
      onClick={() => !empty && onSelect(cell)}
      disabled={empty}
      title={empty ? "No risk in this cell" : `${cell.risk_count} risk(s) — click for detail`}
      style={{
        width: "100%", minHeight: 64, padding: "8px 10px",
        display: "flex", flexDirection: "column", justifyContent: "space-between",
        borderRadius: 6, textAlign: "left", cursor: empty ? "default" : "pointer",
        background: empty ? "var(--surface-2)" : (meta.bg || "var(--surface-2)"),
        border: selected ? "2px solid var(--acc)" : `1px solid ${empty ? "var(--line)" : (meta.border || "var(--line)")}`,
      }}
    >
      {empty ? (
        <span className="mono" style={{ fontSize: 10, color: "var(--ink-4)" }}>—</span>
      ) : (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <span className="mono" style={{ fontSize: 16, fontWeight: 600, color: "var(--ink)" }}>
              {cell.risk_count}
            </span>
            {cell.worst_rag && (
              <span className="mono" style={{
                fontSize: 9, fontWeight: 600, color: _RAG_META[cell.worst_rag]?.color || "var(--ink-3)",
              }}>
                {cell.worst_rag}
              </span>
            )}
          </div>
          <span className="mono" style={{ fontSize: 9.5, color: meta.fg }}>{meta.label}</span>
        </>
      )}
    </button>
  );
}

function CubeGridTable({ data, cellsByKey, selectedKey, onSelectKey }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <div style={{
        display: "grid",
        gridTemplateColumns: `140px repeat(${data.coso_components.length}, minmax(110px, 1fr))`,
        gap: 6, minWidth: 780,
      }}>
        <div />
        {data.coso_components.map(c => (
          <div key={c} className="mono" style={{
            fontSize: 9.5, color: "var(--ink-3)", textAlign: "center", padding: "0 2px",
            display: "flex", alignItems: "flex-end", justifyContent: "center", textWrap: "balance",
          }}>{c}</div>
        ))}

        {data.objective_categories.map(row => (
          <React.Fragment key={row}>
            <div className="mono" style={{
              fontSize: 10.5, color: "var(--ink-2, var(--ink))", fontWeight: 600,
              display: "flex", alignItems: "center",
            }}>{row}</div>
            {data.coso_components.map(col => {
              const key = `${row}::${col}`;
              const cell = cellsByKey[key] || { objective_category: row, coso_component: col, state: "empty", risk_count: 0 };
              return (
                <CubeCell key={key} cell={cell} selected={selectedKey === key}
                  onSelect={() => onSelectKey(prev => (prev === key ? null : key))} />
              );
            })}
          </React.Fragment>
        ))}
      </div>
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
    amber:    _resolveCssColor(v("--amber", "#e0a838"), 0xe0a838),
    amberBg:  _resolveCssColor(v("--amber-soft", "#fbf1de"), 0xfbf1de),
    green:    _resolveCssColor(v("--green-ink", "#2f6b46"), 0x2f6b46),
    greenBg:  _resolveCssColor(v("--acc-soft", "#e5f3ea"), 0xe5f3ea),
    ink:      _resolveCssColor(v("--ink", "#232019"), 0x232019),
    ink3:     _resolveCssColor(v("--ink-3", "#8a8370"), 0x8a8370),
    line:     _resolveCssColor(v("--line", "#e2ded2"), 0xe2ded2),
    accent:   _resolveCssColor(v("--acc", "#2f8f5a"), 0x2f8f5a),
    surface:  _resolveCssColor(v("--surface", "#faf8f3"), 0xfaf8f3),
  };
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

function _stateColor(state, palette) {
  if (state === "verified") return palette.greenBg;
  if (state === "mapped_unverified") return palette.amberBg;
  return palette.empty;
}
function _stateEdgeColor(state, palette) {
  if (state === "verified") return palette.green;
  if (state === "mapped_unverified") return palette.amber;
  return palette.line;
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

      controls = new OrbitControls(camera, renderer.domElement);
      controls.target.set(0, 0.6, 0);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.minDistance = 2.5;
      controls.maxDistance = nCols * 4.5;
      controls.maxPolarAngle = Math.PI / 2.05;
      controls.update();

      scene.add(new THREE.AmbientLight(0xffffff, 0.75));
      const dir = new THREE.DirectionalLight(0xffffff, 0.6);
      dir.position.set(4, 8, 5);
      scene.add(dir);

      const grid = new THREE.GridHelper(Math.max(nCols, nRows) * spacing + 1, 20, palette.line, palette.line);
      grid.position.y = 0;
      scene.add(grid);

      const boxGeo = new THREE.BoxGeometry(1, 1, 1);
      const clickable = [];

      data.objective_categories.forEach((row, ri) => {
        data.coso_components.forEach((col, ci) => {
          const key = `${row}::${col}`;
          const cell = cellsByKey[key] || { objective_category: row, coso_component: col, state: "empty", risk_count: 0 };
          const h = _barHeight(cell.risk_count);
          const mat = new THREE.MeshStandardMaterial({
            color: _stateColor(cell.state, palette), roughness: 0.7, metalness: 0.05,
          });
          const mesh = new THREE.Mesh(boxGeo, mat);
          mesh.scale.set(1.1, h, 1.1);
          const x = baseX + ci * spacing;
          const z = baseZ + ri * spacing;
          mesh.position.set(x, h / 2, z);
          mesh.userData = { cell, key, baseColor: mat.color.getHex(), edgeColor: _stateEdgeColor(cell.state, palette) };
          scene.add(mesh);

          const edges = new THREE.EdgesGeometry(boxGeo);
          const edgeMat = new THREE.LineBasicMaterial({ color: _stateEdgeColor(cell.state, palette) });
          const wire = new THREE.LineSegments(edges, edgeMat);
          wire.scale.copy(mesh.scale);
          wire.position.copy(mesh.position);
          scene.add(wire);

          if (cell.state !== "empty") clickable.push(mesh);
        });
      });

      data.coso_components.forEach((col, ci) => {
        const sprite = _makeTextSprite(_COMPONENT_SHORT[col] || col, { fontSize: 30, color: "#" + palette.ink3.toString(16).padStart(6, "0") });
        sprite.position.set(baseX + ci * spacing, 0.05, baseZ - spacing * 1.5);
        scene.add(sprite);
      });
      data.objective_categories.forEach((row, ri) => {
        const sprite = _makeTextSprite(row, { fontSize: 30, color: "#" + palette.ink.toString(16).padStart(6, "0") });
        sprite.position.set(baseX - spacing * 1.5, 0.05, baseZ + ri * spacing);
        scene.add(sprite);
      });

      // Vertical axis — bar height encodes risk_count, and without a ruler
      // that's only discoverable via hover/click. Drawn at the corner
      // furthest from both label rows so it doesn't collide with them.
      {
        const maxCount = Math.max(1, ...data.cells.map(c => c.risk_count || 0));
        const ticks = _niceTicks(maxCount);
        // Tucked just past the last column/row, close enough to the grid to
        // stay inside the default camera framing (a thin THREE.Line rendered
        // at the very edge of the frustum was easy to lose — a stubby pole +
        // wider tick bars read far more reliably as "an axis").
        const axisX = baseX + (nCols - 1) * spacing + spacing * 0.55;
        const axisZ = baseZ + (nRows - 1) * spacing + spacing * 0.35;
        const topH = _barHeight(maxCount);
        const inkHex = "#" + palette.ink3.toString(16).padStart(6, "0");

        const debugMarker = new THREE.Mesh(new THREE.SphereGeometry(0.3, 16, 16), new THREE.MeshBasicMaterial({ color: 0xff0000 }));
        debugMarker.position.set(axisX, 1, axisZ);
        scene.add(debugMarker);

        const poleMat = new THREE.MeshBasicMaterial({ color: palette.ink3 });
        const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, topH, 8), poleMat);
        pole.position.set(axisX, topH / 2, axisZ);
        scene.add(pole);

        ticks.forEach(v => {
          const h = _barHeight(v);
          const tick = new THREE.Mesh(new THREE.BoxGeometry(spacing * 0.18, 0.02, 0.02), poleMat);
          tick.position.set(axisX + spacing * 0.09, h, axisZ);
          scene.add(tick);
          const label = _makeTextSprite(String(v), { fontSize: 26, color: inkHex });
          label.position.set(axisX + spacing * 0.32, h, axisZ);
          scene.add(label);
        });

        const title = _makeTextSprite("risks (bar height)", { fontSize: 26, color: inkHex });
        title.position.set(axisX, topH + 0.3, axisZ);
        scene.add(title);
      }

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
          if (hovered) hovered.material.color.setHex(hovered.userData.baseColor);
          if (hit) hit.material.color.setHex(palette.accent);
          hovered = hit;
          renderer.domElement.style.cursor = hit ? "pointer" : "grab";
          setHoverInfo(hit ? hit.userData.cell : null);
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
      }

      renderer.domElement.addEventListener("pointermove", onMove);
      renderer.domElement.addEventListener("click", onClick);

      function animate() {
        if (disposed) return;
        if (camAnim.active) {
          camAnim.t += 16;
          const p = Math.min(1, camAnim.t / camAnim.dur);
          const ease = 1 - Math.pow(1 - p, 3);
          camera.position.lerpVectors(camAnim.fromPos, camAnim.toPos, ease);
          controls.target.lerpVectors(camAnim.fromTarget, camAnim.toTarget, ease);
          if (p >= 1) camAnim.active = false;
        }
        controls.update();
        renderer.render(scene, camera);
        raf = requestAnimationFrame(animate);
      }
      animate();

      ro = new ResizeObserver(() => {
        const w = mount.clientWidth || width;
        camera.aspect = w / height;
        camera.updateProjectionMatrix();
        renderer.setSize(w, height);
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
        Ground plane = COSO component x objective category · bar height = risk count (ruler at back-right corner) ·
        drag to rotate · scroll to zoom · click a bar to zoom in and open detail
        {hoverInfo && ` — ${hoverInfo.objective_category} · ${hoverInfo.coso_component}: ${hoverInfo.risk_count} risk${hoverInfo.risk_count === 1 ? "" : "s"}`}
      </div>
    </div>
  );
}

function CubeCellDetail({ cell, onClose }) {
  if (!cell) return null;
  const mix = cell.control_env_mix || {};
  return (
    <div style={{
      border: "1px solid var(--line)", borderRadius: 8, padding: 14, marginTop: 12,
      background: "var(--surface-2)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <div className="kicker">{cell.objective_category} · {cell.coso_component}</div>
          <div style={{ fontSize: 13, fontWeight: 600, marginTop: 2 }}>
            {cell.risk_count} risk{cell.risk_count === 1 ? "" : "s"} — {(_CUBE_STATE_META[cell.state] || {}).label}
          </div>
        </div>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onClose}>✕</button>
      </div>

      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", marginTop: 10 }}>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>Max score</div>
          <div className="mono" style={{ fontSize: 13 }}>{cell.max_score ?? "—"}</div>
        </div>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>Velocity</div>
          <div className="mono" style={{ fontSize: 13 }}>{cell.velocity_label || "—"}</div>
        </div>
        <div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)" }}>Mapped controls</div>
          <div className="mono" style={{ fontSize: 13 }}>
            {cell.verified_control_count}/{cell.mapped_control_count} verified
          </div>
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
        <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)", marginBottom: 4 }}>Risks</div>
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

function SegmentStrip({ segments }) {
  if (!segments || segments.length === 0) {
    return (
      <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
        Consolidated — no geography / business-segment breakdown on file for this entity.
      </div>
    );
  }
  const byType = { geography: [], business_segment: [] };
  for (const s of segments) (byType[s.segment_type] || (byType[s.segment_type] = [])).push(s);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {Object.entries(byType).filter(([, rows]) => rows.length > 0).map(([type, rows]) => (
        <div key={type}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)", marginBottom: 4 }}>
            {type === "geography" ? "Geography" : "Business segment"} — revenue mix ({rows[0]?.source || "filed"})
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {rows.map(r => (
              <span key={r.segment_name} className="mono" style={{
                fontSize: 10.5, padding: "3px 8px", borderRadius: 5,
                border: "1px solid var(--line)", background: "var(--surface-2)",
              }}>
                {r.segment_name} · {r.revenue_pct != null ? `${r.revenue_pct}%` : "—"}
              </span>
            ))}
          </div>
        </div>
      ))}
      <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", fontStyle: "italic" }}>
        Not yet joined to individual risks — shown for entity context only. The grid above is consolidated-only.
      </div>
    </div>
  );
}

function RiskCoverageCubeScreen({ ticker }) {
  const [state, setState] = useState({ loading: false, error: null, data: null });
  const [selectedKey, setSelectedKey] = useState(null);
  const [view, setView] = useState("3d");

  useEffect(() => {
    if (!ticker || typeof window === "undefined" || !window.MCP?.getCoverageCube) return;
    let cancelled = false;
    setState({ loading: true, error: null, data: null });
    window.MCP.getCoverageCube(ticker)
      .then(data => { if (!cancelled) setState({ loading: false, error: null, data }); })
      .catch(e => { if (!cancelled) setState({ loading: false, error: e.message || "Request failed", data: null }); });
    return () => { cancelled = true; };
  }, [ticker]);

  const data = state.data;
  const cellsByKey = {};
  (data?.cells || []).forEach(c => { cellsByKey[`${c.objective_category}::${c.coso_component}`] = c; });
  const selected = selectedKey ? cellsByKey[selectedKey] : null;

  return (
    <div className="panel active" data-screen-label="Risk Coverage Cube">
      <div className="panel-head">
        <div>
          <div className="kicker">Risk Intelligence</div>
          <div className="panel-title mt-8">Risk Coverage Cube</div>
          <div className="panel-sub">
            Where risk assessment (RaC), control assurance (CaC/PaC), and policy enforcement actually meet — and
            where nothing is watching. COSO ERM 2017 component x objective category, for {ticker || "—"}'s latest run.
          </div>
        </div>
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
      ) : (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 10 }}>
            <CubeLegend />
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
                {data.total_risks} risks assessed
                {data.unmapped_risk_count > 0 ? ` · ${data.unmapped_risk_count} in an unmapped category` : ""}
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

          {view === "3d" ? (
            <Cube3D data={data} cellsByKey={cellsByKey} onSelectKey={key => setSelectedKey(prev => (prev === key ? null : key))} />
          ) : (
            <CubeGridTable data={data} cellsByKey={cellsByKey} selectedKey={selectedKey} onSelectKey={setSelectedKey} />
          )}

          <CubeCellDetail cell={selected} onClose={() => setSelectedKey(null)} />

          <div style={{ marginTop: 20, paddingTop: 14, borderTop: "1px solid var(--line)" }}>
            <div className="kicker" style={{ marginBottom: 8 }}>Operating unit context</div>
            <SegmentStrip segments={data.segments} />
          </div>
        </>
      )}
    </div>
  );
}

Object.assign(window, { RiskCoverageCubeScreen });
