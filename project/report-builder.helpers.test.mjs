// Run: node report-builder.helpers.test.mjs
// Tests the pure helpers in report-builder.jsx (the section before "small UI pieces"),
// loaded by slicing the file so no JSX build is needed.
import fs from "node:fs";
import assert from "node:assert/strict";

const src = fs.readFileSync(new URL("./report-builder.jsx", import.meta.url), "utf8");
const start = src.indexOf("// ---------- pure helpers");
const end = src.indexOf("// ---------- small UI pieces");
assert(start > 0 && end > start, "helper section markers not found");
const h = new Function(`${src.slice(start, end)}; return { rbToApi, rbFromApi, rbOutputColumns, rbFilterValue, rbNewBlock, rbSlug, rbEmptyDefinition, rbSplitFields, rbToggleColumn, rbMoveColumn, rbAddValue, rbRemoveValue, rbSetValueFn, rbPrune, rbSuggest, rbDescribeQuery, rbBlockIssues, rbSampleSpec, rbOpsFor, rbAggsFor, rbUniqueAlias };`)();

const datasets = [{ key: "controls", label: "Controls", columns: [
  { name: "framework", label: "Framework", type: "string" }, { name: "category", label: "Category", type: "string" },
  { name: "score", label: "Score", type: "number" }] }];

// filters: parameter, list, between, typed numbers
assert.deepEqual(h.rbFilterValue({ op: "eq", paramName: "fw" }, "string"), { $param: "fw" });
assert.deepEqual(h.rbFilterValue({ op: "in", value: "a, b ,,c" }, "string"), ["a", "b", "c"]);
assert.deepEqual(h.rbFilterValue({ op: "between", value: "1, 5" }, "number"), [1, 5]);
assert.equal(h.rbFilterValue({ op: "gt", value: "7" }, "number"), 7);
assert.equal(h.rbFilterValue({ op: "is_null" }, "string"), undefined);

// aggregating: selected columns are auto-grouped, empty filter rows dropped, value omitted for is_null
const def = { title: "T", parameters: [{ name: "fw", type: "string", default: "", required: true }], blocks: [
  { id: "c1", type: "chart", title: "By cat", chart: { kind: "bar", x: "category", y: ["n"] },
    query: { dataset: "controls", columns: ["category"], group_by: [], aggregates: [{ fn: "count", column: "*", as: "n" }],
      filters: [{ column: "framework", op: "eq", paramName: "fw", value: "" }, { column: "", op: "eq", value: "" }, { column: "category", op: "not_null" }],
      order_by: [{ column: "n", dir: "desc" }], limit: "50" } },
  { id: "t1", type: "table", title: "", query: { dataset: "controls", columns: ["framework"], group_by: [], aggregates: [], filters: [], order_by: [], limit: 500 } },
]};
const api = h.rbToApi(def, datasets);
assert.deepEqual(api.blocks[0].query.group_by, ["category"]);
assert.deepEqual(api.blocks[0].query.filters, [{ column: "framework", op: "eq", value: { $param: "fw" } }, { column: "category", op: "not_null" }]);
assert.equal(api.blocks[0].query.limit, 50);
assert.deepEqual(api.blocks[1].query.group_by, [], "plain select must not get a group_by");
assert.equal(api.parameters[0].default, null, "blank default becomes null");

// round trip: API -> editor -> API is stable
const again = h.rbToApi(h.rbFromApi(api), datasets);
assert.deepEqual(again, api);

// output columns: selected + aggregate alias, with default alias naming
const oc = h.rbOutputColumns({ query: { columns: ["category"], aggregates: [{ fn: "avg", column: "score" }, { fn: "count", column: "*" }] } }, datasets[0]);
assert.deepEqual(oc.map(c => c.name), ["category", "avg_score", "count"]);
assert.equal(oc[1].type, "number");

// new blocks get unique ids; slug rules mirror the server's key rules
assert.equal(h.rbNewBlock("table", [{ id: "table1" }], datasets).id, "table2");
assert.equal(h.rbNewBlock("chart", [], datasets).chart.kind, "bar");
assert.equal(h.rbSlug("  SOX: Controls by Category!! "), "sox-controls-by-category");
assert.equal(h.rbSlug("123 report"), "report");
assert.ok(/^[a-z][a-z0-9_-]{0,63}$/.test(h.rbSlug("My Report 2026")));


// ---------- field picker ----------
const ds = { key: "controls", label: "Controls", columns: [
  { name: "framework", label: "Framework", type: "string", description: "Which framework the control belongs to" },
  { name: "category", label: "Category", type: "string" },
  { name: "score", label: "Score", type: "number" },
  { name: "owner_active", label: "Owner active", type: "bool" },
  { name: "tested_at", label: "Tested at", type: "date" }] };
const emptyQ = () => ({ dataset: "controls", columns: [], filters: [], group_by: [], aggregates: [], order_by: [], limit: 500 });
const blk = (type, q, extra = {}) => ({ id: "b1", type, title: "", query: { ...emptyQ(), ...q }, ...(type === "chart" ? { chart: { kind: "bar", x: "", y: [] } } : {}), ...(type === "kpi" ? { value_column: "" } : {}), ...extra });

// search: label, name and description; numbers are measures, the rest dimensions
let f = h.rbSplitFields(ds.columns, "");
assert.deepEqual(f.dimensions.map(c => c.name), ["framework", "category", "owner_active", "tested_at"]);
assert.deepEqual(f.measures.map(c => c.name), ["score"]);
assert.deepEqual(h.rbSplitFields(ds.columns, "BELONGS").dimensions.map(c => c.name), ["framework"], "matches description, case-insensitively");
assert.deepEqual(h.rbSplitFields(ds.columns, "tested_at").dimensions.map(c => c.name), ["tested_at"], "matches the internal name");
assert.deepEqual(h.rbSplitFields(ds.columns, "zzz"), { dimensions: [], measures: [] });

// toggle / move columns; hiding a column drops a sort that depended on it
let q = h.rbToggleColumn(emptyQ(), "category");
q = h.rbToggleColumn(q, "framework");
assert.deepEqual(q.columns, ["category", "framework"]);
assert.deepEqual(h.rbMoveColumn(q, "framework", -1).columns, ["framework", "category"]);
assert.deepEqual(h.rbMoveColumn(q, "category", -1).columns, ["category", "framework"], "cannot move past the start");
q.order_by = [{ column: "category", dir: "asc" }];
assert.deepEqual(h.rbToggleColumn(q, "category").order_by, []);

// adding values: readable headings, unique internal names, type-aware options
let v = h.rbAddValue(emptyQ(), ds, "count", "*");
assert.deepEqual(v.aggregates[0], { fn: "count", column: "*", as: "row_count", label: "Number of rows" });
v = h.rbAddValue(v, ds, "count", "*");
assert.equal(v.aggregates[1].as, "row_count_2");
v = h.rbAddValue(v, ds, "sum", "score");
assert.equal(v.aggregates[2].label, "Sum of Score");
assert.deepEqual(h.rbAggsFor("number"), ["sum", "avg", "min", "max", "count"]);
assert.deepEqual(h.rbAggsFor("bool"), ["count"]);
assert.ok(!h.rbAggsFor("string").includes("sum"), "no sum of text");
assert.ok(h.rbOpsFor("bool").includes("eq") && !h.rbOpsFor("bool").includes("gt"));

// changing the summary: auto heading/name follow, a renamed heading is kept, sort follows the rename
let w = h.rbAddValue(emptyQ(), ds, "sum", "score");
w.order_by = [{ column: "sum_score", dir: "desc" }];
let w2 = h.rbSetValueFn(w, ds, 0, "avg");
assert.deepEqual([w2.aggregates[0].fn, w2.aggregates[0].label, w2.aggregates[0].as], ["avg", "Average of Score", "avg_score"]);
assert.deepEqual(w2.order_by, [{ column: "avg_score", dir: "desc" }]);
w.aggregates[0].label = "Total risk points";
assert.equal(h.rbSetValueFn(w, ds, 0, "max").aggregates[0].label, "Total risk points", "custom heading is kept");

// removing a value drops its sort; pruning fixes chart/KPI references
const rem = h.rbRemoveValue({ ...w, order_by: [{ column: "sum_score", dir: "asc" }] }, 0);
assert.deepEqual([rem.aggregates, rem.order_by], [[], []]);
const ch = blk("chart", { columns: ["category"], aggregates: [{ fn: "count", column: "*", as: "n", label: "Controls" }] }, { chart: { kind: "bar", x: "category", y: ["n"] } });
const pr = h.rbPrune({ ...ch, query: { ...ch.query, columns: [], aggregates: [] } }, ds);
assert.deepEqual([pr.chart.x, pr.chart.y], ["", []]);
assert.equal(h.rbPrune(blk("kpi", { aggregates: [] }, { value_column: "n" }), ds).value_column, "");

// labels reach output columns and the API; a blank label is not sent
const labelled = blk("table", { columns: ["category"], aggregates: [{ fn: "count", column: "*", as: "n", label: " Controls " }, { fn: "sum", column: "score", as: "s", label: "  " }] });
assert.deepEqual(h.rbOutputColumns(labelled, ds).map(c => c.label), ["Category", "Controls", "s"]);
const apiQ = h.rbToApi({ title: "t", parameters: [], blocks: [labelled] }, [ds]).blocks[0].query;
assert.deepEqual(apiQ.aggregates, [{ fn: "count", column: "*", as: "n", label: "Controls" }, { fn: "sum", column: "score", as: "s" }]);

// list filters keep arrays (values containing commas survive) and round-trip
assert.deepEqual(h.rbFilterValue({ op: "in", value: ["A, Inc", "B"] }, "string"), ["A, Inc", "B"]);
assert.deepEqual(h.rbFilterValue({ op: "between", value: ["2026-01-01", "2026-03-31", "x"] }, "date"), ["2026-01-01", "2026-03-31"]);
const rt = h.rbFromApi({ title: "x", blocks: [{ id: "a", type: "table", query: { dataset: "controls", columns: ["category"], filters: [{ column: "framework", op: "in", value: ["A, Inc", "B"] }] } }] });
assert.deepEqual(rt.blocks[0].query.filters[0].value, ["A, Inc", "B"]);

// plain-English summary
const sumQ = blk("table", { columns: ["category"], aggregates: [{ fn: "count", column: "*", as: "n", label: "Number of controls" }],
  filters: [{ column: "framework", op: "eq", value: "SOX", paramName: "" }, { column: "score", op: "between", value: ["3", "7"], paramName: "" }, { column: "category", op: "is_null" }],
  order_by: [{ column: "n", dir: "desc" }], limit: 50 });
assert.equal(h.rbDescribeQuery(sumQ, ds, []),
  "A table of Category and Number of controls from Controls, one row for each Category, only where Framework is SOX and Score is between 3 and 7 and Category is empty, sorted by Number of controls from high to low, first 50 rows.");
assert.match(h.rbDescribeQuery(blk("table", {}), ds, []), /Nothing selected yet/);
assert.match(h.rbDescribeQuery(blk("kpi", { aggregates: [{ fn: "count", column: "*", as: "n" }], filters: [{ column: "framework", op: "eq", value: "", paramName: "fw" }] }), ds, [{ name: "fw", label: "Framework" }]), /A single number: Number of rows from Controls, only where Framework is the "Framework" parameter, first 500 rows./);

// problems a person can fix
const texts = (b) => h.rbBlockIssues(b, ds).map(i => `${i.level}:${i.text}`);
assert.deepEqual(texts(blk("table", {})), ["error:Pick at least one field to show."]);
assert.deepEqual(texts(blk("table", { columns: ["category"] })), []);
assert.ok(texts(blk("table", { columns: ["category"], filters: [{ column: "framework", op: "eq", value: " ", paramName: "" }] })).includes("error:The filter on Framework has no value."));
assert.ok(texts(blk("table", { columns: ["category"], filters: [{ column: "", op: "eq", value: "" }] })).some(t => t.startsWith("warn:A filter has no field")));
assert.ok(texts(blk("table", { columns: ["category"], filters: [{ column: "score", op: "between", value: ["1", ""], paramName: "" }] })).some(t => t.includes("needs two values")));
assert.deepEqual(texts(blk("table", { columns: ["category"], filters: [{ column: "framework", op: "eq", value: "", paramName: "fw" }, { column: "category", op: "is_null" }] })), [], "parameters and is-empty need no value");
assert.ok(texts(blk("kpi", { aggregates: [{ fn: "count", column: "*", as: "n" }] })).includes("error:Choose which value the KPI shows."));
assert.ok(texts(blk("kpi", { columns: ["category"], aggregates: [{ fn: "count", column: "*", as: "n" }] }, { value_column: "n" })).some(t => t.startsWith("warn:A KPI shows one number")));
assert.deepEqual(texts(blk("kpi", { aggregates: [{ fn: "count", column: "*", as: "n" }] }, { value_column: "n" })), []);
assert.ok(texts(blk("chart", { columns: ["category"] })).some(t => t.startsWith("error:Choose what goes along")));
assert.deepEqual(texts(ch), []);

// suggestions
assert.equal(h.rbSuggest(blk("kpi", { aggregates: [{ fn: "count", column: "*", as: "n" }] }), ds).value_column, "n");
const sg = h.rbSuggest(blk("chart", { columns: ["category"], aggregates: [{ fn: "count", column: "*", as: "n" }] }), ds).chart;
assert.deepEqual([sg.x, sg.y], ["category", ["n"]]);

// sample query: small, never saved, parameter filters use defaults or are reported as skipped
const pb = blk("table", { columns: ["category"], aggregates: [{ fn: "count", column: "*", as: "n" }],
  filters: [{ column: "framework", op: "eq", value: "", paramName: "fw" }, { column: "score", op: "gte", value: "", paramName: "min" }, { column: "category", op: "not_null" }], limit: 500 });
let sp = h.rbSampleSpec(pb, ds, [{ name: "fw", type: "string", default: "SOX" }, { name: "min", type: "number", default: "" }]);
assert.equal(sp.spec.limit, 10);
assert.deepEqual(sp.spec.filters, [{ column: "framework", op: "eq", value: "SOX" }, { column: "category", op: "not_null" }]);
assert.deepEqual(sp.skipped, ["score"]);
assert.deepEqual(sp.spec.group_by, ["category"]);
sp = h.rbSampleSpec(pb, ds, [{ name: "fw", type: "string", default: "SOX" }, { name: "min", type: "number", default: "4" }]);
assert.deepEqual(sp.spec.filters[1], { column: "score", op: "gte", value: 4 });
assert.equal(pb.query.limit, 500, "the block itself is not changed");

// unique alias
assert.equal(h.rbUniqueAlias("n", { columns: ["n"], aggregates: [{ as: "n_2" }] }), "n_3");

console.log("report-builder helpers: all checks passed");
