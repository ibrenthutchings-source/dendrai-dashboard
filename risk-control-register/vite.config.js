import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import AutoImport from 'unplugin-auto-import/vite'

const API = 'http://127.0.0.1:8001'
const proxy = {
  '/api/risk-register': { target: API, changeOrigin: true, rewrite: p => p.replace(/^\/api\/risk-register/, '/risk-register') },
  '/api/pac':           { target: API, changeOrigin: true, rewrite: p => p.replace(/^\/api\/pac/, '/pac') },
  '/auth/':             { target: API, changeOrigin: true },
}

export default defineConfig({
  plugins: [
    react({ jsxRuntime: 'classic' }),
    AutoImport({
      imports: [{ react: [['default', 'React'], 'useState', 'useEffect', 'useRef', 'useMemo', 'useCallback', 'useLayoutEffect', 'useContext', 'useReducer'] }],
      dts: false,
    }),
  ],
  server: { proxy },
  preview: { proxy },
})
