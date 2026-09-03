import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import AutoImport from 'unplugin-auto-import/vite'

// Per-host User-Agent overrides — mirrors api_server.py's _rss_user_agent()
// for the production path. SEC.gov requires a declared company + contact UA
// (fair-access policy) or it serves an HTML block page instead of the feed;
// federalregister.gov (EPA Climate Enforcement) is the opposite — its bot
// protection blocks non-browser UAs, including the identified one, redirecting
// to an "unblock.federalregister.gov" HTML challenge page with a 200 status
// that looks like success until the RSS parser chokes on it.
const RSS_BROWSER_UA_HOSTS = ['federalregister.gov'];
function rssUserAgent(feedUrl) {
  try {
    const host = new URL(feedUrl).hostname;
    if (RSS_BROWSER_UA_HOSTS.some(h => host === h || host.endsWith('.' + h))) {
      return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
    }
  } catch {}
  return 'Dendrai Intelligenza research@dendrai.ai';
}

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
            'User-Agent': rssUserAgent(feedUrl),
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
  build: {
    // NOTE on history: the post-login "Cannot access '<var>' before
    // initialization" crash was chased through two manualChunks attempts
    // (recharts-only vendor chunk, then all-of-node_modules vendor chunk)
    // and even a codeSplitting:false single-file build — none of it helped,
    // because the bug was never in the build config. Decoding a production
    // sourcemap traced the throw site straight to app.jsx: a `useState`
    // declaration (`manualAudits`) sat ~100 lines below a `useEffect` whose
    // dependency array already referenced it — a plain same-render TDZ
    // ReferenceError, evaluated fresh on every render regardless of how the
    // output was chunked. Fixed by moving that declaration above the
    // effect (see app.jsx). Build config is back to defaults.
  },
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
      // Auth endpoints — keep /auth/ prefix; FastAPI router is prefixed "/auth".
      // Trailing slash is required: a bare '/auth' prefix also matches the
      // /auth.jsx source module (import '../auth.jsx' in src/main.jsx), which
      // Vite must serve itself — proxying it to the backend causes a 401 and
      // breaks AuthProvider/useAuth for the whole app.
      '/auth/': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      // Approval workflow endpoints — session-cookie authenticated like /auth/,
      // not API-key-gated like /api/mcp/. Trailing slash for the same reason
      // as /auth/ above (avoid matching a same-named source module if one is
      // ever added).
      '/approvals/': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
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
      // Policy-as-Code / Controls-as-Code (pac_endpoints.py + pac_policy_docs.py).
      // The rewrite is REQUIRED and its absence is not a no-op: pac_endpoints
      // declares prefix="/pac" and api_server.py registers it with no extra
      // prefix, because production nginx's `location /api/ { proxy_pass
      // http://127.0.0.1:8001/; }` already strips "/api/" (the trailing slash
      // on proxy_pass is what does it). This entry used to pass the path
      // through verbatim on the now-stale assumption that the backend served
      // /api/pac/... itself — it doesn't, so every /api/pac/* call 404'd in
      // dev while working fine in production. The visible symptom was an
      // empty Rego Editor: loadModule() treats a non-ok response as "no data"
      // and leaves its initial "" in place.
      '/api/pac': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/pac/, '/pac'),
      },
      // Same gap as /api/pac above, found the same way: api_server.py
      // registers these directly on `app` with no "/api" prefix (production
      // nginx's /api/ catch-all strips it before forwarding, so only dev was
      // ever broken). Confirmed against the real OpenAPI schema — /config,
      // /digests, and /history are real routes with zero dev-proxy entry, so
      // every /api/config, /api/digests, /api/history call 404'd. /digests
      // is what surfaced this: the digest-notification poll in app.jsx
      // (pollDigests) hit /api/digests/check-due every 30s and 404'd every time.
      '/api/config': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/config/, '/config'),
      },
      '/api/digests': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/digests/, '/digests'),
      },
      '/api/history': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/history/, '/history'),
      },
      // evidence_pack_endpoints.router declares prefix="/evidence-pack" —
      // same gap, different router.
      '/api/evidence-pack': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/evidence-pack/, '/evidence-pack'),
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
      // Same rewrite as the dev block above — see the comment there for why
      // it is required rather than cosmetic.
      '/api/pac': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/pac/, '/pac'),
      },
      '/api/config': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/config/, '/config'),
      },
      '/api/digests': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/digests/, '/digests'),
      },
      '/api/history': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/history/, '/history'),
      },
      '/api/evidence-pack': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/evidence-pack/, '/evidence-pack'),
      },
      '/auth/': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      // Approval workflow endpoints — session-cookie authenticated like /auth/,
      // not API-key-gated like /api/mcp/. Trailing slash for the same reason
      // as /auth/ above (avoid matching a same-named source module if one is
      // ever added).
      '/approvals/': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
