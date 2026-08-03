> ## ⚠️ SAMPLE — ILLUSTRATIVE TEMPLATE, NOT A REAL ATTESTATION
>
> Every company name, figure, finding, hash, and signature block below is **synthetic**, created
> to show the shape and tone of the deliverable a Dendrai pilot produces. "Northwind
> Manufacturing plc" does not exist. Nothing here has been observed, tested, or signed.
>
> Do not remove this banner when sharing. When a real pilot produces a real pack, that document
> is generated from live records and signed by named individuals — this one is neither.

---

# AI Agent Control Attestation — Pilot Evidence Pack & Gap Report

**Client:** Northwind Manufacturing plc
**Scope:** `finance-ops-agent` fleet (4 agents) — Accounts Payable, Vendor Master, Procurement
**Period:** 1 October 2026 – 31 December 2026 (92 days)
**Prepared for:** Audit Committee, via the Head of Internal Audit
**Prepared by:** Dendrai, with Northwind Internal Audit
**Classification:** Confidential — Audit Committee

---

## How to read this document

**Part A — Evidence Pack** is the record: what your agents did, what stopped them, who decided.

**Part B — Gap Report** is the part worth your time. It states what this control environment does
*not* cover, including four things Dendrai itself could not see. A pack without a gap report is
marketing.

Two conventions used throughout, both deliberate:

- **"Mapped" and "verified" are never combined into one number.** A control mapped to a SOC 2
  criterion on paper has earned nothing until it has actually fired and passed a test. Where a
  criterion is mapped but unverified, this document says so rather than showing a green check.
- **Absence of evidence is reported as absence**, not as a pass. A verification that checked zero
  records returns "nothing to verify" — never "clean."

---

# Part A — Evidence Pack

## A1. Executive summary

Over 92 days, 41,882 agent tool calls across four agents were ingested, adjudicated, and
recorded. Of those:

| | Count | % |
|---|---:|---:|
| Cleared automatically | 39,114 | 93.4% |
| Monitored (recorded, no action) | 2,504 | 6.0% |
| **Escalated to a human** | **264** | **0.63%** |
| — approved by reviewer | 171 | |
| — rejected by reviewer | 88 | |
| — open at period end | 5 | |

**Of the 88 rejections, 12 were actions that would have moved money or changed payment
instructions.** Those twelve are the pilot's return, and they are itemised at A5.

Three findings in Part B are rated **High**. One (GAP-02) concerns a policy module that was
adjudicating live events without a current approval, and should be read before this pack is
relied upon.

## A2. What was governed

| Agent | Framework | Systems reached | Calls |
|---|---|---|---:|
| `ap-invoice-agent` | LangChain | Oracle Fusion AP, SharePoint | 22,410 |
| `vendor-master-agent` | LangChain | Oracle Fusion Vendor Master | 6,133 |
| `procurement-agent` | OpenAI function calling | Coupa, Oracle Fusion PO | 11,208 |
| `treasury-recon-agent` | Custom loop (in-house) | Oracle Fusion GL, bank portal (read) | 2,131 |

All four report to a single ingestion endpoint using per-agent revocable keys. No agent required
code changes beyond an outbound HTTP call. Two of the four are not MCP-based.

**Not governed during this pilot:** 3 agents in Marketing and 1 in HR, out of scope by agreement.
See GAP-01.

## A3. How each call was adjudicated

Every call ran the same path: verbatim ingestion with a checksum locked on arrival → conformation
and policy evaluation → risk scoring and tiering → a Council of three independent evaluators →
an Adjudicator producing a verdict of CLEAR, MONITOR, or ESCALATE.

Two mechanisms can override that ensemble, both in the conservative direction only:

1. **Policy-as-Code veto.** A fired deny rule forces human review and escalation unconditionally,
   regardless of ensemble confidence. Human-authored, approved policy outranks the score. Fired
   **147 times** this period.
2. **LLM fourth opinion.** Consulted only on calls the deterministic ensemble had *already*
   flagged for human review. It can raise a verdict toward escalation; it cannot lower one. Raised
   **9 verdicts**, lowered none — structurally, it cannot.

Policy evaluation ran against the Open Policy Agent binary throughout the period. No evaluation
in this pack fell back to the non-authoritative heuristic path; this was verified at pilot start
and re-checked on 14 November.

## A4. Human decision record

264 escalations reached the approval inbox. Each carried the AI's recommendation, explicitly
labelled advisory.

| | |
|---|---|
| Preparer/reviewer separation enforced | Yes — no self-approval possible |
| Escalations requiring a second approver (policy-driven) | 61 |
| Median time to decision | 3h 41m |
| Longest open item | 6d 2h (VM-2291, holiday period) |
| **Reviewer disagreed with AI recommendation** | **33.3% (88 of 264)** |

**On that last figure.** A disagreement rate near zero would indicate rubber-stamping and would be
a finding in itself. 33.3% indicates reviewers are engaging with the substance. The rate is
tracked by gate type and risk category and is available continuously, not only at period end.

Rate by category:

| Category | Escalations | Reviewer overrode AI |
|---|---:|---:|
| Vendor master / payment instructions | 74 | 51.4% |
| Purchase order approval limits | 88 | 28.4% |
| Journal entry / period close | 62 | 25.8% |
| Access and permissions | 40 | 27.5% |

The vendor-master rate of 51.4% is elevated and is addressed at GAP-04.

## A5. The twelve material rejections

Each of the following was an agent-initiated action, stopped by policy, escalated, and rejected
by a named human. Full records are individually retrievable and independently verifiable.

| Ref | Date | Agent | Attempted action | Policy fired | Rejected by |
|---|---|---|---|---|---|
| VM-0412 | 09 Oct | `vendor-master-agent` | Change bank details, vendor V-4471 | VM-DENY-001 | [Reviewer name] |
| VM-0518 | 14 Oct | `vendor-master-agent` | Change remittance email, V-1120 | VM-DENY-003 | [Reviewer name] |
| AP-1077 | 21 Oct | `ap-invoice-agent` | Release held invoice, £84,200 | AP-DENY-002 | [Reviewer name] |
| VM-0961 | 02 Nov | `vendor-master-agent` | Create vendor, no tax ID | VM-DENY-007 | [Reviewer name] |
| PO-2240 | 08 Nov | `procurement-agent` | Raise PO above delegated limit | PO-DENY-001 | [Reviewer name] |
| AP-1512 | 11 Nov | `ap-invoice-agent` | Duplicate payment, invoice INV-88213 | AP-DENY-004 | [Reviewer name] |
| VM-1330 | 19 Nov | `vendor-master-agent` | Change bank details, V-2087 | VM-DENY-001 | [Reviewer name] |
| AP-1804 | 26 Nov | `ap-invoice-agent` | Payment to unapproved vendor | AP-DENY-001 | [Reviewer name] |
| PO-2681 | 03 Dec | `procurement-agent` | Split PO below approval threshold | PO-DENY-005 | [Reviewer name] |
| VM-1702 | 09 Dec | `vendor-master-agent` | Reactivate dormant vendor + bank change | VM-DENY-001, VM-DENY-009 | [Reviewer name] |
| AP-2044 | 15 Dec | `ap-invoice-agent` | Release held invoice, £212,600 | AP-DENY-002 | [Reviewer name] |
| GL-0388 | 22 Dec | `treasury-recon-agent` | Post adjusting entry after close | GL-DENY-003 | [Reviewer name] |

*In a live pack, each ref links to the full record: event payload, all three Council votes, the
policy module and version that fired, the reviewer's identity and rationale, and the timestamp.*

## A6. Evidence integrity

Each record is HMAC-signed on write and linked into a hash chain, so that altering a record breaks
its own signature and removing one breaks the chain linkage of its successor.

| Check | Result |
|---|---|
| Records written in period | 41,882 |
| Records in verifiable chain | 41,882 |
| Chain verification | **Unbroken across 41,882 records** |
| Per-record signature spot-check (n=250, random) | 250 of 250 valid |
| Legacy records predating chaining, excluded | 0 |

**What this does and does not prove.** The chain proves completeness and ordering: no record in
this pack was altered or removed after being written. It does not prove that the event reaching
Dendrai was a faithful account of what the agent did in the target system — that depends on the
integrity of the calling agent. Where independent confirmation matters, records should be
reconciled against the target system's own audit trail. See GAP-03.

## A7. Framework coverage

Mapped and verified are reported separately throughout. "Verified" means the control has actually
fired and passed a test during the period, not that it exists on paper.

**SOC 2 (Trust Services Criteria)**

| Criterion | Controls mapped | Verified in period | Status |
|---|---:|---:|---|
| CC6.1 — Logical access | 9 | 9 | Verified |
| CC6.6 — Access restriction | 6 | 6 | Verified |
| CC7.1 — Vulnerability monitoring | 5 | 3 | **Partially verified** |
| CC8.1 — Change management | 11 | 8 | **Partially verified** |
| CC5.2 — Control activities | 4 | 0 | **Mapped only — not verified** |

Equivalent tables for NIST SP 800-53 (AC-3, AC-6, CM-3, RA-5, SA-11), ISO/IEC 27001
(A.9.2.3, A.14.2.2, A.14.2.8) and COSO ERM components are held in the full pack.

**All framework mappings in this document are hand-curated and reviewed. None were generated or
inferred by a language model.** A control with no reviewed mapping is reported as unmapped rather
than assigned a plausible-looking criterion. Mappings are a starting crosswalk and remain subject
to sign-off by Northwind's compliance function before external reliance.

---

# Part B — Gap Report

Nine gaps identified. Four concern the boundary of what Dendrai observed and are stated first,
because a control report that only describes its own strengths is not usable evidence.

## Gaps in Dendrai's own coverage

### GAP-01 · Four agents were outside the governed perimeter · **High**

Three Marketing agents and one HR agent operated during the period with no ingestion configured.
Nothing in Part A speaks to them. The 41,882 figure is the governed population, not the agent
population.

**Recommendation.** Extend ingestion to all four before any statement is made about agent
governance at the entity level. Estimated effort: one endpoint call per agent.

### GAP-02 · A policy module adjudicated live events without a current approval · **High**

`vendor_master` was edited on 12 November and saved directly to production. A save takes effect
immediately; there is no approval gate on the save path today. The module ran for **17 days**
against live events before its approval sign-off was completed on 29 November.

The edit itself was later reviewed and approved, and no rejected escalation in A5 depended on the
unapproved version. The control weakness is nonetheless real: an unreviewed policy change can
adjudicate production events.

**Recommendation.** Enable approval-drift monitoring as a standing alert rather than a
point-in-time check, and treat drift as a change-management exception under CC8.1. Dendrai has
this detection; it was not configured as an alert at pilot start.

### GAP-03 · Evidence attests to the call, not to the effect · **Medium**

Records prove what an agent *attempted* and what Dendrai *decided*. They do not independently
confirm what the target system ultimately did. An agent that reported one action while performing
another would not be detected by this control.

**Recommendation.** Reconcile a sample of records against Oracle Fusion's own audit trail
quarterly. Dendrai ingests Fusion audit events, so this can be automated, but was not in scope
here.

### GAP-04 · Vendor-master override rate suggests policy miscalibration · **Medium**

Reviewers overrode the AI recommendation on 51.4% of vendor-master escalations, against 27.9%
elsewhere. At that rate the recommendation carries little information in this category, and
reviewer time is being spent compensating for it.

**Recommendation.** Review the vendor-master risk weighting against the 74 decisions recorded.
This is a tuning exercise with a concrete dataset behind it, not a redesign.

## Gaps in the control environment

### GAP-05 · Two narrative outputs reach readers with no prior review · **Medium**

Dendrai's persona-brief and audit-report generators produce narrative that reaches a reader —
potentially this committee — with no human review before delivery. Roughly one in five is sampled
for after-the-fact spot-check. That is a detective control, not a preventive one.

**Recommendation.** Enable required pre-delivery review for board-facing narrative output.
Northwind ran the pilot with the default (unreviewed) setting.

*Narrative generated by these features was not used in the preparation of this document.*

### GAP-06 · Five escalations open at period end · **Low**

Five items remained undecided at 31 December, the oldest 6 days. No SLA is currently defined for
escalation ageing.

**Recommendation.** Define a decision SLA by risk tier and alert on breach.

### GAP-07 · Two SOC 2 criteria mapped but never verified · **Medium**

CC5.2 has four controls mapped and none exercised during the period. A mapping alone supports no
assertion of operating effectiveness.

**Recommendation.** Either exercise these controls through negative testing or remove the mapping.
An unverified mapping in a control matrix is worse than an acknowledged gap.

### GAP-08 · Agent identity is asserted, not authenticated end-to-end · **Medium**

Agents authenticate to the ingestion endpoint with a per-agent bearer key. A compromised key would
allow events to be submitted under that agent's identity.

**Recommendation.** Rotate ingestion keys on the standard credential schedule and monitor for
anomalous submission patterns per key.

### GAP-09 · Single-tenant deployment — restore and continuity untested · **Low**

The instance is dedicated to Northwind and its data is not pooled with any other customer's.
Backup restoration was not exercised during the pilot.

**Recommendation.** Include the instance in the standard DR test cycle before production reliance.

---

## Summary of recommendations

| Ref | Rating | Recommendation | Owner |
|---|---|---|---|
| GAP-01 | High | Extend ingestion to all 4 ungoverned agents | [Owner] |
| GAP-02 | High | Standing approval-drift alerting; treat as CC8.1 exception | [Owner] |
| GAP-07 | Medium | Verify or withdraw unverified CC5.2 mappings | [Owner] |
| GAP-03 | Medium | Quarterly reconciliation against Fusion audit trail | [Owner] |
| GAP-04 | Medium | Recalibrate vendor-master risk weighting | [Owner] |
| GAP-05 | Medium | Require pre-delivery review of board-facing narrative | [Owner] |
| GAP-08 | Medium | Ingestion key rotation and per-key anomaly monitoring | [Owner] |
| GAP-06 | Low | Define escalation decision SLA by tier | [Owner] |
| GAP-09 | Low | Include instance in DR test cycle | [Owner] |

---

## Basis of preparation and limitations

This pack is compiled from records held in Northwind's dedicated Dendrai instance for the period
stated. It covers the four agents at A2 and no others.

Dendrai is not Northwind's auditor and this document is not an audit opinion, a SOC 2 report, or
an assurance engagement under any recognised standard. It is a record of controls operated and
evidence retained, prepared to support Northwind's own assurance activities and its external
auditor's procedures.

Framework mappings are a reviewed starting crosswalk and require sign-off by a qualified
compliance professional before external reliance.

Statistical and rule-based components, their human-oversight levels, and their known limitations
are documented in the Dendrai model card, provided with this pack.

---

**Prepared by**  ____________________  Dendrai · [Name, role] · [Date]

**Reviewed by**  ____________________  Northwind Internal Audit · [Name, role] · [Date]

**Received by**  ____________________  Chair, Audit Committee · [Name] · [Date]

*Signature blocks are unexecuted. This is a sample document.*
