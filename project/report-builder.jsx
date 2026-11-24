/* ============================================================
   Report Builder — author, preview, approve and run governed reports.

   Backed by reporting_endpoints.py + reporting_definition_endpoints.py:
     GET  /reporting/datasets                         what can be reported on
     GET  /reporting/definitions/                     latest version of each report
     PUT  /reporting/definitions/{key}                save a NEW draft version
     GET  /reporting/definitions/{key}[/versions/{v}] history / one version
     POST /reporting/definitions/{key}/versions/{v}/approve | retire
     POST /reporting/definitions/{key}/preview | run
     GET  /reporting/definitions/runs[/{id}[/verify|/html|/xlsx]]
     POST /reporting/definitions/runs/{id}/rerun

   Nothing here writes SQL: a block picks columns, filters and aggregates from a
   dataset, and the server compiles and validates it. A saved version never
   changes; editing saves a new draft. Only an approved version can be run, and a
   run is a signed record (verify / rerun / download).
   ============================================================ */

const _RB = "/api/reporting";

async function _rbFetch(path, opts = {}) {
  const res = await fetch(_RB + path, {
    credentials: "include",
    ...opts,
    headers: { ...(opts.body ? { "Content-Type": "application/json" } : {}), ...(opts.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res;
}
const _rbJson = (path, opts) => _rbFetch(path, opts).then(r => r.json());
const _rbBody = (method, body) => ({ method, body: JSON.stringify(body) });

// ---------- pure helpers (no React) ----------

const RB_OPS_BY_TYPE = {
  string:   ["eq", "ne", "contains", "in", "is_null", "not_null"],
  number:   ["eq", "ne", "gt", "gte", "lt", "lte", "in", "between", "is_null", "not_null"],
  date:     ["eq", "ne", "gt", "gte", "lt", "lte", "between", "is_null", "not_null"],
  datetime: ["eq", "ne", "gt", "gte", "lt", "lte", "between", "is_null", "not_null"],
  bool:     ["eq", "ne", "is_null", "not_null"],
};
const RB_AGGS = ["count", "sum", "avg", "min", "max"];

function rbEmptyDefinition() {
  return { title: "", description: "", objective: "", procedure: "", conclusion: "", parameters: [], blocks: [] };
}

function rbNewBlock(type, existing, datasets) {
  let n = existing.length + 1, id;
  do { id = `${type}${n++}`; } while (existing.some(b => b.id === id));
  if (type === "text") return { id, type, content: "" };
  const ds = datasets[0]?.key || "";
  const block = { id, type, title: "", query: { dataset: ds, columns: [], filters: [], group_by: [], aggregates: [], order_by: [], limit: 500 } };
  if (type === "chart") block.chart = { kind: "bar", x: "", y: [] };
  if (type === "kpi") block.value_column = "";
  return block;
}

/** Output columns a block's query will produce: selected columns then aggregate aliases. */
function rbOutputColumns(block, dataset) {
  if (!dataset || !block.query) return [];
  const byName = Object.fromEntries(dataset.columns.map(c => [c.name, c]));
  const out = (block.query.columns || []).filter(c => byName[c]).map(c => ({ name: c, type: byName[c].type, label: byName[c].label }));
  for (const a of block.query.aggregates || []) {
    const alias = a.as || (a.column && a.column !== "*" ? `${a.fn}_${a.column}` : a.fn);
    const src = a.column === "*" ? null : byName[a.column];
    out.push({ name: alias, label: alias, type: ["count", "sum", "avg"].includes(a.fn) ? "number" : (src?.type || "number") });
  }
  return out;
}

/** A filter value as the API wants it: parameters stay {"$param"}, lists split, numbers/bools typed. */
function rbFilterValue(f, colType) {
  if (f.op === "is_null" || f.op === "not_null") return undefined;
  const one = (s) => {
    if (s && typeof s === "object") return s;
    if (colType === "number") { const n = Number(s); return s === "" || Number.isNaN(n) ? s : n; }
    if (colType === "bool") return s === true || s === "true";
    return s;
  };
  if (f.paramName) return { "$param": f.paramName };
  if (f.op === "in") return String(f.value ?? "").split(",").map(s => s.trim()).filter(Boolean).map(one);
  if (f.op === "between") return String(f.value ?? "").split(",").map(s => s.trim()).slice(0, 2).map(one);
  return one(f.value ?? "");
}

/** Editor state (UI-friendly filters) -> API definition. */
function rbToApi(def, datasets) {
  const dsBy = Object.fromEntries(datasets.map(d => [d.key, d]));
  return {
    title: def.title, description: def.description || "", objective: def.objective || "",
    procedure: def.procedure || "", conclusion: def.conclusion || "",
    parameters: def.parameters.map(p => ({ name: p.name, type: p.type, label: p.label || p.name,
      required: p.required !== false, default: p.default === "" ? null : p.default })),
    blocks: def.blocks.map(b => {
      if (b.type === "text") return { id: b.id, type: "text", content: b.content };
      const ds = dsBy[b.query.dataset];
      const colType = (n) => ds?.columns.find(c => c.name === n)?.type || "string";
      // The server needs every plain column to be grouped once anything aggregates.
      const aggregating = b.query.aggregates.length > 0 || b.query.group_by.length > 0;
      const groupBy = aggregating ? [...new Set([...b.query.group_by, ...b.query.columns])] : [];
      const q = {
        dataset: b.query.dataset, columns: b.query.columns, group_by: groupBy,
        aggregates: b.query.aggregates.map(a => ({ fn: a.fn, column: a.column, ...(a.as ? { as: a.as } : {}) })),
        filters: b.query.filters.filter(f => f.column).map(f => {
          const v = rbFilterValue(f, colType(f.column));
          return v === undefined ? { column: f.column, op: f.op } : { column: f.column, op: f.op, value: v };
        }),
        order_by: b.query.order_by.filter(o => o.column), limit: Number(b.query.limit) || 500,
      };
      const out = { id: b.id, type: b.type, title: b.title || "", query: q };
      if (b.type === "kpi") out.value_column = b.value_column;
      if (b.type === "chart") out.chart = b.chart;
      return out;
    }),
  };
}

/** API definition -> editor state (filters get a paramName/value split). */
function rbFromApi(def) {
  const fv = (f) => {
    if (f.value && typeof f.value === "object" && !Array.isArray(f.value) && f.value["$param"]) return { paramName: f.value["$param"], value: "" };
    if (Array.isArray(f.value)) return { paramName: "", value: f.value.join(", ") };
    return { paramName: "", value: f.value ?? "" };
  };
  return {
    title: def.title || "", description: def.description || "", objective: def.objective || "",
    procedure: def.procedure || "", conclusion: def.conclusion || "",
    parameters: (def.parameters || []).map(p => ({ ...p, default: p.default ?? "" })),
    blocks: (def.blocks || []).map(b => b.type === "text" ? { ...b } : ({
      ...b,
      query: {
        limit: 500, ...b.query, columns: b.query.columns || [], group_by: b.query.group_by || [],
        aggregates: (b.query.aggregates || []).map(a => ({ ...a })), order_by: b.query.order_by || [],
        filters: (b.query.filters || []).map(f => ({ column: f.column, op: f.op, ...fv(f) })),
      },
    })),
  };
}

function rbSlug(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").replace(/^[^a-z]+/, "").slice(0, 64);
}

// ---------- small UI pieces ----------

const _rbInput = { fontSize: 12, padding: "5px 8px", border: "1px solid var(--line)", borderRadius: 5, background: "var(--surface)", color: "var(--ink)", width: "100%", boxSizing: "border-box" };
const _rbLabel = { fontSize: 10, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600, marginBottom: 3, display: "block" };
const _rbCard = { border: "1px solid var(--line)", borderRadius: 8, padding: 14, background: "var(--surface)", marginBottom: 12 };
const _rbBtn = (primary) => ({
  fontSize: 12, padding: "5px 12px", borderRadius: 5, cursor: "pointer", fontWeight: 600,
  border: primary ? "1px solid var(--ink)" : "1px solid var(--line)",
  background: primary ? "var(--ink)" : "var(--surface)", color: primary ? "var(--surface)" : "var(--ink)",
});

function RbField({ label, children, style }) {
  return <div style={style}><label style={_rbLabel}>{label}</label>{children}</div>;
}

function RbStatus({ status }) {
  const m = { draft: ["var(--amber-soft)", "var(--amber-ink)"], approved: ["var(--green-soft)", "var(--green-ink)"], retired: ["var(--surface-2)", "var(--ink-3)"] }[status]
    || ["var(--surface-2)", "var(--ink-3)"];
  return <span style={{ fontSize: 9.5, fontWeight: 700, padding: "2px 7px", borderRadius: 999, background: m[0], color: m[1], textTransform: "uppercase" }}>{status}</span>;
}

function RbMulti({ options, value, onChange }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {options.map(o => {
        const on = value.includes(o.name);
        return (
          <label key={o.name} style={{ fontSize: 11.5, display: "flex", gap: 4, alignItems: "center", padding: "2px 8px", borderRadius: 999,
            border: "1px solid var(--line)", background: on ? "var(--surface-2)" : "transparent", cursor: "pointer" }}>
            <input type="checkbox" checked={on} onChange={() => onChange(on ? value.filter(v => v !== o.name) : [...value, o.name])} />
            {o.label || o.name}
          </label>
        );
      })}
    </div>
  );
}

// ---------- query editor ----------

function RbQueryEditor({ block, dataset, datasets, params, onChange }) {
  const q = block.query;
  const setQ = (patch) => onChange({ ...block, query: { ...q, ...patch } });
  const cols = dataset?.columns || [];
  const out = rbOutputColumns(block, dataset);
  const grouped = q.group_by.length > 0 || q.aggregates.length > 0;

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <RbField label="Dataset">
        <select style={_rbInput} value={q.dataset} onChange={e => onChange({ ...block, query: { ...q, dataset: e.target.value, columns: [], filters: [], group_by: [], aggregates: [], order_by: [] } })}>
          {datasets.map(d => <option key={d.key} value={d.key}>{d.label}{d.admin_only ? " (admin only)" : ""}</option>)}
        </select>
        {dataset?.description && <div style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 3 }}>{dataset.description}</div>}
      </RbField>

      <RbField label={grouped ? "Columns (must also be grouped)" : "Columns"}>
        <RbMulti options={cols} value={q.columns} onChange={v => setQ({ columns: v })} />
      </RbField>

      <RbField label="Group by (selected columns are grouped automatically when you aggregate)">
        <RbMulti options={cols} value={q.group_by} onChange={v => setQ({ group_by: v })} />
      </RbField>

      <RbField label="Aggregates">
        {q.aggregates.map((a, i) => (
          <div key={i} style={{ display: "flex", gap: 6, marginBottom: 4 }}>
            <select style={{ ..._rbInput, width: 90 }} value={a.fn} onChange={e => setQ({ aggregates: q.aggregates.map((x, j) => j === i ? { ...x, fn: e.target.value } : x) })}>
              {RB_AGGS.map(f => <option key={f}>{f}</option>)}
            </select>
            <select style={_rbInput} value={a.column} onChange={e => setQ({ aggregates: q.aggregates.map((x, j) => j === i ? { ...x, column: e.target.value } : x) })}>
              <option value="*">* (rows)</option>
              {cols.map(c => <option key={c.name} value={c.name}>{c.label}</option>)}
            </select>
            <input style={{ ..._rbInput, width: 110 }} placeholder="name (as)" value={a.as || ""} onChange={e => setQ({ aggregates: q.aggregates.map((x, j) => j === i ? { ...x, as: e.target.value } : x) })} />
            <button style={_rbBtn(false)} onClick={() => setQ({ aggregates: q.aggregates.filter((_, j) => j !== i) })}>×</button>
          </div>
        ))}
        <button style={_rbBtn(false)} onClick={() => setQ({ aggregates: [...q.aggregates, { fn: "count", column: "*", as: `n${q.aggregates.length + 1}` }] })}>+ Aggregate</button>
      </RbField>

      <RbField label="Filters">
        {q.filters.map((f, i) => {
          const type = cols.find(c => c.name === f.column)?.type || "string";
          const set = (patch) => setQ({ filters: q.filters.map((x, j) => j === i ? { ...x, ...patch } : x) });
          const noValue = f.op === "is_null" || f.op === "not_null";
          return (
            <div key={i} style={{ display: "flex", gap: 6, marginBottom: 4 }}>
              <select style={_rbInput} value={f.column} onChange={e => set({ column: e.target.value, op: "eq" })}>
                <option value="">column…</option>{cols.map(c => <option key={c.name} value={c.name}>{c.label}</option>)}
              </select>
              <select style={{ ..._rbInput, width: 110 }} value={f.op} onChange={e => set({ op: e.target.value })}>
                {(RB_OPS_BY_TYPE[type] || []).map(o => <option key={o}>{o}</option>)}
              </select>
              {!noValue && (
                <select style={{ ..._rbInput, width: 120 }} value={f.paramName || ""} onChange={e => set({ paramName: e.target.value })}>
                  <option value="">value</option>{params.map(p => <option key={p.name} value={p.name}>param: {p.name}</option>)}
                </select>
              )}
              {!noValue && !f.paramName && (
                <input style={_rbInput} value={f.value ?? ""} placeholder={f.op === "in" ? "a, b, c" : f.op === "between" ? "low, high" : "value"} onChange={e => set({ value: e.target.value })} />
              )}
              <button style={_rbBtn(false)} onClick={() => setQ({ filters: q.filters.filter((_, j) => j !== i) })}>×</button>
            </div>
          );
        })}
        <button style={_rbBtn(false)} onClick={() => setQ({ filters: [...q.filters, { column: "", op: "eq", value: "", paramName: "" }] })}>+ Filter</button>
      </RbField>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 90px 100px", gap: 8 }}>
        <RbField label="Order by">
          <select style={_rbInput} value={q.order_by[0]?.column || ""} onChange={e => setQ({ order_by: e.target.value ? [{ column: e.target.value, dir: q.order_by[0]?.dir || "asc" }] : [] })}>
            <option value="">none</option>{out.map(c => <option key={c.name} value={c.name}>{c.label}</option>)}
          </select>
        </RbField>
        <RbField label="Direction">
          <select style={_rbInput} value={q.order_by[0]?.dir || "asc"} disabled={!q.order_by[0]} onChange={e => setQ({ order_by: [{ ...q.order_by[0], dir: e.target.value }] })}>
            <option>asc</option><option>desc</option>
          </select>
        </RbField>
        <RbField label="Row limit">
          <input style={_rbInput} type="number" min="1" value={q.limit} onChange={e => setQ({ limit: e.target.value })} />
        </RbField>
      </div>
    </div>
  );
}

// ---------- block editor ----------

function RbBlockEditor({ block, index, count, datasets, params, onChange, onMove, onRemove }) {
  const dataset = datasets.find(d => d.key === block.query?.dataset);
  const out = rbOutputColumns(block, dataset);
  const numeric = out.filter(c => c.type === "number");
  return (
    <div style={_rbCard}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
        <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "var(--ink-3)" }}>{block.type}</span>
        <input style={{ ..._rbInput, width: 140 }} value={block.id} title="Block id (lowercase letters, digits, _)"
          onChange={e => onChange({ ...block, id: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "") })} />
        {block.type !== "text" && <input style={_rbInput} placeholder="Block title" value={block.title} onChange={e => onChange({ ...block, title: e.target.value })} />}
        <span style={{ flex: 1 }} />
        <button style={_rbBtn(false)} disabled={index === 0} onClick={() => onMove(-1)}>↑</button>
        <button style={_rbBtn(false)} disabled={index === count - 1} onClick={() => onMove(1)}>↓</button>
        <button style={_rbBtn(false)} onClick={onRemove}>Remove</button>
      </div>

      {block.type === "text" ? (
        <textarea style={{ ..._rbInput, height: 80 }} value={block.content} placeholder="Narrative text. Blank line = new paragraph." onChange={e => onChange({ ...block, content: e.target.value })} />
      ) : (
        <>
          <RbQueryEditor block={block} dataset={dataset} datasets={datasets} params={params} onChange={onChange} />
          {block.type === "kpi" && (
            <RbField label="KPI value column" style={{ marginTop: 10 }}>
              <select style={_rbInput} value={block.value_column} onChange={e => onChange({ ...block, value_column: e.target.value })}>
                <option value="">choose…</option>{out.map(c => <option key={c.name} value={c.name}>{c.label}</option>)}
              </select>
              <div style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 3 }}>A KPI query must return exactly one row (use an aggregate with no grouping).</div>
            </RbField>
          )}
          {block.type === "chart" && (
            <div style={{ display: "grid", gridTemplateColumns: "100px 1fr", gap: 8, marginTop: 10 }}>
              <RbField label="Chart type">
                <select style={_rbInput} value={block.chart.kind} onChange={e => onChange({ ...block, chart: { ...block.chart, kind: e.target.value } })}>
                  <option value="bar">bar</option><option value="line">line</option>
                </select>
              </RbField>
              <RbField label="X axis">
                <select style={_rbInput} value={block.chart.x} onChange={e => onChange({ ...block, chart: { ...block.chart, x: e.target.value } })}>
                  <option value="">choose…</option>{out.map(c => <option key={c.name} value={c.name}>{c.label}</option>)}
                </select>
              </RbField>
              <div style={{ gridColumn: "1 / -1" }}>
                <RbField label="Y series (number columns, up to 5)">
                  <RbMulti options={numeric} value={block.chart.y} onChange={v => onChange({ ...block, chart: { ...block.chart, y: v.slice(0, 5) } })} />
                </RbField>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------- editor ----------

function RbEditor({ initial, datasets, onSaved, existingKey, isAdmin }) {
  const [key, setKey] = React.useState(existingKey || "");
  const [def, setDef] = React.useState(() => initial ? rbFromApi(initial) : rbEmptyDefinition());
  const [mode, setMode] = React.useState("visual");
  const [jsonText, setJsonText] = React.useState("");
  const [msg, setMsg] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [preview, setPreview] = React.useState(null);
  const [pvParams, setPvParams] = React.useState({});

  React.useEffect(() => { setDef(initial ? rbFromApi(initial) : rbEmptyDefinition()); setKey(existingKey || ""); setPreview(null); setMsg(null); setErr(null); }, [initial, existingKey]);

  const patch = (p) => setDef(d => ({ ...d, ...p }));
  const setBlock = (i, b) => patch({ blocks: def.blocks.map((x, j) => j === i ? b : x) });
  const moveBlock = (i, dir) => { const a = [...def.blocks]; [a[i], a[i + dir]] = [a[i + dir], a[i]]; patch({ blocks: a }); };

  function apiDefinition() {
    if (mode === "json") return JSON.parse(jsonText);
    return rbToApi(def, datasets);
  }

  async function run(fn) {
    setBusy(true); setErr(null); setMsg(null);
    try { await fn(); } catch (e) { setErr(e.message || String(e)); } finally { setBusy(false); }
  }

  const doPreview = () => run(async () => {
    if (!key) throw new Error("Give the report a key first (lowercase letters, digits, - or _).");
    // Preview runs a SAVED version, so the current edit is saved as a draft first.
    const saved = await _rbJson(`/definitions/${encodeURIComponent(key)}`, _rbBody("PUT", { definition: apiDefinition() }));
    onSaved && onSaved(key, saved, { quiet: true });
    const res = await _rbJson(`/definitions/${encodeURIComponent(key)}/preview`, _rbBody("POST", { version: saved.version, parameters: pvParams }));
    setPreview(res);
    setMsg(`Saved as draft v${saved.version} and previewed.`);
  });

  const doSave = () => run(async () => {
    if (!key) throw new Error("Give the report a key first (lowercase letters, digits, - or _).");
    const saved = await _rbJson(`/definitions/${encodeURIComponent(key)}`, _rbBody("PUT", { definition: apiDefinition() }));
    const missing = saved.missing_narrative || [];
    setMsg(`Saved draft v${saved.version}.` + (missing.length ? ` Before it can be approved, add: ${missing.join(", ")}.` : ""));
    onSaved && onSaved(key, saved);
  });

  function toJson() { try { setJsonText(JSON.stringify(rbToApi(def, datasets), null, 2)); setMode("json"); setErr(null); } catch (e) { setErr(e.message); } }
  function toVisual() {
    try { setDef(rbFromApi(JSON.parse(jsonText))); setMode("visual"); setErr(null); }
    catch (e) { setErr("The JSON is not valid: " + e.message); }
  }

  return (
    <div>
      <div style={{ display: "flex", gap: 8, alignItems: "end", marginBottom: 12, flexWrap: "wrap" }}>
        <RbField label="Report key" style={{ width: 220 }}>
          <input style={_rbInput} value={key} disabled={!!existingKey} placeholder="e.g. sox-controls-by-category"
            onChange={e => setKey(rbSlug(e.target.value))} />
        </RbField>
        <RbField label="Title" style={{ flex: 1, minWidth: 220 }}>
          <input style={_rbInput} value={def.title} onChange={e => { patch({ title: e.target.value }); if (!existingKey && !key) setKey(rbSlug(e.target.value)); }} />
        </RbField>
        <button style={_rbBtn(false)} onClick={mode === "visual" ? toJson : toVisual}>{mode === "visual" ? "Edit as JSON" : "Back to visual"}</button>
      </div>

      {mode === "json" ? (
        <textarea style={{ ..._rbInput, height: 420, fontFamily: "var(--mono, monospace)", fontSize: 11.5 }} value={jsonText} onChange={e => setJsonText(e.target.value)} spellCheck={false} />
      ) : (
        <>
          <div style={_rbCard}>
            <RbField label="Description"><input style={_rbInput} value={def.description} onChange={e => patch({ description: e.target.value })} /></RbField>
            <div style={{ fontSize: 11, color: "var(--ink-3)", margin: "10px 0 6px" }}>
              Objective, procedure and conclusion are required before approval: an independent reviewer must be able to reperform the work from this report alone.
            </div>
            <div style={{ display: "grid", gap: 8 }}>
              <RbField label="Objective"><textarea style={{ ..._rbInput, height: 44 }} value={def.objective} onChange={e => patch({ objective: e.target.value })} /></RbField>
              <RbField label="Procedure"><textarea style={{ ..._rbInput, height: 44 }} value={def.procedure} onChange={e => patch({ procedure: e.target.value })} /></RbField>
              <RbField label="Conclusion"><textarea style={{ ..._rbInput, height: 44 }} value={def.conclusion} onChange={e => patch({ conclusion: e.target.value })} /></RbField>
            </div>
          </div>

          <div style={_rbCard}>
            <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Parameters</div>
            {def.parameters.map((p, i) => {
              const set = (pp) => patch({ parameters: def.parameters.map((x, j) => j === i ? { ...x, ...pp } : x) });
              return (
                <div key={i} style={{ display: "flex", gap: 6, marginBottom: 4 }}>
                  <input style={{ ..._rbInput, width: 130 }} placeholder="name" value={p.name} onChange={e => set({ name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "") })} />
                  <select style={{ ..._rbInput, width: 100 }} value={p.type} onChange={e => set({ type: e.target.value })}>
                    <option>string</option><option>number</option><option>date</option>
                  </select>
                  <input style={_rbInput} placeholder="label" value={p.label || ""} onChange={e => set({ label: e.target.value })} />
                  <input style={_rbInput} placeholder="default" value={p.default ?? ""} onChange={e => set({ default: e.target.value })} />
                  <button style={_rbBtn(false)} onClick={() => patch({ parameters: def.parameters.filter((_, j) => j !== i) })}>×</button>
                </div>
              );
            })}
            <button style={_rbBtn(false)} onClick={() => patch({ parameters: [...def.parameters, { name: `p${def.parameters.length + 1}`, type: "string", label: "", default: "" }] })}>+ Parameter</button>
          </div>

          {def.blocks.map((b, i) => (
            <RbBlockEditor key={i} block={b} index={i} count={def.blocks.length} datasets={datasets} params={def.parameters}
              onChange={nb => setBlock(i, nb)} onMove={d => moveBlock(i, d)} onRemove={() => patch({ blocks: def.blocks.filter((_, j) => j !== i) })} />
          ))}
          <div style={{ display: "flex", gap: 6, marginBottom: 14 }}>
            {["text", "kpi", "table", "chart"].map(t => (
              <button key={t} style={_rbBtn(false)} onClick={() => patch({ blocks: [...def.blocks, rbNewBlock(t, def.blocks, datasets)] })}>+ {t[0].toUpperCase() + t.slice(1)} block</button>
            ))}
          </div>
        </>
      )}

      {def.parameters.length > 0 && mode === "visual" && (
        <div style={{ ..._rbCard, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <div style={{ width: "100%", fontSize: 11, color: "var(--ink-3)" }}>Parameter values for the preview (blank uses the default)</div>
          {def.parameters.map(p => (
            <RbField key={p.name} label={p.label || p.name} style={{ width: 160 }}>
              <input style={_rbInput} type={p.type === "date" ? "date" : "text"} value={pvParams[p.name] ?? ""} onChange={e => setPvParams(v => ({ ...v, [p.name]: e.target.value }))} />
            </RbField>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
        <button style={_rbBtn(true)} disabled={busy} onClick={doSave}>{busy ? "Working…" : "Save as new draft"}</button>
        <button style={_rbBtn(false)} disabled={busy} onClick={doPreview}>Save &amp; preview</button>
        {msg && <span style={{ fontSize: 11.5, color: "var(--green-ink)" }}>{msg}</span>}
      </div>
      {err && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)", marginBottom: 12, whiteSpace: "pre-wrap" }}>{err}</div>}

      {preview && (
        <div style={_rbCard}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Preview (not a record)</div>
          {/* sandbox="" : the rendered report cannot run scripts or reach the app, whatever the data contains */}
          <iframe title="Report preview" sandbox="" srcDoc={preview.html} style={{ width: "100%", height: 560, border: "1px solid var(--line)", borderRadius: 6, background: "#fff" }} />
        </div>
      )}
    </div>
  );
}

// ---------- versions + runs ----------

function RbVersions({ reportKey, isAdmin, onOpenVersion, onChanged }) {
  const [versions, setVersions] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [runForm, setRunForm] = React.useState({ note: "", engagement: "", params: "" });
  const [last, setLast] = React.useState(null);

  const load = React.useCallback(() =>
    _rbJson(`/definitions/${encodeURIComponent(reportKey)}`).then(d => { setVersions(d.versions); setErr(null); }).catch(e => setErr(e.message)),
  [reportKey]);
  React.useEffect(() => { load(); }, [load]);

  const act = async (fn) => { setErr(null); try { await fn(); await load(); onChanged && onChanged(); } catch (e) { setErr(e.message); } };
  const approve = (v) => act(() => _rbJson(`/definitions/${encodeURIComponent(reportKey)}/versions/${v}/approve`, { method: "POST" }));
  const retire = (v) => act(() => _rbJson(`/definitions/${encodeURIComponent(reportKey)}/versions/${v}/retire`, { method: "POST" }));
  const runIt = (v) => act(async () => {
    let parameters = {};
    if (runForm.params.trim()) { try { parameters = JSON.parse(runForm.params); } catch { throw new Error('Parameters must be JSON, e.g. {"fw": "SOX"}'); } }
    const res = await _rbJson(`/definitions/${encodeURIComponent(reportKey)}/run`, _rbBody("POST", { version: v, parameters, note: runForm.note || null, engagement: runForm.engagement || null }));
    setLast(res);
  });

  return (
    <div style={_rbCard}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Versions</div>
      {err && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)", marginBottom: 8 }}>{err}</div>}
      {versions === null ? "Loading…" : versions.map(v => (
        <div key={v.version} style={{ display: "flex", gap: 8, alignItems: "center", padding: "5px 0", borderTop: "1px solid var(--line)", fontSize: 12 }}>
          <b>v{v.version}</b> <RbStatus status={v.status} />
          <span style={{ color: "var(--ink-3)", flex: 1 }}>
            {v.created_by}{v.approved_by ? ` · approved by ${v.approved_by}` : ""}{v.restricted ? " · restricted" : ""}
          </span>
          <button style={_rbBtn(false)} onClick={() => onOpenVersion(v.version)}>Open</button>
          {isAdmin && v.status === "draft" && <button style={_rbBtn(false)} onClick={() => approve(v.version)}>Approve</button>}
          {isAdmin && v.status !== "retired" && <button style={_rbBtn(false)} onClick={() => retire(v.version)}>Retire</button>}
          {v.status === "approved" && <button style={_rbBtn(true)} onClick={() => runIt(v.version)}>Run &amp; record</button>}
        </div>
      ))}
      {versions && versions.some(v => v.status === "approved") && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginTop: 10 }}>
          <RbField label="Engagement (optional)"><input style={_rbInput} value={runForm.engagement} onChange={e => setRunForm(f => ({ ...f, engagement: e.target.value }))} /></RbField>
          <RbField label="Note (optional)"><input style={_rbInput} value={runForm.note} onChange={e => setRunForm(f => ({ ...f, note: e.target.value }))} /></RbField>
          <RbField label="Parameters (JSON, optional)"><input style={_rbInput} placeholder='{"fw": "SOX"}' value={runForm.params} onChange={e => setRunForm(f => ({ ...f, params: e.target.value }))} /></RbField>
        </div>
      )}
      {last && <div style={{ fontSize: 11.5, color: "var(--green-ink)", marginTop: 8 }}>Run #{last.id} recorded and signed. See Runs below.</div>}
    </div>
  );
}

function RbRuns({ reportKey, refreshKey }) {
  const [runs, setRuns] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [checks, setChecks] = React.useState({});
  const [html, setHtml] = React.useState(null);

  const load = React.useCallback(() =>
    _rbJson(`/definitions/runs?definition_key=${encodeURIComponent(reportKey)}`).then(d => setRuns(d.runs)).catch(e => setErr(e.message)),
  [reportKey]);
  React.useEffect(() => { load(); }, [load, refreshKey]);

  const check = async (id, kind) => {
    try {
      const res = await _rbJson(`/definitions/runs/${id}/${kind === "verify" ? "verify" : "rerun"}`, kind === "verify" ? {} : { method: "POST" });
      setChecks(c => ({ ...c, [`${id}:${kind}`]: res }));
    } catch (e) { setChecks(c => ({ ...c, [`${id}:${kind}`]: { error: e.message } })); }
  };
  const openHtml = async (id) => { const r = await _rbFetch(`/definitions/runs/${id}/html`); setHtml(await r.text()); };
  const download = async (id) => {
    try {
      const r = await _rbFetch(`/definitions/runs/${id}/xlsx`);
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a"); a.href = url; a.download = `${reportKey}-run${id}.xlsx`; a.click(); URL.revokeObjectURL(url);
    } catch (e) { setErr(e.message); }
  };

  return (
    <div style={_rbCard}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Runs (signed records)</div>
      {err && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)" }}>{err}</div>}
      {runs === null ? "Loading…" : !runs.length ? <div style={{ fontSize: 12, color: "var(--ink-3)" }}>No runs yet. Approve a version, then Run &amp; record.</div> : runs.map(r => {
        const v = checks[`${r.id}:verify`], rr = checks[`${r.id}:rerun`];
        return (
          <div key={r.id} style={{ padding: "6px 0", borderTop: "1px solid var(--line)", fontSize: 12 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <b>#{r.id}</b> v{r.definition_version}
              <span style={{ color: "var(--ink-3)", flex: 1 }}>{new Date(r.created_at).toLocaleString()} · {r.created_by}{r.engagement ? ` · ${r.engagement}` : ""}</span>
              <button style={_rbBtn(false)} onClick={() => check(r.id, "verify")}>Verify</button>
              <button style={_rbBtn(false)} onClick={() => check(r.id, "rerun")}>Re-run &amp; compare</button>
              <button style={_rbBtn(false)} onClick={() => openHtml(r.id)}>View</button>
              <button style={_rbBtn(false)} onClick={() => download(r.id)}>Excel</button>
            </div>
            {v && <div style={{ fontSize: 11.5, marginTop: 4, color: v.error || !v.intact ? "var(--red-ink)" : "var(--green-ink)" }}>
              {v.error ? v.error : v.intact ? "Verified: signature, row hashes and definition all match." : `Does not verify: ${v.detail}`}</div>}
            {rr && <div style={{ fontSize: 11.5, marginTop: 4, color: rr.error ? "var(--red-ink)" : rr.reproduced ? "var(--green-ink)" : "var(--amber-ink)" }}>
              {rr.error ? rr.error : rr.reproduced ? "A fresh run reproduces this record exactly." :
                "Differs: " + Object.entries(rr.blocks).filter(([, b]) => !b.reproduced).map(([id, b]) => `${id} (${b.reason})`).join("; ")}</div>}
          </div>
        );
      })}
      {html && (
        <div style={{ marginTop: 10 }}>
          <button style={_rbBtn(false)} onClick={() => setHtml(null)}>Close</button>
          <iframe title="Stored run" sandbox="" srcDoc={html} style={{ width: "100%", height: 560, border: "1px solid var(--line)", borderRadius: 6, background: "#fff", marginTop: 6 }} />
        </div>
      )}
    </div>
  );
}

// ---------- screen ----------

function ReportBuilderScreen() {
  const auth = window.useAuth ? window.useAuth() : null;
  const isAdmin = auth?.user?.role === "admin";
  const [datasets, setDatasets] = React.useState(null);
  const [list, setList] = React.useState(null);
  const [selected, setSelected] = React.useState(null);      // { key, version, definition } | "new" | null
  const [error, setError] = React.useState(null);
  const [tick, setTick] = React.useState(0);

  const loadList = React.useCallback(() =>
    _rbJson("/definitions/").then(d => { setList(d.definitions || []); setError(null); }).catch(e => setError(e.message)), []);

  React.useEffect(() => {
    _rbJson("/datasets").then(d => setDatasets(d.datasets || [])).catch(e => setError(e.message));
    loadList();
  }, [loadList]);

  const openVersion = async (key, version) => {
    try {
      const v = await _rbJson(`/definitions/${encodeURIComponent(key)}/versions/${version}`);
      setSelected({ key, version: v.version, definition: v.definition });
    } catch (e) { setError(e.message); }
  };
  // Resolve "latest" from the version history (newest first).
  const openLatest = async (key) => {
    try { const h = await _rbJson(`/definitions/${encodeURIComponent(key)}`); await openVersion(key, h.versions[0].version); }
    catch (e) { setError(e.message); }
  };

  if (datasets === null && !error) return <div className="scope-screen"><Empty>Loading…</Empty></div>;

  return (
    <div className="scope-screen" data-screen-label="Report Builder">
      <div className="panel-head">
        <div>
          <div className="kicker">Audit &amp; Compliance · Report Builder</div>
          <div className="panel-title mt-8">Report Builder</div>
          <div className="panel-sub">
            Build reports from governed datasets, no SQL. Every saved version is permanent; a version must be approved by a
            different admin before it can be run, and each run is a signed record an independent reviewer can verify and reperform.
          </div>
        </div>
      </div>

      {error && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)", marginBottom: 12 }}>{error}</div>}

      <div style={{ display: "grid", gridTemplateColumns: "230px 1fr", gap: 16, alignItems: "start" }}>
        <div>
          <button style={{ ..._rbBtn(true), width: "100%", marginBottom: 10 }} onClick={() => setSelected("new")}>+ New report</button>
          {list === null ? "Loading…" : !list.length ? <div style={{ fontSize: 12, color: "var(--ink-3)" }}>No reports yet.</div> : list.map(d => (
            <div key={d.definition_key} onClick={() => openLatest(d.definition_key)}
              style={{ ..._rbCard, padding: 10, marginBottom: 8, cursor: "pointer", borderColor: selected?.key === d.definition_key ? "var(--ink)" : "var(--line)" }}>
              <div style={{ fontWeight: 600, fontSize: 12.5 }}>{d.title}</div>
              <div style={{ fontSize: 10.5, color: "var(--ink-4)", margin: "2px 0 4px" }}>{d.definition_key}</div>
              <RbStatus status={d.status} /> <span style={{ fontSize: 10.5, color: "var(--ink-3)" }}>v{d.version}{d.approved_version ? ` · v${d.approved_version} approved` : ""}</span>
            </div>
          ))}
        </div>

        <div>
          {!selected ? (
            <div style={{ fontSize: 13, color: "var(--ink-3)", padding: 20 }}>Pick a report, or start a new one.</div>
          ) : (
            <>
              <RbEditor
                initial={selected === "new" ? null : selected.definition}
                existingKey={selected === "new" ? "" : selected.key}
                datasets={datasets || []} isAdmin={isAdmin}
                onSaved={(key, saved, o) => {
                  loadList(); setTick(t => t + 1);
                  // A brand-new report: reload what the server stored so the editor shows the saved version.
                  if (selected === "new" && !(o && o.quiet)) openVersion(key, saved.version);
                }}
              />
              {selected !== "new" && selected.key && (
                <>
                  <RbVersions reportKey={selected.key} isAdmin={isAdmin} key={`v${selected.key}${tick}`}
                    onOpenVersion={(v) => openVersion(selected.key, v)} onChanged={() => { loadList(); setTick(t => t + 1); }} />
                  <RbRuns reportKey={selected.key} refreshKey={tick} />
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ReportBuilderScreen, rbToApi, rbFromApi, rbOutputColumns, rbFilterValue });
