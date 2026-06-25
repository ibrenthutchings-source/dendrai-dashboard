import { Router } from 'express'
import { randomUUID } from 'crypto'
import { prisma } from '../db.js'

const router = Router()

// ─── helpers ───────────────────────────────────────────────────────────────

function uid() { return randomUUID() }
function now() { return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z') }

// JSON is valid YAML — used as the artifact format when no yaml library is present
function toYaml(doc) { return JSON.stringify(doc, null, 2) }

const RAG_STATE   = { R: 'not-satisfied', A: 'not-satisfied', G: 'satisfied' }
const VEL_LABEL   = { '-1': 'improving', '0': 'stable', '1': 'slightly-elevated', '2': 'elevated', '3': 'critical' }
const ISO_TREAT   = { R: 'risk_modification', A: 'risk_modification', G: 'risk_retention' }
const COSO_RESP   = { R: 'Reduce', A: 'Reduce', G: 'Accept' }

const COSO_PRINCIPLES = {
  Revenue:             { principle: 9,  label: 'Identifies Risk',              component: 'Risk Assessment' },
  'Financial Reporting':{ principle: 11, label: 'Assesses Severity of Risk',   component: 'Risk Assessment' },
  Cybersecurity:       { principle: 13, label: 'Implements Risk Responses',    component: 'Risk Response' },
  Operational:         { principle: 10, label: 'Analyzes Risk',                component: 'Risk Assessment' },
  Governance:          { principle: 3,  label: 'Defines Desired Culture',      component: 'Governance & Culture' },
  Macro:               { principle: 6,  label: 'Analyzes Business Context',    component: 'Strategy & Objective-Setting' },
  'Supply Chain':      { principle: 10, label: 'Analyzes Risk',                component: 'Risk Assessment' },
  Regulatory:          { principle: 8,  label: 'Evaluates Alternative Strategies', component: 'Strategy & Objective-Setting' },
}
const COSO_DEFAULT = { principle: 9, label: 'Identifies Risk', component: 'Risk Assessment' }

function indexByRisk(items, ...keys) {
  const idx = {}
  for (const item of items) {
    const refs = []
    for (const k of keys) {
      const val = item[k]
      if (Array.isArray(val)) refs.push(...val)
      else if (typeof val === 'string' && val) refs.push(val)
    }
    for (const ref of refs) {
      if (!idx[ref]) idx[ref] = []
      idx[ref].push(item)
    }
  }
  return idx
}

// ─── OSCAL translator ──────────────────────────────────────────────────────

function toOscal({ ticker, risks, objectives, maps, ratios, signals, industry, period, run_id }) {
  const objByRisk = indexByRisk(objectives, 'linkedRisks', 'linkedRisk', 'linked_risk')
  const mapByRisk = indexByRisk(maps,       'linkedRisks', 'linkedRisk', 'linked_risk')

  const oscalRisks    = []
  const oscalFindings = []

  for (const risk of risks) {
    const riskId   = risk.id   || ''
    const riskUuid = uid()
    const score    = parseFloat(risk.score || 0)
    const rag      = risk.rag  || 'G'
    const velocity = risk.velocity ?? 0

    const characterization = {
      origin: {
        actors: [{ type: 'tool', 'actor-uuid': 'dendrai-risk-engine', title: 'Dendrai Risk Engine' }],
      },
      facets: [
        { name: 'likelihood',            system: 'https://dendrai.ai/scoring/v1', value: String(Math.round(parseFloat(risk.likelihood || 0) * 100) / 100) },
        { name: 'impact',                system: 'https://dendrai.ai/scoring/v1', value: String(Math.round(parseFloat(risk.impact || 0) * 100) / 100) },
        { name: 'risk-score',            system: 'https://dendrai.ai/scoring/v1', value: String(Math.round(score * 100) / 100) },
        { name: 'rag-status',            system: 'https://dendrai.ai/scoring/v1', value: rag },
        { name: 'velocity',              system: 'https://dendrai.ai/scoring/v1', value: String(velocity) },
        { name: 'control-effectiveness', system: 'https://dendrai.ai/scoring/v1', value: risk.ce || 'ADEQUATE' },
        { name: 'peer-benchmark',        system: 'https://dendrai.ai/scoring/v1', value: risk.peer || 'in-line' },
      ],
    }

    const remediations = (mapByRisk[riskId] || []).map(m => {
      const tasks = []
      if (m.successCriteria) {
        tasks.push({
          uuid: uid(), type: 'action', title: m.successCriteria || '',
          timing: { 'within-date-range': { start: now().slice(0, 10), end: m.dueDate || '' } },
        })
      }
      return {
        uuid: uid(), lifecycle: 'recommendation',
        title: m.finding || `Management Action Plan — ${riskId}`,
        description: m.action || '',
        origins: [{ actors: [{ type: 'party', 'actor-uuid': 'management', title: m.owner || 'Management' }] }],
        tasks,
      }
    })

    oscalRisks.push({
      uuid: riskUuid, title: risk.name || riskId,
      description: risk.narrative || '',
      status: (rag === 'R' || rag === 'A') ? 'open' : 'closed',
      characterizations: [characterization],
      'mitigating-factors': [],
      remediations,
      'risk-log': {
        entries: [{
          uuid: uid(),
          title: `Scored ${score} (${rag}) — velocity ${VEL_LABEL[String(velocity)] || String(velocity)}`,
          start: now(),
          'logged-by': [{ 'party-uuid': 'dendrai-risk-engine' }],
          'related-risks': [{ 'risk-uuid': riskUuid }],
        }],
      },
    })

    const linkedObjs = objByRisk[riskId] || []
    oscalFindings.push({
      uuid: uid(), title: risk.name || riskId,
      description: risk.narrative || '',
      target: {
        type: 'objective-id', 'target-id': riskId,
        description: `Category: ${risk.category || ''} | Industry: ${industry}`,
        status: {
          state:  RAG_STATE[rag] || 'not-satisfied',
          reason: `Score ${score} — ${rag} — velocity ${VEL_LABEL[String(velocity)] || String(velocity)}`,
        },
      },
      'implementation-statement-uuid': linkedObjs[0]?.id || null,
      'related-risks': [{ 'risk-uuid': oscalRisks[oscalRisks.length - 1].uuid }],
      remarks: risk.filingSnippet || '',
    })
  }

  const redCount   = risks.filter(r => r.rag === 'R').length
  const amberCount = risks.filter(r => r.rag === 'A').length
  const greenCount = risks.filter(r => r.rag === 'G').length

  const doc = {
    'assessment-results': {
      uuid: uid(),
      metadata: {
        title: `Risk Assessment — ${ticker} ${period}`,
        published: now(), 'last-modified': now(),
        version: '1.0', 'oscal-version': '1.1.2',
        remarks: `Generated by Dendrai Risk Loop v2.0. Run ID: ${run_id}. Industry: ${industry}. Register: ${redCount} RED / ${amberCount} AMBER / ${greenCount} GREEN.`,
        roles: [
          { id: 'cae',          title: 'Chief Audit Executive' },
          { id: 'cfo',          title: 'Chief Financial Officer' },
          { id: 'risk-officer', title: 'Risk Officer' },
          { id: 'tool',         title: 'Dendrai Risk Engine' },
        ],
        parties: [{ uuid: 'dendrai-risk-engine', type: 'tool', name: 'Dendrai Risk Engine v2.0' }],
      },
      'import-ap': { href: `#audit-plan-${ticker.toLowerCase()}` },
      'local-definitions': {
        activities: [{
          uuid: uid(), title: 'Automated Risk Scoring — Signal-Adjusted Six-Stage Loop',
          description: 'EDGAR 10-K, FRED macro, RSS, and internal KRI signals are aggregated (Stage 1), scored with a signal-adjusted impact × likelihood model (Stage 2), mapped to audit objectives (Stage 3), reviewed by HITL (Gate 1), resolved through MAPs (Stage 4), and loop-calibrated (Stage 6).',
          steps: [
            { uuid: uid(), title: 'Stage 1 — Signal Intake',      description: 'Aggregate EDGAR, FRED, RSS, and internal KRI signals' },
            { uuid: uid(), title: 'Stage 2 — Risk Assessment',    description: 'Score risks using signal-adjusted model (Impact × Likelihood, 0–25 scale)' },
            { uuid: uid(), title: 'Stage 3 — Audit Scope',        description: 'Derive prioritised audit objectives from top risks' },
            { uuid: uid(), title: 'Gate 1 — HITL Risk Review',    description: 'Human-in-the-loop per-risk disposition (CAE → CFO → Audit Committee)' },
            { uuid: uid(), title: 'Stage 4 — Management Action Plans', description: 'Generate MAPs for high-risk objectives' },
            { uuid: uid(), title: 'Stage 5 — Closure Evidence',   description: 'Quantify projected risk reduction from MAP execution' },
            { uuid: uid(), title: 'Stage 6 — Loop Calibration',   description: 'Re-calibrate scoring model and set next-run frequency' },
          ],
        }],
      },
      results: [{
        uuid: uid(), title: `Risk Register — ${period} Loop Run`,
        description: `Signal-adjusted risk register for ${ticker} (${industry}). ${risks.length} risks assessed across ${objectives.length} audit objectives.`,
        start: now(), end: now(),
        'reviewed-controls': {
          'control-selections': [{ description: 'Enterprise Risk Management control environment', 'include-all': {} }],
        },
        attestations: [{
          parts: [{
            name: 'assessment-log',
            prose: `Risk loop completed for ${ticker}. ${redCount} RED, ${amberCount} AMBER, ${greenCount} GREEN. M-Score: ${ratios?.m_score ?? 'N/A'}. Revenue growth: ${ratios?.revenue_growth_pct ?? 'N/A'}%.`,
          }],
        }],
        observations: [{
          uuid: uid(), title: 'Financial Ratio Analysis — XBRL + Beneish M-Score',
          description: `Beneish M-Score: ${ratios?.m_score ?? 'N/A'} (${ratios?.m_score_interpretation ?? 'N/A'}). Revenue Growth: ${ratios?.revenue_growth_pct ?? 'N/A'}%. Gross Margin: ${ratios?.gross_margin_pct ?? 'N/A'}%. DSRI: ${ratios?.dsri ?? 'N/A'}. AQI: ${ratios?.aqi ?? 'N/A'}.`,
          methods: ['AUTOMATED'], types: ['finding'],
          'relevant-evidence': [{ description: 'EDGAR XBRL financial data, FRED macro indicators, RSS industry signals' }],
        }],
        findings: oscalFindings,
        risks:    oscalRisks,
      }],
    },
  }

  return toYaml(doc)
}

// ─── COSO ERM / ISO 31000 translator ──────────────────────────────────────

function toCosoErm({ ticker, risks, objectives, maps, ratios, signals, industry, period, run_id }) {
  const objByRisk = indexByRisk(objectives, 'linkedRisks', 'linkedRisk', 'linked_risk')
  const mapByRisk = indexByRisk(maps,       'linkedRisks', 'linkedRisk', 'linked_risk')
  const sigByRisk = indexByRisk(signals,    'affectedRisks')

  const riskUniverse = []

  for (const risk of risks) {
    const riskId   = risk.id   || ''
    const rag      = risk.rag  || 'G'
    const score    = parseFloat(risk.score || 0)
    const inherent = parseFloat(risk.inherent ?? Math.round(score * 1.25 * 100) / 100)
    const residual = parseFloat(risk.residual ?? score)
    const velocity = risk.velocity ?? 0
    const category = risk.category || 'Operational'
    const coso     = COSO_PRINCIPLES[category] || COSO_DEFAULT

    const linkedObjs = objByRisk[riskId] || []
    const linkedMaps = mapByRisk[riskId] || []
    const linkedSigs = sigByRisk[riskId] || []

    const auditLink = linkedObjs[0] ? {
      objective_id:    linkedObjs[0].id       || '',
      title:           linkedObjs[0].objective || '',
      priority:        linkedObjs[0].priority  || '',
      sprint:          linkedObjs[0].sprint    || '',
      budgeted_hours:  linkedObjs[0].hours     || 0,
    } : null

    const entry = {
      risk_id:  riskId,
      name:     risk.name || '',
      category,
      coso_component:       coso.component,
      coso_principle:       coso.principle,
      coso_principle_label: coso.label,
      iso31000_clause: '6.4.2',
      context: {
        internal:         (risk.narrative      || '').slice(0, 300),
        external:         (linkedSigs.find(s => s.category === 'Market')?.label || ''),
        filing_evidence:  (risk.filingSnippet  || '').slice(0, 400),
      },
      inherent_risk: {
        likelihood: Math.round(parseFloat(risk.likelihood || 0) * 100) / 100,
        impact:     Math.round(parseFloat(risk.impact     || 0) * 100) / 100,
        score:      Math.round(inherent * 100) / 100,
        rating:     rag,
      },
      residual_risk: {
        likelihood:           Math.round(parseFloat(risk.likelihood || 0) * 0.85 * 100) / 100,
        impact:               Math.round(parseFloat(risk.impact     || 0) * 100) / 100,
        score:                Math.round(residual * 100) / 100,
        control_effectiveness: risk.ce || 'ADEQUATE',
        rating:               rag,
      },
      velocity,
      velocity_label: VEL_LABEL[String(velocity)] || String(velocity),
      peer_benchmark: risk.peer || 'in-line',
      risk_response: {
        coso_component: 'Performance',
        strategy: COSO_RESP[rag] || 'Accept',
        owner: linkedMaps[0]?.owner || 'Risk Owner',
        actions: linkedMaps.map(m => ({
          description:            m.action           || '',
          root_cause:             m.rootCause         || '',
          target_date:            m.dueDate           || '',
          success_criteria:       m.successCriteria   || '',
          expected_reduction_pct: m.reductionPct      || 0,
        })),
      },
      iso31000_treatment: {
        clause:               '6.5',
        type:                 ISO_TREAT[rag] || 'risk_retention',
        monitoring_frequency: rag === 'R' ? 'Monthly' : 'Quarterly',
        review_date:          linkedMaps[0]?.dueDate || '',
        kri_monitoring:       rag === 'R' || rag === 'A',
      },
      signals: linkedSigs.slice(0, 5).map(s => ({
        source:   s.src      || '',
        label:    s.label    || '',
        delta:    s.delta    || '',
        velocity: s.velocity || 0,
        category: s.category || '',
      })),
    }

    if (auditLink) entry.audit_link = auditLink
    riskUniverse.push(entry)
  }

  const redCount   = risks.filter(r => r.rag === 'R').length
  const amberCount = risks.filter(r => r.rag === 'A').length
  const greenCount = risks.filter(r => r.rag === 'G').length

  const doc = {
    framework:    'COSO ERM 2017 / ISO 31000:2018',
    generator:    'Dendrai Risk Loop v2.0',
    entity:       ticker,
    industry,
    period,
    generated_at: now(),
    run_id,
    executive_summary: {
      total_risks: risks.length,
      red:   redCount,
      amber: amberCount,
      green: greenCount,
      top_risk:     risks[0]?.name  || '',
      signal_count: signals.length,
    },
    financial_context: {
      m_score:                  ratios?.m_score,
      m_score_interpretation:   ratios?.m_score_interpretation,
      revenue_growth_pct:       ratios?.revenue_growth_pct,
      gross_margin_pct:         ratios?.gross_margin_pct,
      dsri:                     ratios?.dsri,
      aqi:                      ratios?.aqi,
    },
    governance: {
      coso_component: 'Governance & Culture',
      board_oversight: 'Audit Committee reviews risk register quarterly and receives ad-hoc briefings on RED and appetite-breaching risks.',
      risk_culture: 'Tone-at-the-top supports proactive, data-driven risk management.',
      three_lines: {
        first:  'Management — owns and manages risks day-to-day',
        second: 'Risk & Compliance — oversees risk framework and appetite',
        third:  'Internal Audit — provides independent assurance (this report)',
      },
    },
    reporting: {
      coso_component:  'Information, Communication & Reporting',
      iso31000_clause: '7.0',
      cadence:         'Loop runs monthly (configurable); CAE brief after each run',
      escalation:      'RED risks auto-escalate to CFO within 24 h of detection',
      audit_trail:     `Persisted to Dendrai DB run_id=${run_id}`,
    },
    risk_universe: riskUniverse,
  }

  return toYaml(doc)
}

// ─── core generation ───────────────────────────────────────────────────────

function generateAll(data) {
  const common = {
    ticker:     data.ticker     || '',
    risks:      data.risks      || [],
    objectives: data.objectives || [],
    maps:       data.maps       || [],
    ratios:     data.ratios     || {},
    signals:    data.signals    || [],
    industry:   data.industry   || '',
    period:     data.period     || '',
    run_id:     data.run_id     ?? null,
  }
  return {
    oscal:    toOscal(common),
    coso_erm: toCosoErm(common),
  }
}

// ─── POST /api/risks-as-code/generate ─────────────────────────────────────

router.post('/generate', async (req, res) => {
  try {
    const artifacts = generateAll(req.body || {})

    // Persist to DB if available and run_id provided
    const saved = {}
    if (req.body?.run_id && prisma) {
      for (const [framework, content] of Object.entries(artifacts)) {
        try {
          const record = await prisma.risksAsCodeArtifact.create({
            data: { runId: req.body.run_id, ticker: req.body.ticker || '', framework, content },
          })
          saved[framework] = record.id
        } catch {
          // DB schema may not have this table — ignore silently
        }
      }
    }

    res.json({
      ticker:       req.body?.ticker || '',
      run_id:       req.body?.run_id ?? null,
      generated_at: now(),
      artifacts: Object.fromEntries(
        Object.entries(artifacts).map(([fw, content]) => [fw, { content, artifact_id: saved[fw] ?? null }])
      ),
    })
  } catch (err) {
    console.error('risks-as-code generate error:', err)
    res.status(500).json({ error: err.message })
  }
})

// ─── GET /api/risks-as-code/stream/:runId ──────────────────────────────────

router.get('/stream/:runId', (req, res) => {
  res.setHeader('Content-Type',  'text/event-stream')
  res.setHeader('Cache-Control', 'no-cache')
  res.setHeader('Connection',    'keep-alive')
  res.setHeader('X-Accel-Buffering', 'no')
  res.flushHeaders()

  const runId = parseInt(req.params.runId, 10)

  function send(obj) {
    res.write(`data: ${JSON.stringify(obj)}\n\n`)
  }

  send({ type: 'connected', run_id: runId })

  if (!prisma) {
    send({ type: 'error', message: 'Database not configured' })
    res.end()
    return
  }

  let polls = 0
  const MAX_POLLS = 60
  let lastCount = -1

  const interval = setInterval(async () => {
    polls++
    if (polls > MAX_POLLS) {
      clearInterval(interval)
      send({ type: 'timeout', run_id: runId })
      res.end()
      return
    }

    try {
      const run = await prisma.run.findUnique({
        where: { id: runId },
        include: { riskScores: true },
      }).catch(() => null)

      if (!run) {
        clearInterval(interval)
        send({ type: 'error', message: `Run ${runId} not found` })
        res.end()
        return
      }

      const riskScores = run.riskScores || []
      const currentCount = riskScores.length
      const completed = Boolean(run.completed)

      if (currentCount !== lastCount || completed) {
        lastCount = currentCount

        if (riskScores.length) {
          const artifacts = generateAll({
            ticker:   run.ticker   || '',
            risks:    riskScores,
            run_id:   runId,
            industry: run.industry || '',
            period:   run.periodEnd || '',
          })

          for (const [fw, content] of Object.entries(artifacts)) {
            await prisma.risksAsCodeArtifact.upsert({
              where:  { runId_framework: { runId, framework: fw } },
              create: { runId, ticker: run.ticker || '', framework: fw, content },
              update: { content },
            }).catch(() => {})
          }

          send({
            type:       'update',
            run_id:     runId,
            risk_count: currentCount,
            completed,
            frameworks: Object.keys(artifacts),
            artifacts,
          })
        }

        if (completed) {
          clearInterval(interval)
          send({ type: 'done', run_id: runId })
          res.end()
        }
      }
    } catch (err) {
      clearInterval(interval)
      send({ type: 'error', message: err.message })
      res.end()
    }
  }, 2000)

  req.on('close', () => clearInterval(interval))
})

// ─── GET /api/risks-as-code/export/:runId/:framework ───────────────────────

router.get('/export/:runId/:framework', async (req, res) => {
  const SUPPORTED = ['oscal', 'coso_erm']
  const { runId, framework } = req.params
  if (!SUPPORTED.includes(framework)) {
    return res.status(400).json({ error: `Unknown framework '${framework}'. Supported: ${SUPPORTED.join(', ')}` })
  }
  if (!prisma) {
    return res.status(503).json({ error: 'Database not configured — set DATABASE_URL to enable persistence' })
  }
  try {
    const artifact = await prisma.risksAsCodeArtifact.findFirst({
      where: { runId: parseInt(runId, 10), framework },
    })
    if (!artifact) {
      return res.status(404).json({ error: `No ${framework} artifact found for run ${runId}. Run /generate first.` })
    }
    const filename = `dendrai_${framework}_${artifact.ticker}_${runId}.yaml`
    res.setHeader('Content-Disposition', `attachment; filename=${filename}`)
    res.setHeader('Content-Type', 'application/x-yaml')
    res.send(artifact.content)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

export default router
