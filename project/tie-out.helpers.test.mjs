// Run: node tie-out.helpers.test.mjs
// Tests the pure helpers in tie-out.jsx (the section before "small UI pieces").
import fs from "node:fs";
import assert from "node:assert/strict";

const src = fs.readFileSync(new URL("./tie-out.jsx", import.meta.url), "utf8");
const start = src.indexOf("// ---------- pure helpers");
const end = src.indexOf("// ---------- small UI pieces");
assert(start > 0 && end > start, "helper section markers not found");
const h = new Function(`${src.slice(start, end)}; return { toParseRows, toHeaderOf, toBuildRequest, toFilterItems, toEmptyForm };`)();

// CSV: header, quotes, escaped quotes, thousands separator inside quotes, CRLF, blank lines, BOM
assert.deepEqual(h.toParseRows('﻿ref,amount\r\nA,"1,234.50"\r\n\r\n"B ""x""",7\r\n'),
  [{ ref: "A", amount: "1,234.50" }, { ref: 'B "x"', amount: "7" }]);
// tab separated
assert.deepEqual(h.toParseRows("ref\tamt\nA\t5\nB\t6"), [{ ref: "A", amt: "5" }, { ref: "B", amt: "6" }]);
// values stay strings (exact decimals are the server's job)
assert.equal(h.toParseRows("a,b\n1,0.10")[0].b, "0.10");
// embedded newline inside quotes stays in the cell
assert.equal(h.toParseRows('a,b\n"x\ny",1')[0].a, "x\ny");
// errors are plain language
assert.throws(() => h.toParseRows("ref,amt"), /header row and at least one/);
assert.throws(() => h.toParseRows("a,b\n1"), /Row 2 has 1 values but there are 2/);
assert.throws(() => h.toParseRows("a,a\n1,2"), /unique/);
assert.throws(() => h.toParseRows("a,\n1,2"), /header name/);
assert.deepEqual(h.toHeaderOf("ref,amt\nA,1"), ["ref", "amt"]);
assert.deepEqual(h.toHeaderOf("garbage"), []);

// request building
const f = h.toEmptyForm();
Object.assign(f, { objective: "obj", procedure: "proc", conclusion: "done", engagement: " FY26 ", tolAbs: " 0.01 " });
Object.assign(f.left, { label: "GL", key: "ref", amount: "amt", text: "ref,amt\nA,1" });
Object.assign(f.right, { type: "snapshot", label: "Sub", key: "ref", amount: "amt", snapshotId: "7" });
const req = h.toBuildRequest(f);
assert.deepEqual(req.left_source, { type: "inline", label: "GL", rows: [{ ref: "A", amt: "1" }] });
assert.deepEqual(req.right_source, { type: "snapshot", id: 7, label: "Sub" });
assert.deepEqual(req.spec.tolerance, { abs: "0.01" });          // blank pct omitted
assert.equal(req.engagement, "FY26"); assert.equal(req.note, null);
assert.equal(req.spec.left.key, "ref");

// every required field is checked, with a message naming it
const bad = (mut, re) => { const g = structuredClone(f); mut(g); assert.throws(() => h.toBuildRequest(g), re); };
bad(g => { g.objective = " "; }, /Objective is required/);
bad(g => { g.procedure = ""; }, /Procedure is required/);
bad(g => { g.conclusion = ""; }, /Conclusion is required/);
bad(g => { g.left.label = ""; }, /Left label is required/);
bad(g => { g.right.amount = ""; }, /Right amount column is required/);
bad(g => { g.left.key = ""; }, /Left key column is required/);
bad(g => { g.right.snapshotId = ""; }, /Pick a snapshot for the right/);
bad(g => { g.right.snapshotId = "1.5"; }, /Pick a snapshot/);
bad(g => { g.left.text = "ref,amt"; }, /header row/);

// exceptions filter
const items = [{ mark: "T" }, { mark: "R" }, { mark: "D" }, { mark: "L" }, { mark: "X" }];
assert.equal(h.toFilterItems(items, false).length, 5);
assert.deepEqual(h.toFilterItems(items, true).map(i => i.mark), ["D", "L", "X"]);
console.log("tie-out helpers: ok");
