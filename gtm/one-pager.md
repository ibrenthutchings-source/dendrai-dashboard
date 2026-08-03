# Dendrai — the audit-grade control plane for AI agents

**Your agents are already in production. Dendrai is what lets you defend that decision.**

Dendrai sits in front of every action your AI agents take, enforces policy your own people
wrote and approved, escalates the risky calls to a named human, and writes the evidence your
auditor, board, and regulator will ask for.

---

## The problem

Agent pilots don't stall on model quality. They stall at the sign-off meeting.

Internal audit asks *"show me every action the agent took last quarter, which control applied,
and who approved the ones that mattered."* Today the honest answer is a application log and a
policy document that nobody enforces at runtime. That gap is where the deployment stops.

## What Dendrai does

**1. Intercepts.** Every agent tool call is ingested as a governed event — one endpoint, any
framework. MCP, LangChain, OpenAI function calling, or your own loop.

**2. Adjudicates.** Each event runs a Bronze→Silver→Gold pipeline into a Council of three
independent evaluators (quantitative, narrative, systemic), then an Adjudicator that produces a
verdict: **CLEAR / MONITOR / ESCALATE**, with a composite risk score and conflict flags.

**3. Enforces.** Policy-as-Code runs on the real Open Policy Agent binary — not a pattern
matcher. A fired deny rule vetoes the ensemble outright and forces human review. Human-authored,
approved policy outranks any score.

**4. Escalates.** Escalated calls land in an approval inbox with preparer/reviewer separation and
multi-approver sign-off. A named human decides. That decision is recorded against the AI's.

**5. Proves it.** Every event, verdict, policy hit, and human decision is written to a
tamper-evident, hash-chained evidence record, mapped to SOC 2, NIST SP 800-53, ISO 27001, and
COSO. Verifiable on demand.

---

## Why this is different

**Runtime control *and* audit-grade evidence.** Guardrail products stop bad calls but leave no
defensible record. Governance platforms hold registries and policy PDFs but touch nothing at
runtime. You need both in the same system, or the evidence doesn't tie to the enforcement.

**Built out of an audit platform, not bolted onto one.** The approval workflow, sign-off chain,
risk register, control mapping, and board reporting already existed — because Dendrai started as
a continuous internal-audit system. Agent governance inherits all of it.

**Conservative by design, and specific about it:**

- The LLM reviewer can only ever *escalate* a verdict. It can never talk one down.
- The LLM is consulted only on cases the deterministic ensemble already flagged for a human.
- Framework control mappings are hand-curated and reviewed. Never AI-generated. There is
  deliberately no "AI-assist" button on that data.
- Every AI output carries provenance — model, effort, tokens, cost — and is queryable.

**We publish a model card.** Every algorithmic component, its human-oversight level, and its
known limitations, including the ones we haven't closed yet. Ask for it in the first meeting.
We would rather you find the gaps from us than from your regulator.

---

## Expands from the same engine

The adjudication pipeline isn't agent-only. Point it at the rest of the estate and the evidence
chain, control mapping, and approval workflow all carry over:

| | |
|---|---|
| **ERP** | Oracle Fusion RMCS, SAP, NetSuite, Dynamics 365 — SoD violations, control test results, deficiencies |
| **Identity** | SailPoint — privilege escalation, orphaned accounts, certification failures |
| **DevOps** | Branch-protection drift, secret scanning, pipeline security, SARIF findings, DORA change metrics |
| **Cloud** | Postgres CIS hardening, platform drift, connector credential hygiene |
| **Enterprise risk** | Continuous risk register, audit scope, management action plans, board reporting |

---

## Deployment

Dedicated single-tenant instance, in your cloud or ours. Your audit data is never pooled with
another customer's — cross-tenant aggregation was ruled out by design, not deferred.

SSO (Microsoft Entra, Google Workspace, Okta, GitHub) · role-based screen permissions ·
JWT sessions · Postgres · Docker.

---

## Pilot

90 days, one agent fleet, fixed fee. You finish with a signed evidence pack your audit committee
can read and a written gap report against SOC 2 / ISO 27001 — whether or not you buy.

**Start the conversation:** [contact]
