// Run: node risk-taxonomy.test.mjs
// Parity of risk-engine.js's evidence scorer with agentic-tools/risk_taxonomy.py,
// plus the 8-K -> CEM mapping in mcp-data.js. Loads the IIFE modules into a
// window shim, so no build is needed.
import fs from "node:fs";
import assert from "node:assert/strict";

globalThis.window = globalThis;
for (const f of ["risk-engine.js", "mcp-data.js"]) (0, eval)(fs.readFileSync(new URL(`./${f}`, import.meta.url), "utf8"));
const R = window.RISK_ENGINE, M = window.MCP;

// ── Parity with Python (expected values produced by risk_taxonomy.score_taxonomy
//    for the same inputs — see test_risk_taxonomy.py) ──────────────────────
const ratios = {
  tata: 0.08, dsri: 1.3, cashRatio: 0.03, fcfMargin: -0.2, revGrowth: -0.07,
  grossMargin: 0.30, grossMarginPrev: 0.35, niMargin: -0.02, sgi: 1.6,
  wcToAssets: -0.1, equityToLiabilities: 0.2, lvgi: 1.3, mscoreVal: -1.5, zscoreVal: 0.9,
};
const { risks, notAssessed } = R.buildRisks(ratios);
const got = Object.fromEntries(risks.map(r => [r.id, [r.score, r.base, r.velocity, r.rag]]));
const want = {
  "TX-FRQ": [18.8, 14.8, 3, "R"], "TX-LIQ": [25.0, 25.0, 0, "R"], "TX-PRF": [13.6, 8.8, 3, "A"],
  "TX-REV": [11.2, 4.0, 3, "A"], "TX-CAP": [10.4, 7.2, 2, "A"],
};
assert.deepEqual(got, want, "JS scorer must match the Python scorer");
assert.equal(notAssessed.length, 9);

// ── no evidence -> Not assessed, never a default score ─────────────────────
const empty = R.buildRisks({});
assert.equal(empty.risks.length, 0);
assert.equal(empty.notAssessed.length, R.CATALOG.length);

// ── floor:'R' evidence forces Red ──────────────────────────────────────────
{
  const e = R.ev("SEC 8-K", "bankruptcy", 0.1, { trend: true });
  e.floor = "R";
  const sc = R.scoreTaxonomy([{ "TX-LIQ": [e] }]);
  assert.equal(sc.risks[0].score, 15);
  assert.equal(sc.risks[0].rag, "R");
}

// ── every catalog category has a real impact + audit-response text ─────────
for (const c of R.CATALOG) {
  assert(R.CATEGORY_IMPACT[c.category], `${c.id}: category ${c.category} needs an impact`);
  for (const k of ["obj", "controls", "mapFinding", "mapAction", "mapOwner", "mapSuccessCriteria"]) assert(c[k], `${c.id}.${k}`);
}

// ── profile derived from the final risk set ────────────────────────────────
{
  const base = R.buildProfile("T", null, null, "Generic");
  assert.equal(base.risks.length, 0);
  const p = R.refreshForRisks(base, risks, "T", "Generic");
  assert.equal(p.risks.length, 5);
  assert(p.objectives.length > 0 && p.maps.length === p.objectives.length);
  assert.equal(p.objectives[0].linked_risk, "TX-LIQ");
}

// ── 8-K -> CEM: mapped / review shown, ignored hidden, P1 never capped out ──
{
  const mk = (acc, date, status, severity, routes, extra = {}) => ({
    accession_number: acc, date, item_descriptions: { "4.01": "Changes in Registrant's Certifying Accountant" },
    risk_routing: { status, severity, routes, weight: 1, flags: [], ignored: [], review: [], ...extra },
  });
  const events = [
    mk("A-1", "2026-11-01", "mapped", "P1", [{ item: "4.01", tx: "TX-AUD", points: 3.0, rationale: "Auditor change with adverse indicator(s): resigned" }]),
    mk("A-2", "2026-10-01", "ignored", null, [], { ignored: [{ item: "7.01", reason: "Regulation FD disclosure — informational" }] }),
    mk("A-3", "2026-09-01", "review", null, [], { review: [{ item: "8.01", reason: "Other Events — reviewer to confirm" }] }),
  ];
  const cem = M.map8kToCemEvents({ events });
  assert.deepEqual(cem.map(e => e.mappingStatus).sort(), ["mapped", "review"]);
  const m = cem.find(e => e.mappingStatus === "mapped");
  assert.equal(m.severity, "P1");
  assert.equal(m.routing[0].name, "Audit Oversight & Internal Control");
  assert(cem.find(e => e.mappingStatus === "review").needsReview);

  // 30 P3 filings must not push the lone P1 out of the 20-event display cap
  const many = Array.from({ length: 30 }, (_, i) =>
    mk(`B-${i}`, `2026-0${1 + (i % 9)}-15`, "mapped", "P3", [{ item: "1.01", tx: "TX-STR", points: 0.3, rationale: "New material agreement" }]));
  const capped = M.map8kToCemEvents({ events: [...many, events[0]] });
  assert(capped.length <= 20 && capped.some(e => e.severity === "P1"));
}

// ── taxonomy evidence from the backend: matched by id, not by array index ──
{
  const ev = M.taxonomyEvidenceFromMcp({ risks: [
    { id: "TX-LIQ", assessed: true, evidence: [{ source: "XBRL", label: "x", points: 1, trend: false }] },
    { id: "TX-FRQ", assessed: false, evidence: [] },
    { id: "R-01", assessed: true, evidence: [{ source: "x", label: "legacy", points: 9 }] },
  ] });
  assert.deepEqual(Object.keys(ev), ["TX-LIQ"]);
}

console.log("risk-taxonomy tests passed");
