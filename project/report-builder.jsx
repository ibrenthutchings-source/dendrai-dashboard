/* ============================================================
   Report Builder — author, preview, approve and run governed reports.

   Backed by reporting_endpoints.py + reporting_definition_endpoints.py:
     GET  /reporting/datasets                         what can be reported on
     GET  /reporting/definitions/                     latest version of each report
     PUT  /reporting/definitions/{key}                save a NEW draft version
     GET  /reporting/definitions/{key}[/versions/{v}] history / one version
     POST /reporting/definitions/{key}/versions/{v}/approve | retire
     POST /reporting/definitions/{key}/preview | run
     GET  /reporting/definitions/runs[/{id}[/verify|/html|/xlsx|/pdf|/pptx]]
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
    out.push({ name: alias, label: (a.label && a.label.trim()) || alias, type: ["count", "sum", "avg"].includes(a.fn) ? "number" : (src?.type || "number") });
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
  if (Array.isArray(f.value)) return (f.op === "between" ? f.value.slice(0, 2) : f.value).map(one);
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
        aggregates: b.query.aggregates.map(a => ({ fn: a.fn, column: a.column, ...(a.as ? { as: a.as } : {}),
          ...(a.label && a.label.trim() ? { label: a.label.trim() } : {}) })),
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
    if (Array.isArray(f.value)) return { paramName: "", value: [...f.value] };
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


// ----- field-picker helpers (pure) -----

const RB_OP_LABELS = {
  eq: "is", ne: "is not", contains: "contains", in: "is any of", is_null: "is empty", not_null: "is not empty",
  gt: "is greater than", gte: "is at least", lt: "is less than", lte: "is at most", between: "is between",
};
const RB_AGG_LABELS = { sum: "Sum of", avg: "Average of", min: "Lowest", max: "Highest", count: "Count of" };
const RB_AGGS_BY_TYPE = {
  number: ["sum", "avg", "min", "max", "count"], string: ["count", "min", "max"],
  date: ["min", "max", "count"], datetime: ["min", "max", "count"], bool: ["count"],
};
const RB_TYPE_BADGE = { string: "Aa", number: "123", date: "Date", datetime: "Date", bool: "Y/N" };

const rbOpsFor = (type) => RB_OPS_BY_TYPE[type] || RB_OPS_BY_TYPE.string;
const rbAggsFor = (type) => RB_AGGS_BY_TYPE[type] || ["count"];
/** Numbers are things you add up (measures); everything else is a way to slice (dimensions). */
const rbIsMeasure = (col) => col.type === "number";

/** Fields matching a search box (label, name or description), split into dimensions and measures. */
function rbSplitFields(columns, search) {
  const t = String(search || "").trim().toLowerCase();
  const hit = (c) => !t || [c.label, c.name, c.description].some(x => String(x || "").toLowerCase().includes(t));
  const shown = (columns || []).filter(hit);
  return { dimensions: shown.filter(c => !rbIsMeasure(c)), measures: shown.filter(rbIsMeasure) };
}

function rbUniqueAlias(base, q) {
  const used = new Set([...(q.columns || []), ...(q.aggregates || []).map(a => a.as)].filter(Boolean));
  let alias = base, n = 2;
  while (used.has(alias)) alias = `${base}_${n++}`;
  return alias;
}

/** Show or hide a field as a column. Hiding it also drops any sort that depended on it. */
function rbToggleColumn(q, name) {
  const on = q.columns.includes(name);
  const columns = on ? q.columns.filter(c => c !== name) : [...q.columns, name];
  const order_by = on ? q.order_by.filter(o => o.column !== name) : q.order_by;
  return { ...q, columns, order_by };
}

function rbMoveColumn(q, name, dir) {
  const i = q.columns.indexOf(name), j = i + dir;
  if (i < 0 || j < 0 || j >= q.columns.length) return q;
  const columns = [...q.columns];
  [columns[i], columns[j]] = [columns[j], columns[i]];
  return { ...q, columns };
}

/** Add a summary value: fn over a field ("*" for number of rows) with a readable name. */
function rbAddValue(q, dataset, fn, column) {
  const col = column === "*" ? null : (dataset?.columns || []).find(c => c.name === column);
  const alias = rbUniqueAlias(column === "*" ? "row_count" : `${fn}_${column}`, q);
  const label = column === "*" ? "Number of rows" : `${RB_AGG_LABELS[fn]} ${col?.label || column}`;
  return { ...q, aggregates: [...q.aggregates, { fn, column, as: alias, label }] };
}

function rbRemoveValue(q, index) {
  const gone = q.aggregates[index];
  return { ...q, aggregates: q.aggregates.filter((_, i) => i !== index),
    order_by: q.order_by.filter(o => !gone || o.column !== gone.as) };
}

/** Sort and chart/KPI references that point at output columns that no longer exist are dropped. */
function rbPrune(block, dataset) {
  const out = new Set(rbOutputColumns(block, dataset).map(c => c.name));
  const next = { ...block, query: { ...block.query, order_by: block.query.order_by.filter(o => out.has(o.column)) } };
  if (block.type === "kpi" && block.value_column && !out.has(block.value_column)) next.value_column = "";
  if (block.type === "chart") {
    next.chart = { ...block.chart, x: out.has(block.chart.x) ? block.chart.x : "",
      y: block.chart.y.filter(y => out.has(y)) };
  }
  return next;
}

/** First sensible chart axes / KPI value from what the query returns. */
function rbSuggest(block, dataset) {
  const out = rbOutputColumns(block, dataset);
  const nums = out.filter(c => c.type === "number");
  if (block.type === "kpi") return { value_column: (nums[0] || out[0])?.name || "" };
  const x = out.find(c => c.type !== "number") || out[0];
  return { chart: { ...block.chart, x: x?.name || "", y: nums.filter(c => c.name !== x?.name).slice(0, 1).map(c => c.name) } };
}

function _rbFilterText(f, col, params) {
  const label = col?.label || f.column;
  if (f.op === "is_null" || f.op === "not_null") return `${label} ${RB_OP_LABELS[f.op]}`;
  let v;
  if (f.paramName) v = `the "${(params.find(p => p.name === f.paramName)?.label) || f.paramName}" parameter`;
  else if (Array.isArray(f.value)) v = f.op === "between" ? f.value.join(" and ") : f.value.join(", ");
  else if (f.op === "between") v = String(f.value ?? "").split(",").map(x => x.trim()).join(" and ");
  else v = String(f.value ?? "");
  return `${label} ${RB_OP_LABELS[f.op] || f.op} ${v}`;
}

/** One plain-English sentence describing what the block will show. */
function rbDescribeQuery(block, dataset, params) {
  const q = block.query, byName = Object.fromEntries((dataset?.columns || []).map(c => [c.name, c]));
  const shown = q.columns.map(c => byName[c]?.label || c);
  const vals = q.aggregates.map(a => (a.label && a.label.trim()) || (a.column === "*" ? "Number of rows" : `${RB_AGG_LABELS[a.fn]} ${byName[a.column]?.label || a.column}`));
  const list = (xs) => xs.length < 2 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1];
  const kind = { table: "A table of", kpi: "A single number:", chart: "A chart of" }[block.type] || "";
  const what = [...shown, ...vals];
  if (!what.length) return "Nothing selected yet. Pick fields from the list on the left.";
  let t = `${kind} ${list(what)} from ${dataset?.label || q.dataset}`;
  if (q.aggregates.length && q.columns.length) t += `, one row for each ${list(shown)}`;
  const fl = q.filters.filter(f => f.column).map(f => _rbFilterText(f, byName[f.column], params || []));
  if (fl.length) t += `, only where ${fl.join(" and ")}`;
  const o = q.order_by[0];
  if (o) {
    const oc = rbOutputColumns(block, dataset).find(c => c.name === o.column);
    t += `, sorted by ${oc?.label || o.column} ${o.dir === "desc" ? "from high to low" : "from low to high"}`;
  }
  return t + `, first ${Number(q.limit) || 500} rows.`;
}

/** Problems a person can fix before saving: [{level: "error" | "warn", text}]. */
function rbBlockIssues(block, dataset) {
  const issues = [], q = block.query, out = rbOutputColumns(block, dataset);
  const err = (text) => issues.push({ level: "error", text }), warn = (text) => issues.push({ level: "warn", text });
  if (!q.columns.length && !q.aggregates.length) err("Pick at least one field to show.");
  q.filters.forEach(f => {
    const lbl = (dataset?.columns || []).find(c => c.name === f.column)?.label || f.column;
    if (!f.column) return warn("A filter has no field chosen and will be ignored.");
    if (f.op === "is_null" || f.op === "not_null" || f.paramName) return;
    const v = f.value, empty = Array.isArray(v) ? v.length === 0 || v.every(x => String(x).trim() === "") : String(v ?? "").trim() === "";
    if (empty) return err(`The filter on ${lbl} has no value.`);
    if (f.op === "between" && (Array.isArray(v) ? v.length < 2 || v.some(x => String(x).trim() === "") : String(v).split(",").length < 2)) err(`The "between" filter on ${lbl} needs two values.`);
  });
  if (block.type === "kpi") {
    if (q.columns.length && q.aggregates.length) warn("A KPI shows one number: remove the fields under “Show”, or use a table or chart instead.");
    if (!q.aggregates.length && q.columns.length) warn("A KPI normally summarises rows: add a summary value such as a sum or count.");
    if (!block.value_column) err("Choose which value the KPI shows.");
    else if (!out.some(c => c.name === block.value_column)) err("The KPI value is no longer in the results.");
  }
  if (block.type === "chart") {
    if (!block.chart.x) err("Choose what goes along the chart's X axis.");
    if (!block.chart.y.length) err("Choose at least one number series for the chart.");
    if (q.columns.length && !q.aggregates.length) warn("Charts usually plot summarised values; add a sum or count so each bar is meaningful.");
  }
  return issues;
}

/** The query a sample preview should run: parameter-bound filters use the parameter default
 *  (or are skipped and reported), and the row count is small. Never saves anything. */
function rbSampleSpec(block, dataset, params) {
  const api = rbToApi({ title: "", parameters: params || [], blocks: [{ ...block, type: "table" }] }, dataset ? [dataset] : []);
  const spec = api.blocks[0].query;
  const skipped = [];
  spec.filters = spec.filters.filter(f => {
    if (!(f.value && typeof f.value === "object" && !Array.isArray(f.value) && f.value["$param"])) return true;
    const p = (params || []).find(x => x.name === f.value["$param"]);
    const d = p && p.default !== "" && p.default != null ? p.default : null;
    if (d === null) { skipped.push(f.column); return false; }
    f.value = p.type === "number" && !Number.isNaN(Number(d)) ? Number(d) : d;
    return true;
  });
  spec.limit = 10;
  return { spec, skipped };
}


/** Change how a value is summarised. The heading and internal name follow along only if the
 *  person has not renamed them (they still equal what we generated). */
function rbSetValueFn(q, dataset, index, fn) {
  const a = q.aggregates[index];
  if (!a || a.column === "*") return q;
  const col = (dataset?.columns || []).find(c => c.name === a.column);
  const autoLabel = `${RB_AGG_LABELS[a.fn]} ${col?.label || a.column}`;
  const autoAlias = `${a.fn}_${a.column}`;
  const others = { ...q, aggregates: q.aggregates.filter((_, i) => i !== index) };
  const next = { ...a, fn };
  if (!a.label || a.label === autoLabel) next.label = `${RB_AGG_LABELS[fn]} ${col?.label || a.column}`;
  let renamedAlias = false;
  if (a.as === autoAlias || String(a.as).startsWith(autoAlias + "_")) { next.as = rbUniqueAlias(`${fn}_${a.column}`, others); renamedAlias = true; }
  const aggregates = q.aggregates.map((x, i) => i === index ? next : x);
  // Anything that referred to the old internal name (sort) follows the rename.
  const order_by = renamedAlias ? q.order_by.map(o => o.column === a.as ? { ...o, column: next.as } : o) : q.order_by;
  return { ...q, aggregates, order_by };
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
//
// Layout follows the field-list + wells pattern used by Power BI, Tableau and Looker:
// a searchable list of the dataset's fields on the left (dimensions to slice by, measures to
// add up), and on the right the places a field can go: Show, Summarise, Filters, Sort.
// Nothing here uses database terms: "group by" is implied (summarised values are worked out
// for each combination of the fields you show), and every block has a one-sentence summary,
// plain-language problems, and a sample of the real rows on demand.

const _rbValueCache = new Map();   // "dataset|column" -> Promise<{values, truncated} | null>

function rbLoadValues(ds, col) {
  const k = `${ds}|${col}`;
  if (!_rbValueCache.has(k)) {
    _rbValueCache.set(k, _rbJson(`/datasets/${encodeURIComponent(ds)}/columns/${encodeURIComponent(col)}/values`)
      .then(r => r, () => { _rbValueCache.delete(k); return null; }));
  }
  return _rbValueCache.get(k);
}

function useRbValues(ds, col, wanted) {
  const [res, setRes] = React.useState(null);
  React.useEffect(() => {
    let live = true;
    setRes(null);
    if (wanted && ds && col) rbLoadValues(ds, col).then(r => { if (live) setRes(r); });
    return () => { live = false; };
  }, [ds, col, wanted]);
  return res;
}

const _rbSmall = { fontSize: 10.5, color: "var(--ink-4)" };
const _rbPill = { display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 6px 3px 8px", borderRadius: 999,
  border: "1px solid var(--line)", background: "var(--surface-2)", fontSize: 12 };
const _rbIconBtn = { border: "none", background: "transparent", cursor: "pointer", color: "var(--ink-3)", fontSize: 12, padding: "0 3px" };

function RbBadge({ type }) {
  return <span style={{ fontSize: 9, fontWeight: 700, minWidth: 26, textAlign: "center", padding: "1px 4px", borderRadius: 3,
    background: type === "number" ? "var(--green-soft)" : "var(--surface-2)", color: type === "number" ? "var(--green-ink)" : "var(--ink-3)" }}>{RB_TYPE_BADGE[type] || "?"}</span>;
}

function RbSection({ title, hint, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ ..._rbLabel, marginBottom: 2 }}>{title}</div>
      {hint && <div style={{ ..._rbSmall, marginBottom: 6 }}>{hint}</div>}
      {children}
    </div>
  );
}

// ----- field list -----

function RbFieldList({ dataset, q, onChange }) {
  const [search, setSearch] = React.useState("");
  const { dimensions, measures } = rbSplitFields(dataset?.columns, search);

  const row = (c) => {
    const on = q.columns.includes(c.name);
    return (
      <div key={c.name} title={c.description || c.name}
        style={{ display: "flex", gap: 6, alignItems: "center", padding: "4px 6px", borderRadius: 5, marginBottom: 2,
          background: on ? "var(--surface-2)" : "transparent", border: on ? "1px solid var(--line)" : "1px solid transparent" }}>
        <RbBadge type={c.type} />
        <button onClick={() => onChange(rbToggleColumn(q, c.name))} title={on ? "Click to remove from Show" : "Click to show as a column"}
          style={{ flex: 1, textAlign: "left", border: "none", background: "transparent", cursor: "pointer", fontSize: 12, padding: 0,
            color: "var(--ink)", fontWeight: on ? 700 : 400 }}>
          {on ? "✓ " : ""}{c.label}
          {c.description && <div style={{ ..._rbSmall, fontWeight: 400, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 170 }}>{c.description}</div>}
        </button>
        <select aria-label={`Summarise ${c.label}`} title="Summarise: add a total, average, count, lowest or highest" value=""
          onChange={e => e.target.value && onChange(rbAddValue(q, dataset, e.target.value, c.name))}
          style={{ fontSize: 11, border: "1px solid var(--line)", borderRadius: 4, background: "var(--surface)", color: "var(--ink-3)", width: 38, padding: "1px 0" }}>
          <option value="">{"Σ"}</option>
          {rbAggsFor(c.type).map(fn => <option key={fn} value={fn}>{RB_AGG_LABELS[fn]} {c.label}</option>)}
        </select>
        <button title="Filter by this field" aria-label={`Filter by ${c.label}`}
          onClick={() => onChange({ ...q, filters: [...q.filters, { column: c.name, op: rbOpsFor(c.type)[0], value: "", paramName: "" }] })}
          style={{ ..._rbIconBtn, border: "1px solid var(--line)", borderRadius: 4, fontSize: 10.5, padding: "1px 5px" }}>Filter</button>
      </div>
    );
  };

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 8, background: "var(--surface)", alignSelf: "start" }}>
      <input style={{ ..._rbInput, marginBottom: 8 }} placeholder="Search fields…" value={search} onChange={e => setSearch(e.target.value)} aria-label="Search fields" />
      <div style={{ ..._rbSmall, marginBottom: 6 }}>Click a field to show it. {"Σ"} adds a total, average or count. Filter narrows the rows.</div>
      {!search && (
        <button onClick={() => onChange(rbAddValue(q, dataset, "count", "*"))}
          style={{ ..._rbBtn(false), width: "100%", textAlign: "left", marginBottom: 8 }}>{"Σ"} Number of rows</button>
      )}
      {dimensions.length > 0 && <div style={{ ..._rbLabel, margin: "4px 0" }}>Fields to slice by</div>}
      {dimensions.map(row)}
      {measures.length > 0 && <div style={{ ..._rbLabel, margin: "8px 0 4px" }}>Numbers to add up</div>}
      {measures.map(row)}
      {!dimensions.length && !measures.length && <div style={_rbSmall}>No field matches “{search}”.</div>}
    </div>
  );
}

// ----- filters -----

function RbFilterRow({ f, idBase, dataset, params, onChange, onRemove }) {
  const cols = dataset?.columns || [];
  const col = cols.find(c => c.name === f.column);
  const type = col?.type || "string";
  const noValue = f.op === "is_null" || f.op === "not_null";
  const pickable = !!col && (type === "string" || type === "bool") && !noValue && !f.paramName && ["eq", "ne", "in"].includes(f.op);
  const known = useRbValues(dataset?.key, f.column, pickable && type === "string");
  const set = (patch) => onChange({ ...f, ...patch });
  const inputType = type === "number" ? "number" : type === "date" ? "date" : type === "datetime" ? "datetime-local" : "text";
  const arr = Array.isArray(f.value) ? f.value : String(f.value ?? "").split(",").map(x => x.trim()).filter(Boolean);

  let valueControl = null;
  if (!noValue && !f.paramName) {
    if (f.op === "between") {
      const two = [arr[0] ?? "", arr[1] ?? ""];
      valueControl = (
        <span style={{ display: "flex", gap: 4, alignItems: "center", flex: 1 }}>
          <input style={_rbInput} type={inputType} value={two[0]} onChange={e => set({ value: [e.target.value, two[1]] })} aria-label="From" />
          <span style={_rbSmall}>and</span>
          <input style={_rbInput} type={inputType} value={two[1]} onChange={e => set({ value: [two[0], e.target.value] })} aria-label="To" />
        </span>
      );
    } else if (type === "bool") {
      valueControl = (
        <select style={_rbInput} value={String(f.value ?? "")} onChange={e => set({ value: e.target.value })}>
          <option value="">choose…</option><option value="true">Yes</option><option value="false">No</option>
        </select>
      );
    } else if (f.op === "in" && known && !known.truncated && known.values.length) {
      valueControl = (
        <span style={{ display: "flex", flexWrap: "wrap", gap: 4, flex: 1 }}>
          {known.values.map(v => {
            const on = arr.includes(String(v));
            return (
              <label key={String(v)} style={{ ..._rbPill, cursor: "pointer", background: on ? "var(--ink)" : "var(--surface)", color: on ? "var(--surface)" : "var(--ink)" }}>
                <input type="checkbox" style={{ display: "none" }} checked={on}
                  onChange={() => set({ value: on ? arr.filter(x => x !== String(v)) : [...arr, String(v)] })} />
                {String(v)}
              </label>
            );
          })}
        </span>
      );
    } else if (f.op === "in") {
      valueControl = <input style={_rbInput} value={arr.join(", ")} placeholder="a, b, c" onChange={e => set({ value: e.target.value })} />;
    } else {
      const listId = known && known.values.length && f.op !== "contains" ? `${idBase}-dl` : undefined;
      valueControl = (
        <>
          <input style={_rbInput} type={inputType} list={listId} value={Array.isArray(f.value) ? "" : (f.value ?? "")}
            placeholder={f.op === "contains" ? "text to look for" : "value"} onChange={e => set({ value: e.target.value })} />
          {listId && <datalist id={listId}>{known.values.map(v => <option key={String(v)} value={String(v)} />)}</datalist>}
        </>
      );
    }
  }

  return (
    <div style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "flex-start" }}>
      <select style={{ ..._rbInput, width: 170, flexShrink: 0 }} value={f.column} aria-label="Filter field"
        onChange={e => { const c = cols.find(x => x.name === e.target.value); set({ column: e.target.value, op: rbOpsFor(c?.type)[0], value: "", paramName: "" }); }}>
        <option value="">field…</option>{cols.map(c => <option key={c.name} value={c.name}>{c.label}</option>)}
      </select>
      <select style={{ ..._rbInput, width: 140, flexShrink: 0 }} value={f.op} aria-label="Filter condition"
        onChange={e => set({ op: e.target.value, value: e.target.value === f.op ? f.value : "" })}>
        {rbOpsFor(type).map(o => <option key={o} value={o}>{RB_OP_LABELS[o]}</option>)}
      </select>
      <div style={{ display: "flex", flex: 1, minWidth: 0 }}>{valueControl}</div>
      {!noValue && params.length > 0 && (
        <select style={{ ..._rbInput, width: 150, flexShrink: 0 }} value={f.paramName || ""} aria-label="Value source"
          title="Use a fixed value, or let whoever runs the report choose it" onChange={e => set({ paramName: e.target.value })}>
          <option value="">fixed value</option>
          {params.map(p => <option key={p.name} value={p.name}>asked when run: {p.label || p.name}</option>)}
        </select>
      )}
      <button style={_rbIconBtn} title="Remove this filter" aria-label="Remove filter" onClick={onRemove}>{"×"}</button>
    </div>
  );
}

// ----- sample rows -----

function RbSample({ block, dataset, params, blocked }) {
  const [state, setState] = React.useState({ status: "idle" });
  const { spec, skipped } = React.useMemo(() => rbSampleSpec(block, dataset, params), [block, dataset, params]);
  const key = JSON.stringify(spec);
  const stale = state.status === "ok" && state.key !== key;

  const run = async () => {
    setState({ status: "loading" });
    try {
      const res = await _rbJson("/query", _rbBody("POST", { spec }));
      setState({ status: "ok", key, res, skipped });
    } catch (e) { setState({ status: "error", message: e.message }); }
  };

  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
        <button style={_rbBtn(false)} disabled={blocked || state.status === "loading"} onClick={run}>
          {state.status === "loading" ? "Loading…" : state.status === "ok" ? "Refresh sample" : "Show sample rows"}
        </button>
        <span style={_rbSmall}>The first 10 real rows. Not saved. {skipped.length > 0 && `Filters tied to a parameter without a default are skipped here (${skipped.join(", ")}).`}</span>
        {stale && <span style={{ fontSize: 10.5, fontWeight: 700, color: "var(--amber-ink)" }}>Out of date: refresh to see your latest changes</span>}
      </div>
      {state.status === "error" && <div className="mono" style={{ fontSize: 11.5, color: "var(--red-ink)" }}>{state.message}</div>}
      {state.status === "ok" && (
        <div style={{ overflow: "auto", maxHeight: 280, border: "1px solid var(--line)", borderRadius: 6, opacity: stale ? 0.55 : 1 }}>
          {!state.res.rows.length ? <div style={{ padding: 10, fontSize: 12, color: "var(--ink-3)" }}>No rows match these filters.</div> : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
              <thead><tr>{state.res.columns.map(c => <th key={c.name} style={{ textAlign: c.type === "number" ? "right" : "left", padding: "4px 8px", background: "var(--surface-2)", position: "sticky", top: 0, whiteSpace: "nowrap" }}>{c.label}</th>)}</tr></thead>
              <tbody>{state.res.rows.map((r, i) => (
                <tr key={i}>{state.res.columns.map(c => <td key={c.name} style={{ padding: "3px 8px", borderTop: "1px solid var(--line)", textAlign: c.type === "number" ? "right" : "left", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{String(r[c.name] ?? "")}</td>)}</tr>
              ))}</tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

// ----- the editor -----

function RbQueryEditor({ block, dataset, datasets, params, onChange, idBase }) {
  const q = block.query;
  const cols = dataset?.columns || [];
  const byName = Object.fromEntries(cols.map(c => [c.name, c]));
  const out = rbOutputColumns(block, dataset);
  const setQ = (nq) => onChange(rbPrune({ ...block, query: nq }, dataset));
  const hasValues = q.aggregates.length > 0;
  const blocked = rbBlockIssues(block, dataset).some(i => i.text.startsWith("Pick at least one field"));

  const changeDataset = (key) => {
    const configured = q.columns.length || q.aggregates.length || q.filters.length;
    if (configured && !window.confirm("Changing the dataset clears this block's fields, filters and sort. Continue?")) return;
    onChange(rbPrune({ ...block, value_column: block.type === "kpi" ? "" : block.value_column,
      query: { ...q, dataset: key, columns: [], filters: [], group_by: [], aggregates: [], order_by: [] } },
      datasets.find(d => d.key === key)));
  };

  return (
    <div>
      <RbField label="Dataset" style={{ marginBottom: 10, maxWidth: 420 }}>
        <select style={_rbInput} value={q.dataset} onChange={e => changeDataset(e.target.value)}>
          {datasets.map(d => <option key={d.key} value={d.key}>{d.label}{d.admin_only ? " (admin only)" : ""}</option>)}
        </select>
        {dataset?.description && <div style={{ ..._rbSmall, marginTop: 3 }}>{dataset.description}</div>}
      </RbField>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(220px, 270px) 1fr", gap: 14, alignItems: "start" }}>
        <RbFieldList dataset={dataset} q={q} onChange={setQ} />

        <div>
          <RbSection title="Show" hint={hasValues && q.columns.length ? "The values below are worked out for each combination of these fields." : "Fields shown as columns. Click a field on the left to add it."}>
            {!q.columns.length ? <div style={_rbSmall}>None yet.</div> : (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {q.columns.map((name, i) => (
                  <span key={name} style={_rbPill}>
                    <RbBadge type={byName[name]?.type} />{byName[name]?.label || name}
                    <button style={_rbIconBtn} disabled={i === 0} title="Move earlier" aria-label={`Move ${name} earlier`} onClick={() => setQ(rbMoveColumn(q, name, -1))}>{"←"}</button>
                    <button style={_rbIconBtn} disabled={i === q.columns.length - 1} title="Move later" aria-label={`Move ${name} later`} onClick={() => setQ(rbMoveColumn(q, name, 1))}>{"→"}</button>
                    <button style={_rbIconBtn} title="Remove" aria-label={`Remove ${name}`} onClick={() => setQ(rbToggleColumn(q, name))}>{"×"}</button>
                  </span>
                ))}
              </div>
            )}
            {q.group_by.filter(g => !q.columns.includes(g)).length > 0 && (
              <div style={{ ..._rbSmall, marginTop: 4 }}>Also grouped by (not shown): {q.group_by.filter(g => !q.columns.includes(g)).map(g => byName[g]?.label || g).join(", ")}
                {" "}<button style={{ ..._rbIconBtn, textDecoration: "underline" }} onClick={() => setQ({ ...q, group_by: q.group_by.filter(g => q.columns.includes(g)) })}>clear</button></div>
            )}
          </RbSection>

          <RbSection title="Summarise" hint="Totals, averages, counts, lowest or highest. Use the Σ menu beside a field, or Number of rows.">
            {!q.aggregates.length ? <div style={_rbSmall}>None yet.</div> : q.aggregates.map((a, i) => {
              const c = byName[a.column];
              const set = (patch) => setQ({ ...q, aggregates: q.aggregates.map((x, j) => j === i ? { ...x, ...patch } : x) });
              return (
                <div key={i} style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 4 }}>
                  {a.column === "*" ? <span style={{ fontSize: 12, width: 150 }}>Count of rows</span> : (
                    <>
                      <select style={{ ..._rbInput, width: 130 }} value={a.fn} aria-label="How to summarise"
                        onChange={e => { const r = rbSetValueFn(q, dataset, i, e.target.value); setQ(r); }}>
                        {rbAggsFor(c?.type).map(fn => <option key={fn} value={fn}>{RB_AGG_LABELS[fn]}</option>)}
                      </select>
                      <span style={{ fontSize: 12, minWidth: 90 }}>{c?.label || a.column}</span>
                    </>
                  )}
                  <span style={_rbSmall}>shown as</span>
                  <input style={{ ..._rbInput, flex: 1, maxWidth: 260 }} value={a.label ?? ""} aria-label="Column heading"
                    placeholder={a.as} onChange={e => set({ label: e.target.value })} />
                  <button style={_rbIconBtn} title="Remove" aria-label="Remove summary" onClick={() => setQ(rbRemoveValue(q, i))}>{"×"}</button>
                </div>
              );
            })}
          </RbSection>

          <RbSection title="Filters" hint="Only rows that meet every filter are used.">
            {q.filters.map((f, i) => (
              <RbFilterRow key={i} f={f} idBase={`${idBase}-f${i}`} dataset={dataset} params={params}
                onChange={nf => setQ({ ...q, filters: q.filters.map((x, j) => j === i ? nf : x) })}
                onRemove={() => setQ({ ...q, filters: q.filters.filter((_, j) => j !== i) })} />
            ))}
            <button style={_rbBtn(false)} onClick={() => setQ({ ...q, filters: [...q.filters, { column: "", op: "eq", value: "", paramName: "" }] })}>+ Add filter</button>
          </RbSection>

          <RbSection title="Sort and size">
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <span style={_rbSmall}>Sort by</span>
              <select style={{ ..._rbInput, width: 200 }} value={q.order_by[0]?.column || ""} aria-label="Sort by"
                onChange={e => setQ({ ...q, order_by: e.target.value ? [{ column: e.target.value, dir: q.order_by[0]?.dir || "asc" }] : [] })}>
                <option value="">no particular order</option>{out.map(c => <option key={c.name} value={c.name}>{c.label}</option>)}
              </select>
              <select style={{ ..._rbInput, width: 140 }} value={q.order_by[0]?.dir || "asc"} disabled={!q.order_by[0]} aria-label="Sort direction"
                onChange={e => setQ({ ...q, order_by: [{ ...q.order_by[0], dir: e.target.value }] })}>
                <option value="asc">Low to high</option><option value="desc">High to low</option>
              </select>
              <span style={_rbSmall}>Show the first</span>
              <input style={{ ..._rbInput, width: 80 }} type="number" min="1" value={q.limit} aria-label="Row limit" onChange={e => setQ({ ...q, limit: e.target.value })} />
              <span style={_rbSmall}>rows</span>
            </div>
          </RbSection>

          <RbSection title="Sample">
            <RbSample block={block} dataset={dataset} params={params} blocked={blocked} />
          </RbSection>
        </div>
      </div>
    </div>
  );
}

// ---------- block editor ----------

function RbBlockEditor({ block, index, count, datasets, params, onChange, onMove, onRemove }) {
  const dataset = datasets.find(d => d.key === block.query?.dataset);
  const out = rbOutputColumns(block, dataset);
  const numeric = out.filter(c => c.type === "number");
  const issues = block.type === "text" ? [] : rbBlockIssues(block, dataset);
  const suggestable = (block.type === "kpi" && !block.value_column) || (block.type === "chart" && (!block.chart.x || !block.chart.y.length));
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
          <div style={{ fontSize: 12.5, padding: "8px 10px", background: "var(--surface-2)", borderRadius: 6, marginBottom: 10 }}>
            {rbDescribeQuery(block, dataset, params)}
          </div>
          {issues.map((i, k) => (
            <div key={k} role={i.level === "error" ? "alert" : undefined} style={{ fontSize: 11.5, marginBottom: 4, color: i.level === "error" ? "var(--red-ink)" : "var(--amber-ink)" }}>
              {i.level === "error" ? "\u26D4 " : "\u26A0 "}{i.text}
            </div>
          ))}
          <div style={{ height: 6 }} />
          <RbQueryEditor block={block} dataset={dataset} datasets={datasets} params={params} onChange={onChange} idBase={`rb-${block.id}`} />
          {suggestable && out.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <button style={_rbBtn(false)} onClick={() => onChange({ ...block, ...rbSuggest(block, dataset) })}>
                {block.type === "kpi" ? "Pick the value for me" : "Pick the axes for me"}
              </button>
            </div>
          )}
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
  const download = async (id, ext = "xlsx") => {
    try {
      const r = await _rbFetch(`/definitions/runs/${id}/${ext}`);
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a"); a.href = url; a.download = `${reportKey}-run${id}.${ext}`; a.click(); URL.revokeObjectURL(url);
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
              <button style={_rbBtn(false)} onClick={() => download(r.id, "xlsx")}>Excel</button>
              <button style={_rbBtn(false)} onClick={() => download(r.id, "pdf")}>PDF</button>
              <button style={_rbBtn(false)} onClick={() => download(r.id, "pptx")}>PowerPoint</button>
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
