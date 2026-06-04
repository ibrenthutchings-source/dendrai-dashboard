import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const geminiApiKey = env.VITE_GEMINI_API_KEY

  const handleGeminiProxy = async (req, res, next) => {
    if (req.url === '/probe-gemini') {
      res.statusCode = 200
      res.setHeader('Content-Type', 'text/plain')
      res.end('probe ok')
      return
    }

    if (req.url?.startsWith('/api/gemini')) {
      console.log('[gemini-proxy] request', req.method, req.url)
    }
    if (req.method !== 'POST' || !req.url?.startsWith('/api/gemini')) return next()

    if (!geminiApiKey) {
      res.statusCode = 500
      res.end('Missing Gemini API key in .env')
      return
    }

    try {
      let body = ''
      for await (const chunk of req) body += chunk

      const geminiModel = env.VITE_GEMINI_MODEL || 'gemini-2.5-flash'
      const apiUrl = `https://generativelanguage.googleapis.com/v1/models/${geminiModel}:generateContent?key=${geminiApiKey}`
      const upstream = await fetch(apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      })

      res.statusCode = upstream.status
      upstream.headers.forEach((value, name) => {
        if (name.toLowerCase() === 'content-length') return
        res.setHeader(name, value)
      })

      const text = await upstream.text()
      res.end(text)
    } catch (error) {
      res.statusCode = 500
      res.end(`Gemini proxy error: ${error.message}`)
    }
  }

  return {
    plugins: [
      react(),
      tailwindcss(),
      {
        name: 'vite:gemini-proxy',
        configureServer(server) {
          server.middlewares.use(handleGeminiProxy)
        },
        configurePreviewServer(server) {
          server.middlewares.use(handleGeminiProxy)
        }
      }
    ],
  }
})