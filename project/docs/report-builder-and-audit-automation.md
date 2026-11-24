# Intelligenza: Report Builder and Audit Automation

Analysis and recommendations. Nothing here is built yet. Written 2026-11-23.

**How this was produced:** a read of the existing modules' code and docstrings (names are cited so you can check). I did not exercise each module, so "exists" means "present and intended for this purpose", not "proven complete". Items marked **assumption** are my reading of your request; correct them and the recommendations shift.

**Decisions I needed from you** were answered on 2026-11-23 and are recorded in the next section. The original questions are at the end.

---

## Decisions and build status (2026-11-23)

| Question | Decision | Consequence |
|---|---|---|
| Users | All, especially risks and controls that support external auditors (financial, SOX, ITGC). Technical frameworks (ISO, SOC 2) are audited by third parties. | External auditors need restricted, read-mostly accounts. Evidence and reports must stand on their own for someone outside the company. **Not built yet** (needs an external-auditor role and per-engagement scoping). |
| "Operating systems" | Other products and platforms. | Part 1 stays a reusable package (API, MCP tools, embeddable viewer). |
| Systems | Oracle Fusion, NetSuite, SAP S/4 and HANA. | Connectors exist for Oracle Fusion, NetSuite and SAP HANA (`connector_poller._ADAPTERS`). **S/4-specific coverage is not verified**; check it before promising automatic collection for S/4. |
| Standard | Documentation must let a knowledgeable, trained auditor with no involvement in the audit **reperform the work from the documentation alone**. | See "The reperformance standard" below. It is a design requirement for everything built here. |
| Build approach | Hybrid for reporting. | As recommended. |
| Order | Report subsystem and document requests first. | Phase 0 of reporting and the first Request-as-Code increment are built (below). |

### The reperformance standard, as concrete requirements

Everything here is held to one test: could a stranger who knows the craft redo this from the record alone? That means every result records the following.

| Must be recorded | How it is met today |
|---|---|
| **What was done**, exactly | Report queries: normalised spec plus the exact SQL text and bound parameters (`manifest.spec`, `manifest.sql`, `manifest.params`). PBC requests: a frozen copy of the requirement (`requirement_snapshot`, `requirement_hash`). |
| **On what data, when** | `manifest.dataset` (key, version, source tables) and `manifest.as_of`. |
| **What came out, provably** | `manifest.result_sha256` over the canonical result; the audit log records the same hash for each run. |
| **Which checks ran and what they found** | PBC `checks` plus `checks_version`; findings are stored, never recomputed, so later rule changes cannot rewrite history. |
| **Who did and who reviewed** | `submitted_by`, `reviewed_by`, `reviewed_at`, `review_note`; reviewer must differ from submitter and from whoever logged the evidence. |
| **Why a decision was made** | A rejection requires a written note. |
| **How a sample was chosen** | `sample_selection_tool.py` already records method, seed, interval and start point. |

**Known gaps against the standard (not yet met):**
1. ~~**Result rows are not persisted.**~~ **Closed (code complete, SQL untested on real Postgres).** `POST /reporting/snapshots` stores the manifest, who, why, engagement and the result rows as exact text, signed with the tenant's HMAC key; a trigger makes the table append-only; `/verify` reports row-hash and signature failures separately; `/rerun` shows whether a fresh run reproduces the snapshot or the data or dataset definition has moved. Remaining limits: a key rotation invalidates old signatures unless old keys are kept, and results are capped at the dataset's row limit (a truncated snapshot says so in its manifest).
2. **Completeness and accuracy of source data (IPE)** is not yet captured: nothing records control totals or row counts reconciled to the source system. Needed for any dataset pulled from Oracle, NetSuite or SAP.
3. **No procedure narrative.** Reperformance also needs a plain-language statement of the objective, procedure and conclusion per test. The report and tie-out templates should require one.
4. **Tickmark legend and tolerance** belong in every tie-out and need a defined place in the reconciliation engine's output.

### What is built (all uncommitted, tests passing, SQL not yet run against a real Postgres)

**Reporting phase 0**
- `reporting_catalog.py`: dataset registry and query compiler (identifiers only from the registry, all values bound, allow-listed operators, row cap, injection attempts tested) and the reperformance manifest. Starter datasets: `controls`, `risk_scores`, `pbc_evidence`, and `audit_log` (admin only).
- `reporting_endpoints.py`: `GET /reporting/datasets`, `GET /reporting/datasets/{key}`, `POST /reporting/query`. Read-only transaction, 15 s statement timeout, every run audit-logged with its result hash.
- Dev proxy entry `/api/reporting`. Tests: `test_reporting.py`.

**Request-as-Code, first increment**
- `pbc_request_tool.py` (pure rules), `pbc_request_endpoints.py`, tables `observability.evidence_requirements` and `observability.pbc_requests`.
- Declare what evidence a control needs; generate an engagement's request list (idempotent); link logged evidence, which runs deterministic checks (wrong control, period not covered, signature, freshness) against the **frozen** requirement; accept or reject with enforced reviewer independence; status board with derived "overdue". All audit-logged. Tests: `test_pbc_requests.py`.
- **Also built:** the PBC Requests screen (`pbc-requests.jsx`) and reminders/escalation (`pbc_reminder_sweep.py`, webhook, single-tenant only for now).
- **Not built yet:** automatic collection from Oracle/NetSuite/SAP (a requirement's `source_system` is recorded, not acted on), per-tenant webhooks and per-owner notification, and the external-auditor role.

**Also built:** signed immutable report snapshots (`reporting_snapshot.py`, table `observability.report_snapshots`, tests `test_report_snapshots.py`).

**Also built:** Report-as-Code (`reporting_definitions.py`, `reporting_definition_endpoints.py`, tables `report_definition_versions` and `report_runs`, tests `test_report_definitions.py`): versioned definitions, draft/approve/retire with author != approver and a required objective/procedure/conclusion (closes gap 3 for reports), signed run records embedding the definition, per-block verification and rerun, HTML renderer. XLSX export of stored runs (`reporting_export.py`, with a Reperformance sheet). Builder UI (`report-builder.jsx`; not yet exercised in a browser against a live backend). Not yet: PDF/PPTX export.

**Next increments, in order:** (2b) PDF/PPTX export; (4) connector-backed automatic collection starting with Oracle Fusion, with IPE totals; (5) external-auditor role and engagement scoping.

---

## Part 1: Report-writing subsystem

### Where you are today

| Capability | What exists | Limit |
|---|---|---|
| Reports | `report.jsx` (Loop Report modal), `board-consolidated-report.jsx`, `evidence-pack.jsx`, `POST /ai/audit-report` | Each is a fixed, hand-coded layout. A user cannot make a new one. |
| Charts | `charts.jsx` (Recharts, ~1,500 lines) | Chart code is welded to specific screens. |
| Export | `xlsx-export.js`, `export-pptx.js` | Per-screen, client-side, not driven by a shared definition. |
| Delivery | `report_delivery_endpoints.py` (saved destinations, encrypted credentials, send log) | Good: reusable as is. |
| Data | ~120 tables, 12+ connectors, risk register, JE findings, audit log, XBRL series | No catalog of "what can be reported on". |
| Vocabulary | Concept layer (`ontology_*`, `concept_linking.py`) | Useful for consistent labels and metric names. |
| Cross-product access | `mcp-server/` (Node) and the Python MCP servers | No reporting tools exposed. |
| AI governance | `/ai/review-queue` (human review of AI output) | Reusable for AI-drafted reports. |

The gap: every new report means new React code, and any other product would have to copy it.

### Recommended architecture: five layers, one package

1. **Semantic layer (governed datasets and metrics).** A registry of named datasets, for example `risk_register`, `controls`, `je_findings`, `audit_log`. Each declares its columns, types, labels, allowed filters, row-level scope and the permission needed to read it. Metrics ("open high risks", "failed controls %") are defined once here. Users and AI pick from this catalog and never write SQL against raw tables. This is the safety boundary: queries are compiled to parameterised SQL over allow-listed views, run through the existing tenant-bound connection, so multi-tenant isolation and screen permissions apply automatically.
2. **Report definition ("Report-as-Code").** A versioned JSON document: parameters, a list of blocks (text, KPI, table, chart, AI narrative), and each block's query spec. It is stored in the database, diffable, and approvable, the same philosophy as your RaC and CaC. Everything downstream consumes this one format.
3. **Render engine.** One server-side renderer turns a definition plus parameters into HTML, PDF, XLSX and PPTX, and the browser renders the same definition with Recharts. Charts use a small declarative spec (Vega-Lite is the obvious standard) so web and exports match. Replaces the per-screen exporters over time.
4. **Builder UI.** Dataset explorer, block editor, live preview, parameter form, save, version, share. AI assist is optional: "describe the report" produces a draft definition that is validated against the catalog and goes to a human before it is saved. AI never sees more data than the query returns, and AI-written narrative is labelled and reviewable (reuse the review queue).
5. **Run, schedule, deliver.** Scheduled runs, delivery through the existing `report_destinations`, and **immutable run snapshots**: each run stores its parameters, dataset versions and an HMAC-signed hash (the `evidence_records` pattern), so a report issued to an auditor or regulator can be reproduced and shown unaltered.

**Making it reusable across products (assumption: "operating systems" means other products or platforms in your portfolio, not OS-level software).** Ship it as a self-contained package with three surfaces:
- a REST API (`/reporting/*`) for definitions, queries, runs and snapshots;
- MCP tools (`list_datasets`, `query_dataset`, `render_report`), which also makes it usable from Claude and other agents;
- an embeddable viewer (iframe or web component) opened with a short-lived signed token, so another product shows a report without a second login.

Other products plug in by registering datasets through a small provider interface. Tenancy, permissions and audit logging come from the host rather than being re-implemented.

**Governance built in, not bolted on:**
- Permissions: reuse the screen permission matrix; add per-dataset read permission.
- Audit log events: `report_created`, `report_run`, `report_exported`, `report_shared`, each with failure outcomes.
- Every report footer carries lineage: dataset versions, parameters, as-of time and snapshot hash.
- The existing redact mode and PII retention rules apply to report output.

### Build, buy or hybrid

| Option | For | Against |
|---|---|---|
| Embed a BI tool (Metabase, Superset) | Fastest to a working builder | Multi-tenant isolation, your permission model, audit logging, branding and AI integration all become integration projects; users learn a second product. |
| Build everything | Full fit | Largest effort; you rebuild charting and layout. |
| **Hybrid (recommended)** | You own what is hard to buy: the governed catalog, report-as-code, snapshots, permissions. You reuse commodity parts: Recharts or Vega-Lite for charts, a headless browser for PDF. | You still build a builder UI. |

### Phasing (relative size: S small, M medium, L large)

| Phase | Deliverable | Size |
|---|---|---|
| 0 | Dataset registry and safe query API over 4 starter datasets (risk register, controls, JE findings, audit log) | M |
| 1 | Report definition format, server renderer, PDF and XLSX export, port one existing report to it as proof | M |
| 2 | Builder UI | L |
| 3 | Scheduling, delivery, signed snapshots | M |
| 4 | AI draft assist, MCP tools, embeddable viewer | M |

Phases 0 and 1 are enough to be useful for Part 2 (audit report issuance), so they should come first.

### Risks
- Scope creep into a general BI product. Keep the catalog curated and small.
- Query cost: put row and time limits on the query API from day one.
- Chart parity between browser and exports is the usual pain point; settle the chart spec early.

---

## Part 2: Automating more auditing

### The audit lifecycle against what exists

| Stage | Exists today | Gap |
|---|---|---|
| Scoping and planning | `sox_scoping_tool.py` (materiality, significant accounts), risk register, Framework Matrix | No audit universe or annual plan |
| Walkthroughs | `process_mining_*` (narrative drafting) | Mostly drafted by hand after |
| **Document requests (PBC)** | `evidence_quality_*` checks staleness, signatures and period | **No request workflow:** nothing generates the request list, assigns owners, chases or tracks |
| Evidence collection | Connectors (Oracle Fusion/HCM, SAP HANA, NetSuite, Dynamics 365, SailPoint, Postgres CIS, AWS, Railway), SARIF webhook, GitHub webhook | Only where a connector exists |
| Sampling | `sample_selection_tool.py` (random, risk-based, MUS, reproducible seeds) | Not tied to a control's test procedure |
| Testing | PaC/Rego, CaC, `je_testing_tool.py`, PaC negative tests | **No reconciliation or tick-and-tie engine**; no 3-way match; limited analytics |
| Exceptions | Exception triage, Management Action Plans (`map_detection_sweep.py`) | No structured **inquiry** workflow for discrepancies |
| Workpapers | Evidence pack | No workpaper assembly or sign-off chain |
| Reporting | Loop report, board report, AI audit-report | No findings register, management response, or formal issuance |

### Additional automatable methods

"New" means not found in the code. "Extend" means a building block exists.

**Financial**

| Method | What it automates | Status |
|---|---|---|
| Bank, subledger and GL reconciliation (auto tick-and-tie) | Matching, tolerance, unmatched-item list | New (highest value) |
| Three-way match (PO, receipt, invoice) | Exceptions for price/quantity/missing receipt | New |
| Duplicate payment and invoice detection | Fuzzy match on vendor, amount, date, reference | New |
| Vendor master anomalies | Bank-account changes, vendor/employee overlap, dormant-then-active | New |
| Extra JE tests: Benford, round amounts, post-close entries, unusual account pairs | Wider full-population coverage | Extend `je_testing_tool` |
| Revenue and expense cutoff | Transactions near period end vs delivery/receipt dates | New |
| Flux and analytical review | Period-over-period variance vs materiality, with draft explanations for a human to confirm | Extend (XBRL ratios exist) |
| Payroll and expense analytics | Ghost employees, policy breaches | New |

**Operational**

| Method | What it automates | Status |
|---|---|---|
| Process conformance (actual vs designed flow) | Deviations, cycle time, rework | Extend `process_mining_tool` |
| SLA and KPI exception monitoring | Breaches raised as findings | Extend (ITSM SLA sweep exists) |
| Procurement spend vs contract and approval limits | Off-contract and split purchases | New |
| Vendor risk: SOC 2 and certificate expiry | Already automated | Exists (`vendor_risk_sweep`) |

**IT**

| Method | What it automates | Status |
|---|---|---|
| User access review | Pull entitlements, route to owners, record decisions, flag unreviewed | Extend (SailPoint, identity graph) |
| Segregation of duties | Conflicting roles and open violations | Exists in part (identity graph sync) |
| Joiners, movers, leavers vs HR | Terminated users still active | New |
| Change management | PR approval, branch protection, change ticket linkage | Extend (GitHub webhook, ITSM) |
| Configuration baselines, vulnerability and patch status, TLS certificates, cloud config | Continuous checks | Exists (CIS, AWS, TLS tools) |
| Backup and restore evidence | Evidence of successful restores | New |

### Your key question: enabling PaC, CaC and other methods for the manual steps

The principle: make the **control** the spine. A CaC control becomes a single record that links risk, test procedure, evidence requirement, sample method, schedule and result. Each manual step then generates from it instead of starting from a blank page.

**1. Document requests: Request-as-Code**
- Each control declares its evidence needs: artifact type, source system, period, freshness, owner and acceptance rules.
- From the audit scope plus controls, **generate the PBC list automatically**.
- Where a connector exists, pull the evidence and never ask a person. Where none exists, send the request to the owner (Approval Inbox, email or Slack through existing destinations) with a due date and automatic chasing.
- Incoming evidence is validated on arrival by the existing `evidence_quality` rules (signed, in period, fresh). The AI relevance check stays advisory.
- Result: a live status board (requested, received, accepted, rejected, overdue) replacing the spreadsheet.

**2. Ticking and tying: a declarative reconciliation engine**
- A tie-out spec states the two sources, the keys, the matching strategy (exact, fuzzy, many-to-many), a tolerance, and a materiality threshold taken from `sox_scoping_tool`.
- Matching is deterministic. Unmatched items go to the exceptions queue with a suggested cause (timing, rounding, FX, missing); AI proposes, a human confirms.
- Every result records a tickmark, the source reference and a hash, so it can be re-performed.
- For tying document figures (PDFs, statements) to the ledger, use AI-assisted extraction with a confidence score and a cited page or cell. Low confidence goes to a person; nothing is auto-accepted.

**3. Questions about discrepancies: Inquiry-as-workflow**
- Every exception generates a structured question automatically: what differs, by how much, with evidence links, addressed to the owner.
- Replies are captured, checked (required attachments, contradictions), aged and escalated.
- AI drafts the question and summarises replies, and flags answers that are inconsistent or unresolved. A person clears each item.
- Unresolved items roll into Management Action Plans (`map_detection_sweep` exists).

**4. Final report issuance**
- A findings register with condition, criteria, cause, effect and recommendation, drafted by AI from the exception and its evidence, reviewed by a person.
- Management response collection, a review and approval chain, and a draft-to-final state with a **signed, hashed snapshot** (Part 1's snapshots).
- Built with the Part 1 report subsystem and delivered through `report_destinations`.
- So Part 1 is not a side project: it is the issuance layer for Part 2.

**Where PaC and CaC fit specifically**
- PaC (Rego) today evaluates events and configurations. Extend it to also evaluate **data results**: reconciliation outputs, JE results and evidence status, so one policy language covers configuration, transactions and evidence.
- Keep the PaC negative-test corpus (`pac_negative_tests.py`, `pac_contracts.py`). It proves each rule can fire and does fire, which matters more as rule count grows.
- CaC carries the test procedure itself: which rule or reconciliation, which sample method (`sample_selection_tool`), what tolerance, how often.

### Guardrails these need (audit-specific)
- **Deterministic vs AI stays separated**, as `evidence_quality_tool.py` already insists. AI suggests; the auditor concludes and signs.
- **Reproducibility:** seeds, parameters, source versions and hashes recorded for every test.
- **Completeness and accuracy of source data (IPE):** auditors must show the extract was complete. Add control-total and row-count checks against the source system as a first-class step. This is the most commonly missed requirement in automation.
- **Independence and segregation:** preparer and reviewer must differ, enforced in the workflow.
- **Disclosure:** record where AI contributed to a conclusion.
- Check this against the standards your users follow (PCAOB, IIA, ISA); I have not mapped them.

### Recommended order

1. **Request-as-Code (PBC generation, tracking, auto-validation).** Builds almost entirely on existing parts; removes the most visible manual work.
2. **Reconciliation engine MVP** (bank vs GL and subledger vs GL). The core of tick-and-tie, and the largest new capability.
3. **Report subsystem phases 0 and 1**, then **findings register and report issuance**.
4. **Inquiry-as-workflow** on top of the exceptions queue.
5. **Analytics expansion** (duplicate payments, 3-way match, extra JE tests, access reviews). Independent items, add as demand shows.

---

## Decisions needed from you

1. **Who are the primary users?** Internal audit, external auditors, control owners, or all. External auditors would need their own restricted accounts, which affects permissions in both parts.
2. **What does "operating systems" mean in your request?** I assumed other products or platforms. If you meant something else, Part 1's packaging changes.
3. **Which ERPs and systems do your customers actually run?** The reconciliation and PBC automation value depends on connector coverage.
4. **Which standards must outputs satisfy** (SOX/PCAOB, IIA, ISA, internal policy)?
5. **Build, buy or hybrid for reporting?** I recommend hybrid.
6. **Which first:** Part 1 foundations (phases 0 and 1) or Request-as-Code? They are independent; the first unblocks report issuance, the second removes the most manual work sooner.
