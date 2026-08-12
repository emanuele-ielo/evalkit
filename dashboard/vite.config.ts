import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The bundle is served by evalkit's own FastAPI process, so `base` stays
// relative and the dev server proxies /api to it.
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  server: {
    port: 4748,
    proxy: { '/api': { target: 'http://127.0.0.1:4747', changeOrigin: true } },
  },
})
