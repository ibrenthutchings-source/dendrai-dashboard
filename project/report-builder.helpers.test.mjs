// Run: node report-builder.helpers.test.mjs
// Tests the pure helpers in report-builder.jsx (the section before "small UI pieces"),
// loaded by slicing the file so no JSX build is needed.
import fs from "node:fs";
import assert from "node:assert/strict";

const src = fs.readFileSync(new URL("./report-builder.jsx", import.meta.url), "utf8");
const start = src.indexOf("// ---------- pure helpers");
const end = src.indexOf("// ---------- small UI pieces");
assert(start > 0 && end > start, "helper section markers not found");
const h = new Function(`${src.slice(start, end)}; return { rbToApi, rbFromApi, rbOutputColumns, rbFilterValue, rbNewBlock, rbSlug, rbEmptyDefinition };`)();

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

console.log("report-builder helpers: all checks passed");
