# Dendrai — the 12-minute demo

One story, start to finish: **an agent tries to change vendor bank details, policy stops it, a
human decides, the evidence proves it.**

Every route below is real and lives in this repo. Nothing here is a mockup.

**Rule for this demo: you have ~20 screens and you will show four.** Every extra screen costs
credibility, because it invites "so what *is* this?" Resist the risk register. Resist the
forecasting models. Resist Grey Swan. They are the second meeting.

---

## Why this scenario

Vendor master bank-detail changes are the canonical high-risk ERP transaction — it's how payment
fraud actually happens, every internal auditor in the room has tested this control by hand, and
it needs no explanation. When an *agent* can make that change, the room understands the stakes
in one sentence.

If the prospect named a different high-risk action on Slide 2 of the deck, substitute theirs.
The flow is identical; only the payload changes.

---

## Pre-flight (do this 30 minutes before, every time)

```bash
cd project/agentic-tools && python api_server.py
```

- [ ] `GET /health` returns `ai_enabled: true` — the LLM 4th opinion is part of the story
- [ ] `GET /db/status` confirms Postgres — **no DB means no evidence chain and no demo**
- [ ] OPA binary present in the container. `POST /pac/evaluate` must not return
      `"evaluation": "simulation (Python heuristic — not authoritative OPA)"`. If it does, you
      cannot say "real OPA" out loud, and that's one of your three best lines. Fix it first.
- [ ] Monitored system registered via `POST /observability/systems`, ingest key in your clipboard
- [ ] A vendor-master deny rule exists and is **approved** in Policy-as-Code — the approval chain
      is part of what you're showing
- [ ] At least one prior escalation already sits in `GET /approvals/inbox`, so the inbox isn't
      empty when you arrive
- [ ] `GET /evidence/chain/verify` returns valid **before** you start
- [ ] Browser zoom at 125%+. Two tabs only: the app, and a terminal. Close everything else.
- [ ] Log in as the **reviewer** account, not admin — you want the real permission surface

---

## Minute 0–1 · Frame it

Don't open the product yet. Say this:

> "I'm going to send one agent tool call into a live instance. It's an agent trying to change a
> vendor's bank details in AP. Watch what has to happen before that action is allowed — and
> what your auditor gets afterwards."

That's the whole setup. No architecture, no company slide, no feature list.

---

## Minute 1–3 · Ingest — the install is one endpoint

Terminal, visible on screen. Type it live if you can; a pasted curl looks pre-baked.

```bash
curl -X POST https://<host>/observability/telemetry/ingest \
  -H "Authorization: Bearer <ingest_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "server_name": "langchain-finance-agent",
    "event_type":  "tool_call",
    "action":      "modify_permissions",
    "resource":    "erp.accounts_payable.vendor_master",
    "severity":    "HIGH",
    "payload": {
      "tool": "update_vendor_bank_details",
      "args": {"vendor_id": "V-4471", "new_account": "****8813"}
    }
  }'
```

**Say while it runs:**

> "Note the server name. That's LangChain — not MCP, not our SDK. Any agent that can make an
> HTTP POST is governed. The integration is this endpoint and a per-system key you issue from
> the UI. That's the whole install."

**This is the moment the technical buyer decides whether to keep listening.** Let it land before
moving on. If someone asks "that's really it?" — yes, and offer to let them send one from their
own laptop after the call.

---

## Minute 3–6 · Adjudicate + enforce — the veto

Go to the **Dendrai UBO / Controls Event Monitor** screen. Find the event. Open it.

Walk the adjudication in this order:

1. **The three Council voters** — quantitative, narrative, systemic. Each with a verdict,
   confidence, and risk delta. *"Three independent evaluators, running in parallel."*
2. **The Adjudicator's ensemble result** — composite score, tier, conflict flags.
3. **The Policy-as-Code veto.** This is the beat that matters. The deny rule fired.

Say it exactly like this:

> "The scoring engine had its opinion. But a policy your controls team wrote, and that went
> through a multi-approver sign-off chain, says agents don't touch vendor bank details without
> a human. When that rule fires it overrides the score unconditionally. **A control your people
> approved outranks anything our model thinks.**"

Then click into the fired rule and show it's **real Rego, versioned, with its approval history**.

> "This runs on the actual Open Policy Agent binary. Not a regex pretending to be policy."

**If they ask about the LLM here** (they usually do — "is AI making this decision?"):

> "There's a fourth opinion from an LLM, but only on cases the deterministic engine already
> flagged for a human — and it can only escalate. It structurally cannot talk a verdict down.
> That's a code path, not a prompt instruction."

Don't volunteer the LLM if they don't ask. It's a strong answer to a question, a weak lead.

---

## Minute 6–9 · Escalate — a named human decides

Go to the **Approval Inbox**. The escalation is waiting.

Show, in order:

- The item, with the AI's recommendation **visible and labelled as advisory**
- **Preparer/reviewer separation** — the person who prepared it cannot approve it
- Multi-approver sign-off where the policy requires it

Approve or reject it live. Then:

> "That decision is now recorded against what the AI recommended. Which means we can tell you
> your override rate — by gate, by risk category, by industry."

Show `GET /approvals/ai-acceptance-stats` or the screen that surfaces it.

> "Most vendors can't tell you how often their humans disagreed with their AI. We report it as
> a feature, because that number is the first thing a regulator will ask for — and if it's zero,
> your humans are rubber-stamping and that's its own finding."

That last clause is worth memorizing. It shows you understand their job, not just your product.

---

## Minute 9–12 · Prove it — the evidence chain

Go to **Evidence Pack**. Open the record for this event.

Show the four things an auditor tests:

1. **The event** — what the agent tried to do, when
2. **The control** — which policy module fired, at which version, approved by whom
3. **The decision** — the human, the timestamp, the rationale
4. **The framework mapping** — SOC 2 CC, NIST 800-53, ISO 27001, COSO component

Then run the chain verification live:

```bash
curl https://<host>/evidence/chain/verify
```

> "Hash-chained. If anyone altered a record after the fact — including us — this fails. Your
> auditor can verify it themselves without trusting us."

**Close on the mappings**, and say the quiet part:

> "One thing about these control mappings: they're hand-curated and reviewed. There is
> deliberately no AI-assist button on that data. If a vendor tells you their SOC 2 mapping was
> LLM-generated, ask them how they'd defend it in a PCAOB inspection."

Stop there. Twelve minutes. Do not open another screen.

---

## The close

> "That's one action, end to end. The same pipeline governs your ERP, your identity system, and
> your CI/CD — same evidence chain, same approvals, same mappings. But I'd rather not show you
> that today.
>
> What I'd suggest: 90 days, one agent fleet, fixed fee. You finish with a signed evidence pack
> your audit committee can actually read, and a written gap report — and you keep both whether
> or not you buy anything.
>
> What would have to be true for you to want that pack in hand by [date]?"

Then stop talking.

---

## Handling the four questions you will always get

**"Can this block the call in real time, or is it after the fact?"**
Be precise about what's enforcement versus what's detection — do not blur it. Explain the hold
mechanism (`GET /observability/holds`) and exactly where it sits in the call path. If the honest
answer for their architecture is "detect and escalate, not inline block," say that. They will
find out in the pilot, and finding out then costs you the deal instead of one slide.

**"How is this different from [guardrail vendor they already have]?"**
Don't attack it. "Keep it — it's doing a different job. It stops a bad call. It doesn't give your
auditor a control they can test. You need both, and they need to be tied to the same record."

**"Who else uses this?"**
If the honest answer is "you'd be among the first," say so and immediately reframe: early
customers shape the roadmap and get direct engineering, and the pilot's deliverable has value
regardless. Do not invent logos. This buyer's entire job is verifying claims.

**"What about multi-tenancy / where does our data live?"**
Dedicated single-tenant instance, their cloud or yours. Lead with it as a *privacy commitment* —
cross-tenant aggregation was ruled out by design because this is client-confidential audit data.
It's documented in the model card, which makes it credible rather than defensive.

---

## Three things that will break the demo

1. **No `DATABASE_URL`** — no persistence, no evidence chain, no story. Check `/db/status` first.
2. **OPA falling back to the Python heuristic** — the response is explicitly labelled as
   simulation, and if a technical buyer spots that label after you said "real OPA," you're done.
   Verify before every demo.
3. **Wandering into other screens.** The forecasting models and the risk register are genuinely
   good and they will dilute this story to nothing. Second meeting.
