import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The build lands in the Worker's asset directory rather than ./dist, so
// `wrangler deploy` from ../backend ships the compiled frontend as-is —
// wrangler.jsonc already points `assets.directory` at ./public.
const workerAssets = fileURLToPath(new URL('../backend/public', import.meta.url))

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // The compiler runs in a module worker; the build must emit ES workers to match.
  worker: { format: 'es' },
  build: {
    outDir: workerAssets,
    // outDir sits outside the Vite project root, so Vite refuses to clear it
    // unless told to. Without this, stale hashed bundles pile up in the
    // Worker's asset directory and get uploaded on every deploy.
    emptyOutDir: true,
  },
  server: {
    // `vite dev` serves the UI; anything the Worker owns is proxied to
    // `wrangler dev` so both halves run against the same origin locally.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: true,
      },
    },
  },
})
