import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  build: {
    // Never inline fonts as data: URIs (some @fontsource subsets are under the
    // 4 KB default): the production CSP allows font-src 'self' only.
    assetsInlineLimit: (filePath) => (/\.woff2?$/.test(filePath) ? false : undefined),
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // The live interview is a WebSocket under /api (/api/realtime/stream).
        ws: true,
      },
    },
  },
})
