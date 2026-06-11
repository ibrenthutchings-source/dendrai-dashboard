import { Router } from 'express'
import { requireAuth, getAuth } from '@clerk/express'
import { prisma } from '../db.js'

const router = Router()

function requireDb(res) {
  if (!prisma) {
    res.status(503).json({ error: 'DATABASE_URL is not configured. Set it in your environment to enable report persistence.' })
    return false
  }
  return true
}

async function resolveUser(clerkId, sessionClaims) {
  return prisma.user.upsert({
    where: { clerkId },
    update: {},
    create: {
      clerkId,
      email: sessionClaims?.email ?? `${clerkId}@clerk.local`,
      name: sessionClaims?.name ?? null,
    },
  })
}

// GET /api/reports
router.get('/', requireAuth(), async (req, res) => {
  if (!requireDb(res)) return
  try {
    const { userId: clerkId, sessionClaims } = getAuth(req)
    const user = await resolveUser(clerkId, sessionClaims)
    const reports = await prisma.report.findMany({
      where: { userId: user.id },
      orderBy: { createdAt: 'desc' },
      select: { id: true, title: true, entity: true, stakeholder: true, horizon: true, createdAt: true },
    })
    res.json(reports)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// GET /api/reports/:id
router.get('/:id', requireAuth(), async (req, res) => {
  if (!requireDb(res)) return
  try {
    const { userId: clerkId, sessionClaims } = getAuth(req)
    const user = await resolveUser(clerkId, sessionClaims)
    const report = await prisma.report.findFirst({
      where: { id: req.params.id, userId: user.id },
    })
    if (!report) return res.status(404).json({ error: 'Report not found' })
    res.json(report)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// POST /api/reports
router.post('/', requireAuth(), async (req, res) => {
  if (!requireDb(res)) return
  try {
    const { userId: clerkId, sessionClaims } = getAuth(req)
    const { title, entity, industry, stakeholder, horizon, data } = req.body
    if (!entity || !stakeholder || !data) {
      return res.status(400).json({ error: 'entity, stakeholder, and data are required' })
    }
    const user = await resolveUser(clerkId, sessionClaims)
    const report = await prisma.report.create({
      data: {
        userId: user.id,
        title: title || `${entity} — ${stakeholder}`,
        entity,
        industry: industry ?? null,
        stakeholder,
        horizon: horizon ?? null,
        data,
      },
    })
    await prisma.auditLog.create({
      data: { userId: user.id, action: 'CREATE', resource: `report:${report.id}` },
    })
    res.status(201).json(report)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// DELETE /api/reports/:id
router.delete('/:id', requireAuth(), async (req, res) => {
  if (!requireDb(res)) return
  try {
    const { userId: clerkId, sessionClaims } = getAuth(req)
    const user = await resolveUser(clerkId, sessionClaims)
    const report = await prisma.report.findFirst({
      where: { id: req.params.id, userId: user.id },
    })
    if (!report) return res.status(404).json({ error: 'Report not found' })
    await prisma.report.delete({ where: { id: req.params.id } })
    await prisma.auditLog.create({
      data: { userId: user.id, action: 'DELETE', resource: `report:${req.params.id}` },
    })
    res.json({ deleted: true })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

export default router
