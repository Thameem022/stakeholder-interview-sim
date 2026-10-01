import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
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
