import path from 'path'
import { fileURLToPath } from 'url'
import express from 'express'
import cors from 'cors'
import dotenv from 'dotenv'
import rateLimit from 'express-rate-limit'
import { clerkMiddleware } from '@clerk/express'
import reportsRouter from './routes/reports.js'

dotenv.config()

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const distPath = path.join(__dirname, '..', 'dist')

const app = express()
const port = process.env.PORT || 4000

app.use(cors())
app.use(express.json({ limit: '2mb' }))

// Attach Clerk auth context to every request (does not enforce auth by itself)
app.use(clerkMiddleware())

// Rate limiting: 120 requests per minute per IP on all API routes
const apiLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 120,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many requests, please try again shortly.' },
})
app.use('/api/', apiLimiter)

// Stricter limit on the Gemini proxy to control LLM costs
const geminiLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 20,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Synthesis rate limit reached. Wait a moment before generating another report.' },
})
app.use('/api/gemini', geminiLimiter)

app.get('/health', (req, res) => {
  res.json({ status: 'ok', environment: process.env.NODE_ENV || 'development' })
})

app.use('/api/reports', reportsRouter)

app.post('/api/gemini', async (req, res) => {
  const apiKey = process.env.GEMINI_API_KEY || process.env.VITE_GEMINI_API_KEY
  if (!apiKey) {
    return res.status(500).json({ error: 'Missing Gemini API key in server environment.' })
  }

  if (!req.body || Object.keys(req.body).length === 0) {
    return res.status(400).json({ error: 'Missing request body.' })
  }

  const geminiModel = process.env.GEMINI_MODEL || 'gemini-2.5-flash'
  const apiUrl = `https://generativelanguage.googleapis.com/v1/models/${geminiModel}:generateContent?key=${apiKey}`

  try {
    const upstream = await fetch(apiUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    })

    const text = await upstream.text()
    res.status(upstream.status)
    res.setHeader('Content-Type', 'application/json')
    res.send(text)
  } catch (error) {
    console.error('Gemini proxy error:', error)
    res.status(500).json({ error: 'Gemini proxy error', details: error.message || String(error) })
  }
})

if (process.env.NODE_ENV === 'production') {
  app.use(express.static(distPath))
  app.get('*', (req, res) => {
    res.sendFile(path.join(distPath, 'index.html'))
  })
}

app.listen(port, () => {
  console.log(`Dendrai backend listening on http://localhost:${port}`)
})
