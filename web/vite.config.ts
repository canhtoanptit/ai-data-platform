import react from '@vitejs/plugin-react'
// `vitest/config` re-exports Vite's defineConfig with the `test` block typed.
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // The app only ever calls relative `/api/...` URLs. In dev this proxy
      // forwards them to the API on the host; in the Docker image nginx does
      // the same job (see nginx.conf). Same code either way, no API base URL to
      // configure, and no CORS preflight — the browser sees one origin.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
