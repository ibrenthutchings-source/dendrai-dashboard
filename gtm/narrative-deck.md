# Dendrai — 10-slide narrative deck

Sales narrative, not a feature tour. The arc: *the sign-off meeting is where agent programs die
→ here's why the existing tools can't fix it → here's the shape of a system that can → here's
proof we built it honestly → here's a cheap way to find out.*

Speaker notes are what you say. On-slide text is what they read. Don't read the slide.

---

## Slide 1 — Title

**On slide:**

> # The agent shipped. The sign-off didn't.
> Dendrai — the audit-grade control plane for AI agents

**Notes:** Open cold — no company background, no origin story. "Before I tell you what we do, I want to check whether you
have the problem. Can I ask two questions?" — then Slide 2. If they don't have the problem,
find out in 90 seconds and don't burn their hour.

---

## Slide 2 — The qualifying question

**On slide:**

> **Two questions:**
> 1. How many AI agents are in production, taking actions on real systems?
> 2. If your audit committee asked for every action they took last quarter — which control
>    applied, who approved the risky ones — how long would that take?

**Notes:** Shut up and let them answer. The second question is the whole deal. Answers you're
listening for: *"we'd have to pull logs"*, *"I don't know that we could"*, *"that's exactly the
conversation we had last month."* If the answer is "we'd export it from our platform in an
afternoon," they're not your buyer — thank them and go.

Take notes on the specific agent and system they name. You will use it on Slide 9.

---

## Slide 3 — Where programs actually stall

**On slide:**

> Agent programs don't stall on model quality.
> They stall in the room where someone has to sign.
>
> - Legal wants a defensible record.
> - Audit wants control evidence.
> - The board wants a name next to the risky decisions.
>
> **The pilot works. The rollout doesn't get approved.**

**Notes:** Name it as an organizational failure, not a technical one — that's what they've lived.
Their engineering team solved the hard part months ago. The blocker is a governance artifact
nobody owns. Ask: "Is that roughly where you are, or somewhere else?" Let them correct you; the
correction is the discovery.

---

## Slide 4 — Why the current toolbox doesn't close it

**On slide:**

> | | Stops a bad call | Produces defensible evidence |
> |---|---|---|
> | Guardrails / prompt firewalls | ✅ | ❌ |
> | AI governance platforms (registries, policy docs) | ❌ | Partial — the policy, not the proof |
> | Application logs | ❌ | ❌ — not control evidence |
> | **What audit actually needs** | **✅** | **✅** |
>
> Enforcement and evidence in separate systems don't tie together.
> An auditor can't trace the control to the event.

**Notes:** Be fair to the other categories — they're good at what they do, and the prospect may
own one. The point isn't that they're bad, it's that a log of blocked calls in one tool and a
policy PDF in another can't be tied together after the fact. Auditors test *the operating
effectiveness of a control*, which means: this specific policy, applied to this specific event,
at this specific time, with this human's decision. That's one system or it's nothing.

---

## Slide 5 — The shape of the answer

**On slide:**

> ```
> Agent tool call  (MCP · LangChain · OpenAI · custom)
>        │
>        ▼  one endpoint
>   INGEST  →  ADJUDICATE  →  ENFORCE  →  ESCALATE  →  PROVE
>              Council of 3    Real OPA    Named       Hash-chained
>              + Adjudicator   policy      human       evidence,
>                              veto        approves    mapped to
>                                                      SOC 2 / ISO
> ```
> Five steps. One system. One record.

**Notes:** Walk it left to right, one sentence each. Two things to land hard:

*Framework-agnostic* — it's one HTTP endpoint with a per-system key. You are not asking them to
rewrite their agents or adopt MCP. Any agent that can make a POST is governed.

*The veto* — human-authored policy, approved through a sign-off chain, running on the real Open
Policy Agent binary. When a deny rule fires it overrides the risk score unconditionally. Say
this sentence exactly: **"A control your people wrote and approved outranks anything our
scoring says."** That's the sentence that gets you to the next meeting.

---

## Slide 6 — Who decides what

**On slide:**

> **Deterministic engine decides.** Rules, thresholds, correlation. Inspectable.
>
> **An LLM gets a fourth opinion** — only on cases already flagged for a human,
> and **it can only escalate. It can never talk a verdict down.**
>
> **A human decides the ones that matter.** Preparer/reviewer separation,
> multi-approver sign-off, recorded against what the AI recommended.

**Notes:** This is the trust slide and the one they'll repeat to their boss. The objection you're
pre-empting: *"you're using AI to govern AI."* Answer it before they raise it.

The asymmetry is the design insight — a false negative from an LLM talking a verdict down is
much worse than one extra human review. So we made it structurally impossible. Not a prompt
instruction. The code path can't produce a downgrade.

Also mention: we track where the human disagreed with the AI, broken down by gate and category.
Most vendors can't tell you their own override rate. We report it as a product feature.

---

## Slide 7 — The artifact

**On slide:**

> **What your audit committee actually receives:**
>
> - Every governed action, with verdict and risk score
> - The policy module that fired — versioned, with its approval chain
> - The human decision, with name and timestamp
> - Tamper-evident hash chain, verifiable on demand
> - Mapped to SOC 2 CC · NIST SP 800-53 · ISO 27001 · COSO
>
> *Control mappings are hand-curated and reviewed. Never AI-generated.*

**Notes:** Show the real evidence pack here if you can — this is the moment to go to the product,
not stay in slides.

The italic line matters more than it looks. Ask them: "how would you feel if a vendor told you
their SOC 2 control mapping was generated by an LLM?" They'll wince. We shipped that data with
no AI-assist button on purpose, and the reasoning is in the source code comment. That's the
level of paranoia you want in the thing that talks to your auditor.

---

## Slide 8 — What we'll tell you that others won't

**On slide:**

> **We publish a model card.**
>
> Every algorithmic component. Its human-oversight level.
> Its known limitations — including the open ones.
>
> Ask us for it today. Read the "Known Limitations" section first.

**Notes:** Hand them the printed model card in the room. This is your strongest close and it
costs you nothing, because the alternative is they find the gaps during diligence and wonder
what else you hid.

Pick one open limitation and volunteer it out loud before they read it. Volunteering a real
weakness buys more credibility than any claim on the previous seven slides, and every serious
GRC buyer has been burned by a vendor who claimed everything was covered.

Then: "if that one matters to you, it's configurable / it's on the roadmap / here's the
mitigation." Never leave it hanging.

---

## Slide 9 — Same engine, rest of the estate

**On slide:**

> The adjudication pipeline isn't agent-only.
>
> **ERP** — Oracle Fusion RMCS, SAP, NetSuite, D365 · SoD violations, deficiencies
> **Identity** — SailPoint · privilege escalation, orphaned accounts
> **DevOps** — branch protection, secret scanning, pipeline security, DORA
> **Cloud** — Postgres CIS, platform drift, credential hygiene
> **Enterprise risk** — continuous risk register → audit scope → action plans → board report
>
> Same evidence chain. Same approval workflow. Same control mappings.

**Notes:** This is the expansion story — do **not** lead with it and do not spend more than 60
seconds. Its job on this slide is to answer "is this a point tool that'll be obsolete in two
years?" without turning the meeting into a platform evaluation.

Use the specific system they named on Slide 2. "You mentioned the agent touches vendor master
data in SAP — the same pipeline governs the SAP side too, so the vendor-master control is one
control, not two."

---

## Slide 10 — The cheap way to find out

**On slide:**

> **90-day pilot. One agent fleet. Fixed fee.**
>
> You finish with:
> 1. A signed evidence pack your audit committee can read
> 2. A written gap report against SOC 2 / ISO 27001
>
> **You keep both whether or not you buy.**
>
> Dedicated single-tenant instance — your cloud or ours.
> Your audit data is never pooled with another customer's.

**Notes:** Sell the artifact, not the software. The evidence pack has standalone value to them —
they need it for a committee meeting that's already on the calendar. That reframes the pilot from
a software evaluation (needs procurement, needs a business case) into a deliverable purchase
(needs a budget code).

Close on the deliverable, then go silent: **"What would have to be true for you to want that
evidence pack by [date ~100 days out]?"**

---

## Appendix slides (hold in reserve, don't present)

- **A1 — Architecture:** medallion pipeline, Council, adjudication, persistence. For the
  platform engineer who asks.
- **A2 — Security posture:** SSO providers, RBAC screen matrix, JWT sessions, password policy,
  secrets handling, single-tenant isolation. For the security reviewer.
- **A3 — Integration effort:** the one endpoint, the per-system key, the payload shape. Pull
  this up the moment anyone asks "how long to install." The answer is short and it's your
  strongest technical differentiator — show, don't describe.
- **A4 — Model card, in full.** Don't summarize it. Give them the document.
