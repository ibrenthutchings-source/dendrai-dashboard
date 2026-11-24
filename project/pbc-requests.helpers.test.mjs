// Run: node pbc-requests.helpers.test.mjs
// Checks the pure helpers in pbc-requests.jsx (the section before "UI").
import fs from "node:fs";
import assert from "node:assert/strict";

const src = fs.readFileSync(new URL("./pbc-requests.jsx", import.meta.url), "utf8");
const a = src.indexOf("// ---------- pure helpers"), b = src.indexOf("// ---------- UI");
assert(a > 0 && b > a, "helper markers not found");
const h = new Function(`${src.slice(a, b)}; return { prActions, prEngagements, prDueLabel, PR_STATUS_META };`)();

// who may do what
assert.deepEqual(h.prActions({ status: "requested" }, "bob"), { canReceive: true, canReview: false, reviewBlockedReason: null });
assert.equal(h.prActions({ status: "rejected" }, "bob").canReceive, true, "a rejected request can be resubmitted");
assert.equal(h.prActions({ status: "received", submitted_by: "bob" }, "bob").canReview, false);
assert.match(h.prActions({ status: "received", submitted_by: "Bob" }, "bob").reviewBlockedReason, /someone else/, "comparison ignores case");
assert.equal(h.prActions({ status: "received", submitted_by: "bob" }, "carol").canReview, true);
assert.equal(h.prActions({ status: "accepted" }, "carol").canReceive, false);
assert.equal(h.prActions({ status: "accepted" }, "carol").canReview, false);

// engagements: distinct, sorted descending
assert.deepEqual(h.prEngagements([{ engagement: "FY25" }, { engagement: "FY26" }, { engagement: "FY25" }]), ["FY26", "FY25"]);

// due labels
const today = new Date(2026, 10, 23);
assert.equal(h.prDueLabel("2026-11-23", today), "due today");
assert.equal(h.prDueLabel("2026-11-26T00:00:00+00:00", today), "due in 3d");
assert.equal(h.prDueLabel("2026-11-20", today), "3d late");

// every status the server can return has a badge
for (const s of ["requested", "received", "accepted", "rejected", "overdue"]) assert.ok(h.PR_STATUS_META[s], s);
console.log("pbc-requests helpers: all checks passed");
