import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import AutoImport from 'unplugin-auto-import/vite'

export default defineConfig({
  plugins: [
    react(),
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
  ],
})
