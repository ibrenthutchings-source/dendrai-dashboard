import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const geminiApiKey = env.VITE_GEMINI_API_KEY

  return {
    plugins: [
      react(),
      tailwindcss(),
      {
        name: 'vite:gemini-proxy',
        configureServer(server) {
          server.middlewares.use(async (req, res, next) => {
            if (req.method !== 'POST' || !req.url?.startsWith('/api/gemini')) return next()

            if (!geminiApiKey) {
              res.statusCode = 500
              res.end('Missing Gemini API key in .env')
              return
            }

            try {
              let body = ''
              for await (const chunk of req) body += chunk

              const apiUrl = `https://generativelanguage.googleapis.com/v1beta2/models/gemini-1.5-flash:generateContent?key=${geminiApiKey}`
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
          })
        }
      }
    ],
  }
})