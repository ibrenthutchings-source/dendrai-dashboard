import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import AutoImport from 'unplugin-auto-import/vite'

// Server-side RSS proxy — fetches feed XML from the origin server, bypassing
// browser CORS restrictions and rss2json.com's per-feed 422 failures.
const rssProxyPlugin = {
  name: 'rss-proxy',
  configureServer(server) {
    server.middlewares.use(async (req, res, next) => {
      if (!req.url.startsWith('/api/rss-proxy')) return next();
      const parsed = new URL(req.url, 'http://localhost');
      const feedUrl = parsed.searchParams.get('url');
      if (!feedUrl) { res.statusCode = 400; res.end('Missing url param'); return; }
      try {
        const upstream = await fetch(feedUrl, {
          headers: {
            'User-Agent': 'Mozilla/5.0 (compatible; DendraiBot/1.0; +https://dendrai.ai)',
            'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml, */*',
          },
          signal: AbortSignal.timeout(10000),
        });
        const text = await upstream.text();
        res.setHeader('Content-Type', upstream.headers.get('content-type') || 'application/xml');
        res.setHeader('Access-Control-Allow-Origin', '*');
        res.statusCode = upstream.status;
        res.end(text);
      } catch (e) {
        res.statusCode = 502;
        res.end(e.message);
      }
    });
  },
};

export default defineConfig({
  plugins: [
    react({ jsxRuntime: 'classic' }),
    AutoImport({
      imports: [
        {
          react: [
            ['default', 'React'],
            'useState',
            'useEffect',
            'useRef',
            'useMemo',
            'useCallback',
            'useLayoutEffect',
            'useContext',
            'useReducer',
            'useImperativeHandle',
            'useDebugValue'
          ]
        }
      ],
      dts: true,
      eslintrc: {
        enabled: false,
      },
    }),
    rssProxyPlugin,
  ],
  server: {
    proxy: {
      // Proxy SEC EDGAR API requests (data.sec.gov — companyfacts, submissions)
      '/api/edgar': {
        target: 'https://data.sec.gov',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/edgar/, ''),
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; DendraiBot/1.0; +https://dendrai.ai)',
          'Accept': 'application/json',
        },
      },
      // Proxy SEC www resources (www.sec.gov — company_tickers.json, etc.)
      '/api/sec': {
        target: 'https://www.sec.gov',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/sec/, ''),
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; DendraiBot/1.0; +https://dendrai.ai)',
          'Accept': 'application/json',
        },
      },
      // Proxy Python MCP API server (project/agentic-tools/api_server.py)
      // Start server first: python project/agentic-tools/api_server.py
      '/api/mcp': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/mcp/, ''),
      },
      // Risk Register Review API (reviews, framework search, convert-to-code, upload)
      '/api/risk-register': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/risk-register/, '/risk-register'),
      },
      // Risks-as-Code: OSCAL + COSO ERM generation + SSE live stream
      '/api/risks-as-code': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/risks-as-code/, '/risks-as-code'),
      },
    },
  },
  // vite preview (port 4173) needs its own proxy block — server.proxy is dev-only
  preview: {
    proxy: {
      '/api/edgar': {
        target: 'https://data.sec.gov',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/edgar/, ''),
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; DendraiBot/1.0; +https://dendrai.ai)',
          'Accept': 'application/json',
        },
      },
      '/api/sec': {
        target: 'https://www.sec.gov',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/sec/, ''),
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; DendraiBot/1.0; +https://dendrai.ai)',
          'Accept': 'application/json',
        },
      },
      '/api/mcp': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/mcp/, ''),
      },
      '/api/risk-register': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/risk-register/, '/risk-register'),
      },
      '/api/risks-as-code': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/risks-as-code/, '/risks-as-code'),
      },
    },
  },
})
