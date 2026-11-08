import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // Follow-up sends real Gmail via Browserbase and can take a while.
        timeout: 180_000,
        proxyTimeout: 180_000,
      },
    },
  },
})
