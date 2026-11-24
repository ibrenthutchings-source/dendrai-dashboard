// Run: node report-builder.render.test.mjs
// Renders the report-builder editor components server-side to catch runtime errors (undefined names, bad props)
// that a build does not: every block type, with and without fields, filters, parameters and summaries.
import fs from "node:fs";
import { createRequire } from "node:module";
const root = new URL("./", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const require = createRequire(root + "package.json");
const { transformWithOxc } = require("vite");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

const src = fs.readFileSync(root + "report-builder.jsx", "utf8");
const out = (await transformWithOxc(src, "report-builder.jsx", { lang: "jsx", jsx: { runtime: "classic" } })).code;
globalThis.React = React;
globalThis.window = globalThis;
globalThis.window.useAuth = () => ({ user: { role: "admin" } });
globalThis.Empty = ({ children }) => React.createElement("div", null, children);
globalThis.fetch = async () => { throw new Error("no network in smoke test"); };
const api = new Function("React", out + "\nreturn { RbBlockEditor, RbEditor, RbQueryEditor, RbFilterRow, RbFieldList };")(React);

const ds = { key: "controls", label: "Controls", description: "The control library", columns: [
  { name: "framework", label: "Framework", type: "string", description: "Which framework" },
  { name: "category", label: "Category", type: "string" },
  { name: "score", label: "Score", type: "number" },
  { name: "owner_active", label: "Owner active", type: "bool" },
  { name: "tested_at", label: "Tested at", type: "date" }] };
const q = (o) => ({ dataset: "controls", columns: [], filters: [], group_by: [], aggregates: [], order_by: [], limit: 500, ...o });
const params = [{ name: "fw", type: "string", label: "Framework", default: "SOX" }];
const blocks = [
  { id: "t1", type: "table", title: "T", query: q({}) },
  { id: "t2", type: "table", title: "T2", query: q({ columns: ["category"], aggregates: [{ fn: "count", column: "*", as: "n", label: "Controls" }, { fn: "sum", column: "score", as: "s", label: "Total" }],
    filters: [{ column: "framework", op: "eq", value: "", paramName: "fw" }, { column: "score", op: "between", value: ["1", "5"], paramName: "" }, { column: "owner_active", op: "eq", value: "true", paramName: "" },
      { column: "tested_at", op: "gte", value: "2026-01-01", paramName: "" }, { column: "framework", op: "in", value: ["A", "B"], paramName: "" }, { column: "", op: "eq", value: "" }],
    order_by: [{ column: "n", dir: "desc" }], group_by: ["framework"] }) },
  { id: "k1", type: "kpi", title: "K", value_column: "", query: q({ aggregates: [{ fn: "count", column: "*", as: "n" }] }) },
  { id: "c1", type: "chart", title: "C", chart: { kind: "bar", x: "", y: [] }, query: q({ columns: ["category"], aggregates: [{ fn: "count", column: "*", as: "n" }] }) },
  { id: "x1", type: "text", content: "hello" },
];
let ok = 0;
for (const b of blocks) {
  const html = renderToStaticMarkup(React.createElement(api.RbBlockEditor, { block: b, index: 0, count: 5, datasets: [ds], params, onChange() {}, onMove() {}, onRemove() {} }));
  if (b.type !== "text") {
    for (const needle of ["Search fields", "Fields to slice by", "Numbers to add up", "Summarise", "Filters", "Sort and size", "Show sample rows"])
      if (!html.includes(needle)) throw new Error(`${b.id}: missing "${needle}"`);
  }
  if (b.id === "t1" && !(html.includes("Nothing selected yet") && html.includes("Pick at least one field"))) throw new Error("empty block guidance missing");
  if (b.id === "t2" && !(html.includes("one row for each Category") && html.includes("Low to high") && html.includes("Also grouped by"))) throw new Error("t2 content missing");
  if (b.id === "k1" && !(html.includes("Pick the value for me") && html.includes("Choose which value the KPI shows"))) throw new Error("kpi guidance missing");
  if (b.id === "c1" && !(html.includes("Pick the axes for me"))) throw new Error("chart guidance missing");
  ok++;
}
// whole editor with the JSON toggle and an empty definition
renderToStaticMarkup(React.createElement(api.RbEditor, { initial: null, datasets: [ds], onSaved() {}, existingKey: "", isAdmin: true }));
console.log(`ssr smoke: ${ok} block editors + full editor rendered without error`);
